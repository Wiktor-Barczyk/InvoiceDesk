"""Read the client's general journal for the selected posting period."""

from .db import client
from .domain import Problem

SIZE = 30


def entries(c, client_id, date_from, date_to, offset, account_id=None):
    client(c, client_id)
    if date_from > date_to or not 0 <= offset <= 1_000_000:
        raise Problem('Sprawdź zakres dat i numer strony.')
    # Only hide pairs explicitly linked to an Invoice Desk replacement.
    scope = """e.client_id=%s AND e.entry_date BETWEEN %s AND %s
                  AND e.status IN ('posted','reversed')
                  AND NOT EXISTS (SELECT 1 FROM accounting.invoice_desk_corrections fix
                                  WHERE fix.reversal_entry_id=e.id)
                  AND NOT EXISTS (
                    SELECT 1 FROM accounting.invoices old
                    JOIN accounting.invoice_desk_corrections fix ON fix.previous_invoice_id=old.id
                    WHERE old.general_ledger_entry_id=e.id
                  )"""
    selected = None
    if account_id is not None:
        from .ledger_accounts import account_ids
        selected = account_ids(c, account_id)
        scope += " AND EXISTS (SELECT 1 FROM accounting.general_ledger_entry_lines chosen WHERE chosen.general_ledger_entry_id=e.id AND chosen.account_id=ANY(%s))"
    parameters = (client_id, date_from, date_to) + ((selected,) if selected is not None else ())
    amount_scope = " AND l.account_id=ANY(%s)" if selected is not None else ""
    amount_parameters = (selected,) if selected is not None else ()
    rows = c.execute(f"""
        WITH page AS (
          SELECT e.* FROM accounting.general_ledger_entries e
          WHERE {scope} ORDER BY e.entry_date DESC,e.id DESC LIMIT %s OFFSET %s
        )
        SELECT e.id,e.entry_date,e.entry_type,e.reference,e.description,e.status,e.reverses_entry_id,
            EXISTS(SELECT 1 FROM accounting.general_ledger_entries rev
                WHERE rev.reverses_entry_id=e.id AND rev.status='posted') AS is_reversed,
            i.id AS invoice_id,i.invoice_number,i.seller_name_snapshot,i.buyer_name_snapshot,
            i.invoice_type,doc.id AS document_id,
            EXISTS(SELECT 1 FROM accounting.invoice_desk_corrections fix
                   WHERE fix.replacement_invoice_id=i.id AND e.reverses_entry_id IS NULL) AS has_history,
            COALESCE(doc.document_role='source' AND e.reverses_entry_id IS NULL,false) AS current_document,
            a.debit,a.credit
        FROM page e
        LEFT JOIN accounting.invoices i
          ON i.general_ledger_entry_id=COALESCE(e.reverses_entry_id,e.id) AND i.client_id=e.client_id
        LEFT JOIN LATERAL (
          SELECT d.id,s.document_role FROM accounting.invoice_source_documents s
          JOIN documents.accounting_documents d ON d.id=s.accounting_document_id
          WHERE s.invoice_id=i.id AND s.document_role IN ('source','supporting')
            AND d.client_id=e.client_id AND d.status<>'archived'
            AND (COALESCE(d.source_reference,'') NOT LIKE 'invoice-desk:split:%%'
              OR COALESCE(i.import_source_key,'') NOT LIKE 'invoice-desk:%%'
              OR split_part(i.import_source_key,':',2)=d.id::text)
          ORDER BY (s.document_role='source') DESC,d.id LIMIT 1
        ) doc ON true
        LEFT JOIN LATERAL (
          SELECT COALESCE(SUM(debit_amount),0) AS debit,COALESCE(SUM(credit_amount),0) AS credit
          FROM accounting.general_ledger_entry_lines l WHERE l.general_ledger_entry_id=e.id {amount_scope}
        ) a ON true
        ORDER BY e.entry_date DESC,e.id DESC
    """, (*parameters,SIZE,offset,*amount_parameters)).fetchall()
    totals = c.execute(f"""
        SELECT COUNT(DISTINCT e.id) AS entries, COALESCE(SUM(l.debit_amount),0) AS debit,
               COALESCE(SUM(l.credit_amount),0) AS credit
        FROM accounting.general_ledger_entries e
        LEFT JOIN accounting.general_ledger_entry_lines l ON l.general_ledger_entry_id=e.id
        WHERE {scope} {amount_scope}
    """, (*parameters,*amount_parameters)).fetchone()
    # Balances are cumulative through date_to. Use the full journal so
    # payments, openings and dated reversals affect each account correctly.
    balances = c.execute("""
        SELECT COALESCE(SUM(l.debit_amount-l.credit_amount)
                        FILTER (WHERE a.account_code='130'),0) AS debiteuren,
               COALESCE(SUM(l.credit_amount-l.debit_amount)
                        FILTER (WHERE a.account_code='140'),0) AS crediteuren
        FROM accounting.general_ledger_entries e
        JOIN accounting.general_ledger_entry_lines l ON l.general_ledger_entry_id=e.id
        JOIN accounting.accounts a ON a.id=l.account_id
        WHERE e.client_id=%s AND e.entry_date<=%s AND e.status IN ('posted','reversed')
          AND a.account_code IN ('130','140')
    """, (client_id,date_to)).fetchone()
    return {'entries': rows, 'totals': totals, 'balances': balances, 'page_size': SIZE}


def lines(c, client_id, entry_id):
    client(c, client_id)
    if not c.execute("""SELECT id FROM accounting.general_ledger_entries
        WHERE id=%s AND client_id=%s AND status IN ('posted','reversed')""",
        (entry_id,client_id)).fetchone():
        raise Problem('Nie znaleziono zapisu w księdze tego klienta.',404)
    return c.execute("""
        SELECT l.line_number,a.account_code,a.account_name,l.description,l.debit_amount,l.credit_amount
        FROM accounting.general_ledger_entry_lines l JOIN accounting.accounts a ON a.id=l.account_id
        WHERE l.general_ledger_entry_id=%s ORDER BY l.line_number
    """, (entry_id,)).fetchall()


def history(c, client_id, entry_id):
    client(c, client_id)
    head = c.execute("""
        SELECT i.id FROM accounting.invoices i
        JOIN accounting.general_ledger_entries e ON e.id=i.general_ledger_entry_id
        WHERE e.id=%s AND i.client_id=%s AND e.client_id=i.client_id
          AND e.status IN ('posted','reversed')
    """, (entry_id,client_id)).fetchone()
    if not head:
        raise Problem('Nie znaleziono księgowania faktury tego klienta.',404)
    steps = c.execute("""
        WITH RECURSIVE chain(invoice_id, visited, depth) AS (
          SELECT %s::bigint,ARRAY[%s::bigint],0
          UNION ALL
          SELECT previous.id,chain.visited || previous.id,chain.depth+1
          FROM chain
          JOIN accounting.invoice_desk_corrections fix ON fix.replacement_invoice_id=chain.invoice_id
          JOIN accounting.invoices previous ON previous.id=fix.previous_invoice_id AND previous.client_id=%s
          WHERE NOT previous.id=ANY(chain.visited)
        )
        SELECT fix.reason,fix.created_at,old.invoice_number,
               original.id AS previous_entry_id,original.entry_date AS previous_date,
               reversal.id AS reversal_entry_id,reversal.entry_date AS reversal_date
        FROM chain
        JOIN accounting.invoice_desk_corrections fix ON fix.previous_invoice_id=chain.invoice_id
        JOIN accounting.invoices old ON old.id=chain.invoice_id
        JOIN accounting.general_ledger_entries original ON original.id=old.general_ledger_entry_id
          AND original.client_id=old.client_id
        JOIN accounting.general_ledger_entries reversal ON reversal.id=fix.reversal_entry_id
          AND reversal.client_id=old.client_id AND reversal.reverses_entry_id=original.id
        WHERE chain.depth>0
        ORDER BY chain.depth
    """, (head['id'],head['id'],client_id)).fetchall()
    ids = [step[key] for step in steps for key in ('previous_entry_id','reversal_entry_id')]
    by_entry = {entry: [] for entry in ids}
    if ids:
        for line in c.execute("""
            SELECT l.general_ledger_entry_id,a.account_code,a.account_name,l.description,l.debit_amount,l.credit_amount
            FROM accounting.general_ledger_entry_lines l JOIN accounting.accounts a ON a.id=l.account_id
            WHERE l.general_ledger_entry_id=ANY(%s) ORDER BY l.general_ledger_entry_id,l.line_number
        """, (ids,)).fetchall():
            by_entry[line.pop('general_ledger_entry_id')].append(line)
    for step in steps:
        step['previous_lines'] = by_entry[step['previous_entry_id']]
        step['reversal_lines'] = by_entry[step['reversal_entry_id']]
    return steps
