"""Durable preparation queue. It stores proposals, never approved invoices."""
from .db import document, insert, jsonb, audit
from .domain import Problem

EDITABLE = ('received', 'to_book', 'needs_explanation', 'waiting_for_documents')
ELIGIBLE = """d.status IN ('received','to_book','needs_explanation','waiting_for_documents')
    AND d.document_type IN ('unknown','purchase_invoice','sales_invoice','receipt','other')
    AND EXISTS (SELECT 1 FROM core.clients cl WHERE cl.id=d.client_id AND cl.is_active)
    AND NOT EXISTS (SELECT 1 FROM accounting.invoice_source_documents s WHERE s.accounting_document_id=d.id AND s.document_role='source')
    AND EXISTS (SELECT 1 FROM documents.accounting_document_files f WHERE f.accounting_document_id=d.id)"""


def mode_from(env):
    if env.get('AI_PROVIDER') == 'mcp':
        return 'mcp'
    enabled = env.get('AI_ENABLED', '0') == '1'
    if enabled and not env.get('OPENAI_API_KEY', '').strip():
        raise RuntimeError('AI_ENABLED=1 wymaga OPENAI_API_KEY w konfiguracji serwera.')
    return 'ai' if enabled else 'ocr'


def require_schema(c):
    for name in ('documents.processing_jobs', 'documents.processing_worker'):
        if not c.execute('SELECT to_regclass(%s) AS t', (name,)).fetchone()['t']:
            raise RuntimeError('Brak tabel kolejki. Uruchom: python -m app.manage migrate')


def enqueue(c, mode, doc_id=None, client_id=None):
    # ON CONFLICT ensures polling, restarts and duplicate uploads do not repeat completed jobs.
    return c.execute(f"""INSERT INTO documents.processing_jobs (accounting_document_id,mode)
        SELECT d.id,%s FROM documents.accounting_documents d WHERE {ELIGIBLE}
        AND (%s::bigint IS NULL OR d.id=%s) AND (%s::bigint IS NULL OR d.client_id=%s)
        ORDER BY d.received_at,d.id
        ON CONFLICT (accounting_document_id) DO UPDATE SET mode=EXCLUDED.mode,status='queued',attempts=0,
        available_at=now(),updated_at=now(),error_message=NULL,needs_split=false
        WHERE processing_jobs.mode='ocr' AND EXCLUDED.mode IN ('ai','mcp') AND processing_jobs.status<>'processing'
        RETURNING accounting_document_id""", (mode, doc_id, doc_id, client_id, client_id)).fetchall()


def job(c, doc_id):
    return c.execute('SELECT * FROM documents.processing_jobs WHERE accounting_document_id=%s', (doc_id,)).fetchone()


def summary(c, client_id, mode, doc_ids=()):
    counts = c.execute("""SELECT j.status,count(*) AS n FROM documents.processing_jobs j
        JOIN documents.accounting_documents d ON d.id=j.accounting_document_id
        WHERE d.client_id=%s AND d.status<>'archived'
        AND NOT EXISTS (SELECT 1 FROM accounting.invoice_source_documents s WHERE s.accounting_document_id=d.id AND s.document_role='source')
        GROUP BY j.status""", (client_id,)).fetchall()
    worker = c.execute("""SELECT mode, heartbeat_at, heartbeat_at>now()-interval '90 seconds' AS online
        FROM documents.processing_worker WHERE id=1""").fetchone()
    jobs = c.execute("""SELECT j.accounting_document_id,j.status,j.mode FROM documents.processing_jobs j
        JOIN documents.accounting_documents d ON d.id=j.accounting_document_id
        WHERE d.client_id=%s AND d.id=ANY(%s::bigint[])""",(client_id,list(doc_ids))).fetchall() if doc_ids else []
    return {'enabled': True, 'mode': mode, 'counts': {r['status']: r['n'] for r in counts}, 'worker': worker,'jobs':jobs}


def retry(c, doc_id, mode, staff_id):
    state = document(c, doc_id, for_update=True)
    if state['invoice'] or state['document']['status'] not in EDITABLE:
        raise Problem('Można przygotować tylko dokument bez zapisanej faktury.', 409)
    enqueue(c, mode, doc_id=doc_id)
    updated = c.execute("""UPDATE documents.processing_jobs SET status='queued',mode=%s,attempts=0,
        available_at=now(),updated_at=now(),error_message=NULL,needs_split=false
        WHERE accounting_document_id=%s AND status IN ('failed','cancelled') RETURNING *""", (mode, doc_id)).fetchone()
    if updated:
        audit(c, 'documents.accounting_documents', doc_id, staff_id, {'preparation_retry': mode})
    return job(c, doc_id)


def recover(c):
    # Only called after obtaining the singleton session advisory lock.
    c.execute("""UPDATE documents.processing_jobs SET
        status=CASE WHEN attempts>=3 THEN 'failed' ELSE 'queued' END,
        available_at=now(),updated_at=now(),error_message='Przerwano odczyt. Zadanie wznowiono po restarcie.'
        WHERE status='processing' AND mode<>'mcp'""")


def retry_client(c, client_id, mode, staff_id):
    rows = c.execute(f"""UPDATE documents.processing_jobs j SET status='queued',mode=%s,attempts=0,
        available_at=now(),updated_at=now(),error_message=NULL,needs_split=false
        FROM documents.accounting_documents d WHERE d.id=j.accounting_document_id AND d.client_id=%s
        AND j.status IN ('failed','cancelled') AND {ELIGIBLE} RETURNING j.accounting_document_id""", (mode,client_id)).fetchall()
    if rows:
        audit(c,'core.clients',client_id,staff_id,{'preparation_retry_count':len(rows),'mode':mode})
    return {'queued':len(rows)}


def claim(c, mode):
    c.execute(f"""UPDATE documents.processing_jobs j SET status='cancelled',updated_at=now()
        WHERE mode<>'mcp' AND status IN ('queued','processing') AND NOT EXISTS
        (SELECT 1 FROM documents.accounting_documents d WHERE d.id=j.accounting_document_id AND {ELIGIBLE})""")
    return c.execute("""UPDATE documents.processing_jobs SET status='processing',started_at=now(),
        updated_at=now(),attempts=attempts+1,error_message=NULL
        WHERE accounting_document_id=(SELECT accounting_document_id FROM documents.processing_jobs
        WHERE status='queued' AND mode=%s AND available_at<=now() AND attempts<3
        ORDER BY available_at,accounting_document_id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *""", (mode,)).fetchone()


def complete(c, task, version, result):
    doc_id = task['accounting_document_id']
    state = document(c, doc_id, for_update=True)
    current = job(c, doc_id)
    if not current or current['status'] != 'processing' or current['attempts'] != task['attempts']:
        return False
    if state['invoice'] or state['document']['status'] not in EDITABLE or state['document']['updated_at'] != version:
        c.execute("UPDATE documents.processing_jobs SET status='cancelled',updated_at=now(),error_message='Dokument zmienił się podczas odczytu.' WHERE accounting_document_id=%s", (doc_id,))
        return False
    run = insert(c, 'documents.document_ocr_runs', {'accounting_document_id': doc_id,
        'ocr_engine': result['engine'], 'ocr_engine_version': result['version'],
        'raw_result': jsonb(result['raw']), 'extracted_fields': jsonb(result['fields']),
        'status': 'completed'})
    c.execute('UPDATE documents.accounting_documents SET updated_at=clock_timestamp() WHERE id=%s', (doc_id,))
    needs_split = bool(result['raw'].get('needs_split'))
    status = 'review' if needs_split or result['raw'].get('warnings') else 'ready'
    c.execute("""UPDATE documents.processing_jobs SET status=%s,needs_split=%s,finished_at=now(),
        updated_at=now(),error_message=NULL WHERE accounting_document_id=%s""", (status, needs_split, doc_id))
    audit(c, 'documents.document_ocr_runs', run['id'], None,
          {'accounting_document_id': doc_id, 'engine': result['engine'], 'status': status})
    return True


def fail(c, task, message, retryable=False, retry_after=0):
    retrying = retryable and task['attempts'] < 3
    seconds = min(3600, max(retry_after, 30 * 2 ** (task['attempts'] - 1)))
    c.execute("""UPDATE documents.processing_jobs SET status=%s,error_message=%s,updated_at=now(),
        available_at=now()+(%s * interval '1 second') WHERE accounting_document_id=%s AND status='processing'""",
        ('queued' if retrying else 'failed', message[:500], seconds, task['accounting_document_id']))
