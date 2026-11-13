import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .domain import Problem, normalized, validate


def serial(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    raise TypeError(type(value).__name__)


def jsonb(value):
    return Jsonb(value, dumps=lambda x: json.dumps(x, default=serial, ensure_ascii=False))


def insert(c, table, data):
    query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
        sql.Identifier(*table.split(".")), sql.SQL(", ").join(map(sql.Identifier, data)),
        sql.SQL(", ").join(sql.Placeholder() for _ in data))
    return c.execute(query, list(data.values())).fetchone()


def audit(c, table, record_id, staff_id, after, before=None):
    schema, name = table.split(".")
    insert(c, "audit.change_log", {"schema_name": schema, "table_name": name, "record_id": record_id,
                                 "action": "UPDATE" if before else "INSERT", "staff_user_id": staff_id,
                                 "before_data": jsonb(before) if before else None, "after_data": jsonb(after)})


def lock(c, key):
    identifier = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
    c.execute("SELECT pg_advisory_xact_lock(%s)", (identifier,))


def client(c, client_id):
    row = c.execute("SELECT * FROM core.clients WHERE id=%s AND is_active", (client_id,)).fetchone()
    if not row:
        raise Problem("Klient nie istnieje lub jest nieaktywny.", 404)
    return row


def references(c, client_id):
    person = client(c, client_id)
    registrations = c.execute("SELECT registration_number, valid_from, valid_to FROM core.client_tax_registrations WHERE client_id=%s AND registration_type='BTW' AND status='active'", (client_id,)).fetchall()
    refs = {
        "client": person, "registrations": registrations,
        "categories": c.execute("SELECT id, category_code, category_name FROM accounting.staff_booking_categories WHERE is_active ORDER BY category_name").fetchall(),
        "accounts": c.execute("SELECT id, account_code, account_name, account_type FROM accounting.accounts WHERE is_active AND allow_direct_posting AND (account_code,lower(regexp_replace(trim(account_name),'^[0-9]+[[:space:]]+',''))) NOT IN             (('130','debiteuren'),('140','crediteuren'),('150','te vorderen btw'),('151','te betalen btw')) ORDER BY account_code").fetchall(),
        "vat_codes": c.execute("SELECT id, vat_code, vat_name, vat_rate_percent, vat_direction, valid_from, valid_to FROM accounting.vat_codes WHERE is_active ORDER BY vat_code").fetchall(),
        "mappings": c.execute("SELECT DISTINCT ON (booking_category_id) booking_category_id, account_id, default_vat_code_id FROM accounting.booking_category_account_mappings WHERE is_active AND (client_id=%s OR client_id IS NULL) ORDER BY booking_category_id, client_id NULLS LAST, priority", (client_id,)).fetchall(),
        "counterparties": c.execute("SELECT id, name, vat_number, iban, is_customer, is_supplier FROM core.client_counterparties WHERE client_id=%s AND is_active ORDER BY name", (client_id,)).fetchall(),
    }

    from .booking import choices
    if c.execute("SELECT to_regclass('accounting.invoice_desk_account_catalog') AS catalog").fetchone()['catalog']:
        refs["booking_catalog"] = c.execute("SELECT account_id, invoice_type FROM accounting.invoice_desk_account_catalog").fetchall()
    refs["booking_choices"] = choices(refs)
    return refs


# A portal invoice has one owner, recorded in its import key. On split
# documents a shared/stray source link must not mark another crop as booked.
SPLIT_INVOICE_OWNER = """(
    COALESCE(d.source_reference,'') NOT LIKE 'invoice-desk:split:%%'
    OR COALESCE(inv.import_source_key,'') NOT LIKE 'invoice-desk:%%'
    OR split_part(inv.import_source_key,':',2)=d.id::text
)"""


def list_documents(c, client_id, offset=0, status="all", search="", background=False):
    client(c, client_id)
    if status not in ('all','pending','approved') or len(search)>200:
        raise Problem('Nieprawidłowy filtr dokumentów.')
    status_sql = "i.status IN ('approved','posted')" if status=='approved' else "(i.status IS NULL OR i.status IN ('draft','review')) AND d.status NOT IN ('rejected','booked','included_in_vat')" if status=='pending' else 'true'
    search_sql = " AND concat_ws(' ',d.description,f.original_filename,i.invoice_number,i.seller_name_snapshot,i.buyer_name_snapshot) ILIKE %s" if search else ''
    args = [client_id]
    if search:
        args.append('%'+search.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%')
    args.append(offset)
    job_fields = "j.status AS preparation_status,j.mode AS preparation_mode," if background else "NULL AS preparation_status,NULL AS preparation_mode,"
    job_join = "LEFT JOIN documents.processing_jobs j ON j.accounting_document_id=d.id" if background else ''
    return c.execute(f"""
        SELECT d.id, d.description, d.document_type, d.status, d.updated_at, d.received_at,
               f.original_filename, i.id AS invoice_id, i.invoice_number, i.status AS invoice_status,
               i.seller_name_snapshot, i.buyer_name_snapshot, i.gross_total, i.currency,
               {job_fields} count(*) OVER() AS total_count
        FROM documents.accounting_documents d
        LEFT JOIN LATERAL (SELECT af.original_filename FROM documents.accounting_document_files af
                         WHERE af.accounting_document_id=d.id ORDER BY (af.file_role='original') DESC, af.id LIMIT 1) f ON true
        LEFT JOIN LATERAL (SELECT inv.* FROM accounting.invoice_source_documents sd JOIN accounting.invoices inv ON inv.id=sd.invoice_id
                         WHERE sd.accounting_document_id=d.id AND sd.document_role='source'
                           AND inv.client_id=d.client_id AND {SPLIT_INVOICE_OWNER}
                           ORDER BY inv.id LIMIT 1) i ON true
        {job_join}
        WHERE d.client_id=%s AND d.status <> 'archived' AND {status_sql} {search_sql}
          AND d.document_type IN ('unknown','purchase_invoice','sales_invoice','receipt','other')
        ORDER BY d.received_at DESC, d.id DESC LIMIT 50 OFFSET %s
    """, args).fetchall()


def document(c, doc_id, for_update=False):
    d = c.execute("SELECT * FROM documents.accounting_documents WHERE id=%s" + (" FOR UPDATE" if for_update else ""), (doc_id,)).fetchone()
    if not d:
        raise Problem("Nie znaleziono dokumentu.", 404)
    files = c.execute("""SELECT f.*, af.original_filename, af.file_role
                         FROM documents.accounting_document_files af JOIN documents.stored_files f ON f.id=af.stored_file_id
                         WHERE af.accounting_document_id=%s ORDER BY (af.file_role='original') DESC, af.id""", (doc_id,)).fetchall()
    ocr = c.execute("SELECT * FROM documents.document_ocr_runs WHERE accounting_document_id=%s ORDER BY processed_at DESC, id DESC LIMIT 1", (doc_id,)).fetchone()
    invoices = c.execute(f"""SELECT inv.* FROM accounting.invoice_source_documents s
                             JOIN accounting.invoices inv ON inv.id=s.invoice_id
                             JOIN documents.accounting_documents d ON d.id=s.accounting_document_id
                             WHERE d.id=%s AND s.document_role='source'
                               AND inv.client_id=d.client_id AND {SPLIT_INVOICE_OWNER}""", (doc_id,)).fetchall()
    if len(invoices) > 1:
        raise Problem("Dokument jest powiązany z kilkoma fakturami. Ta wersja edytora obsługuje jedną fakturę na dokument.", 409)
    invoice = invoices[0] if invoices else None
    if invoice:
        invoice["lines"] = c.execute("""SELECT l.*,a.account_code,a.account_name,
            bc.category_name AS booking_category_name,vc.vat_code AS vat_code_name,
            vc.vat_name AS vat_display_name
            FROM accounting.invoice_lines l LEFT JOIN accounting.accounts a ON a.id=l.account_id
            LEFT JOIN accounting.staff_booking_categories bc ON bc.id=l.booking_category_id
            LEFT JOIN accounting.vat_codes vc ON vc.id=l.vat_code_id
            WHERE l.invoice_id=%s ORDER BY l.line_number""", (invoice["id"],)).fetchall()
        if invoice["general_ledger_entry_id"] is not None:
            entry_id = invoice["general_ledger_entry_id"]
            entry = c.execute("SELECT journal_code,entry_date FROM accounting.general_ledger_entries WHERE id=%s", (entry_id,)).fetchone()
            invoice["portal_posted"] = bool(entry and entry["journal_code"] == "IDESK")
            invoice["ledger_date"] = entry["entry_date"] if entry else None
            amounts = c.execute("""
                SELECT invoice_line_id,SUM(input_vat_amount) AS deductible
                FROM accounting.vat_transactions WHERE general_ledger_entry_id=%s
                GROUP BY invoice_line_id
            """, (entry_id,)).fetchall()
            amounts = {x["invoice_line_id"]:x["deductible"] for x in amounts}
            for line in invoice["lines"]:
                if line["id"] in amounts:
                    line["deductible_vat"] = amounts[line["id"]]
    return {"document": d, "files": files, "ocr": ocr, "invoice": invoice}


def store_upload(c, client_id, staff_id, sha, relative_path, filename, info, size, ocr):
    client(c, client_id)
    lock(c, f"file:{client_id}:{sha}")
    duplicate = c.execute("""SELECT d.id FROM documents.accounting_documents d
        JOIN documents.accounting_document_files af ON af.accounting_document_id=d.id
        JOIN documents.stored_files f ON f.id=af.stored_file_id WHERE d.client_id=%s AND f.sha256=%s
        ORDER BY d.id LIMIT 1""", (client_id, sha)).fetchone()
    if duplicate:
        return {"id": duplicate["id"], "duplicate": True}
    # Hash is global in V5; an existing file can belong to multiple clients.
    row = c.execute("""INSERT INTO documents.stored_files (sha256, storage_backend, storage_path, mime_type, file_size_bytes)
        VALUES (%s,'filesystem',%s,%s,%s) ON CONFLICT (sha256) DO NOTHING RETURNING *""", (sha, relative_path, info["mime"], size)).fetchone()
    if row:
        audit(c, "documents.stored_files", row["id"], staff_id, row)
    else:
        row = c.execute("SELECT * FROM documents.stored_files WHERE sha256=%s", (sha,)).fetchone()
    d = insert(c, "documents.accounting_documents", {"client_id": client_id, "document_type": "unknown", "source": "upload",
        "source_reference": filename, "description": filename, "status": "to_book", "created_by_staff_id": staff_id})
    link = insert(c, "documents.accounting_document_files", {"accounting_document_id": d["id"], "stored_file_id": row["id"], "original_filename": filename})
    if ocr is not None:
        run = insert(c, "documents.document_ocr_runs", {"accounting_document_id": d["id"], "ocr_engine": ocr["engine"],
            "ocr_engine_version": ocr["version"], "raw_result": jsonb(ocr["raw"]), "extracted_fields": jsonb(ocr["fields"]),
            "status": "completed" if any(p["text"].strip() for p in ocr["raw"]["pages"]) else "failed"})
    audit(c, "documents.accounting_documents", d["id"], staff_id, d)
    audit(c, "documents.accounting_document_files", link["id"], staff_id, link)
    if ocr is not None:
        audit(c, "documents.document_ocr_runs", run["id"], staff_id, {"accounting_document_id": d["id"], "engine": ocr["engine"], "status": run["status"]})
    return {"id": d["id"], "duplicate": False}


def approve(c, doc_id, payload, staff_id, *, correction_of=None):
    state = document(c, doc_id, for_update=True)
    d, old = state["document"], state["invoice"]
    if old and old["status"] == "approved":
        return {"id": old["id"], "status": "approved", "already_approved": True}
    if old and old["status"] not in ("draft", "review"):
        raise Problem("Ta faktura jest zaksięgowana lub anulowana. Nie można jej zmieniać.", 409)
    if d["status"] not in ("received", "to_book", "needs_explanation", "waiting_for_documents"):
        raise Problem("Status dokumentu nie pozwala na zatwierdzenie.", 409)
    if payload.get("version") != d["updated_at"].isoformat():
        raise Problem("Dokument zmienił się w innej sesji. Otwórz go ponownie przed zapisem.", 409)
    from .booking import normalize
    refs = references(c, d["client_id"])
    payload, _ = normalize(payload, refs)
    data = validate(payload)
    if d["client_id"] != data["client_id"]:
        raise Problem("Dokument należy do innego klienta.", 409)
    client(c, data["client_id"])
    if not state["files"]:
        raise Problem("Dokument nie ma załączonego pliku źródłowego.")
    # Prevent a separate application posting the draft while lines are replaced.
    if old:
        current = c.execute("SELECT status, updated_at, client_id FROM accounting.invoices WHERE id=%s FOR UPDATE", (old["id"],)).fetchone()
        if current["status"] not in ("draft", "review"):
            raise Problem("Faktura została już zmieniona przez inny proces.", 409)
        if current["client_id"] != data["client_id"] or payload.get("invoice_version") != current["updated_at"].isoformat():
            raise Problem("Dane faktury zmieniły się w bazie. Otwórz dokument ponownie przed zatwierdzeniem.", 409)
    period_lock = c.execute("""SELECT lock_type, locked_through_date FROM tax.accounting_period_locks
        WHERE client_id=%s AND ((lock_type IN ('VAT','FULL') AND locked_through_date >= %s)
        OR (lock_type IN ('YEAR','FULL') AND locked_through_date >= %s)) LIMIT 1""",
        (data["client_id"], data["invoice_date"], data["invoice_date"])).fetchone()
    if period_lock:
        raise Problem(f"Okres {period_lock['lock_type']} jest zamknięty do {period_lock['locked_through_date']}. Wymagana obsługa korekty.", 409)
    accounts = {x["id"]: x for x in refs["accounts"]}
    vat_codes = {x["id"]: x for x in refs["vat_codes"]}
    categories = {x["id"] for x in refs["categories"]}
    for line in data["lines"]:
        if (line["account_id"] is not None and line["account_id"] not in accounts) or line["vat_code_id"] not in vat_codes or (line["booking_category_id"] is not None and line["booking_category_id"] not in categories):
            raise Problem(f"Pozycja {line['line_number']}: wybierz aktywną kategorię / konto i kod VAT.")
        vat = vat_codes[line["vat_code_id"]]
        direction = "input" if data["invoice_type"] == "purchase" else "output"
        if vat["vat_direction"] not in (direction, "both", "none"):
            raise Problem(f"Pozycja {line['line_number']}: kod VAT nie pasuje do zakupu/sprzedaży.")
        if (vat["valid_from"] and data["invoice_date"] < vat["valid_from"]) or (vat["valid_to"] and data["invoice_date"] > vat["valid_to"]):
            raise Problem(f"Pozycja {line['line_number']}: kod VAT nie obowiązuje w wybranej dacie.")
    party_role = "seller" if data["invoice_type"] == "purchase" else "buyer"
    name = data[f"{party_role}_name_snapshot"]
    vat_number = data[f"{party_role}_vat_number_snapshot"]
    lock(c, f"invoice:{data['client_id']}:{data['invoice_type']}:{normalized(data['invoice_number'])}")
    # Compare names too: adding/removing a VAT number must not evade duplicate checks.
    if data["invoice_number"]:
        matches = c.execute("""SELECT id, seller_name_snapshot, buyer_name_snapshot, seller_vat_number_snapshot, buyer_vat_number_snapshot
            FROM accounting.invoices candidate WHERE client_id=%s AND invoice_type=%s AND status <> 'cancelled'
              AND NOT EXISTS (SELECT 1 FROM accounting.general_ledger_entries rev
                   WHERE rev.reverses_entry_id=candidate.general_ledger_entry_id AND rev.status='posted')
            AND invoice_kind=%s AND EXTRACT(YEAR FROM invoice_date)=%s
            AND regexp_replace(lower(invoice_number), '[^[:alnum:]]', '', 'g')=%s AND id<>%s""",
            (data["client_id"], data["invoice_type"], data["invoice_kind"], data["invoice_date"].year, normalized(data["invoice_number"]), old["id"] if old else 0)).fetchall()
        for existing in matches:
            if normalized(existing[f"{party_role}_name_snapshot"]) == normalized(name) or (vat_number and normalized(existing[f"{party_role}_vat_number_snapshot"]) == normalized(vat_number)):
                raise Problem(f"Duplikat: faktura tego kontrahenta o tym numerze jest już w bazie (ID {existing['id']}).", 409)
    cp_id = data["counterparty_id"]
    if cp_id:
        cp = c.execute("SELECT * FROM core.client_counterparties WHERE id=%s AND client_id=%s AND is_active", (cp_id, data["client_id"])).fetchone()
        if not cp:
            raise Problem("Wybrany kontrahent nie należy do klienta lub jest nieaktywny.")
        if (vat_number and cp["vat_number"] and normalized(vat_number) != normalized(cp["vat_number"])) or (not (vat_number and cp["vat_number"]) and normalized(cp["name"]) != normalized(name)):
            raise Problem("Dane kontrahenta na fakturze nie pasują do wybranej kartoteki. Popraw wybór.")
    else:
        lock(c, f"counterparty:{data['client_id']}")
        rows = c.execute("SELECT * FROM core.client_counterparties WHERE client_id=%s AND is_active", (data["client_id"],)).fetchall()
        exact = [x for x in rows if vat_number and normalized(x["vat_number"]) == normalized(vat_number)]
        if not exact:
            exact = [x for x in rows if normalized(x["name"]) == normalized(name) and not (vat_number and x["vat_number"] and normalized(x["vat_number"]) != normalized(vat_number))]
        if len(exact) > 1:
            raise Problem("Znaleziono kilka kartotek kontrahenta. Wybierz właściwą z listy.")
        if exact:
            cp_id = exact[0]["id"]
        else:
            cp = insert(c, "core.client_counterparties", {"client_id": data["client_id"], "name": name, "vat_number": vat_number,
                "iban": data["seller_iban_snapshot"] if party_role == "seller" else None,
                "is_supplier": party_role == "seller", "is_customer": party_role == "buyer"})
            cp_id = cp["id"]
            audit(c, "core.client_counterparties", cp_id, staff_id, cp)
    data["counterparty_id"] = cp_id
    # Legacy V5 compatibility only; the portal has one accounting date.
    data["vat_date"] = data["invoice_date"]
    lines = data.pop("lines")
    data["status"] = "approved"
    if old:
        assignment = sql.SQL(", ").join(sql.SQL("{}=%s").format(sql.Identifier(k)) for k in data)
        c.execute(sql.SQL("UPDATE accounting.invoices SET {} WHERE id=%s").format(assignment), [*data.values(), old["id"]])
        invoice_id = old["id"]
        c.execute("DELETE FROM accounting.invoice_lines WHERE invoice_id=%s", (invoice_id,))
    else:
        data.update({"created_by_staff_id": staff_id, "import_source_key": f"invoice-desk:{doc_id}:correction:{correction_of}" if correction_of else f"invoice-desk:{doc_id}"})
        invoice_id = insert(c, "accounting.invoices", data)["id"]
        insert(c, "accounting.invoice_source_documents", {"invoice_id": invoice_id, "accounting_document_id": doc_id, "document_role": "source"})
    for line in lines:
        inserted = insert(c, "accounting.invoice_lines", {"invoice_id": invoice_id, **line})
        audit(c, "accounting.invoice_lines", inserted["id"], staff_id, inserted)
    for check in ("DESK_HEADER_TOTALS", "DESK_LINE_TOTALS", "DESK_REFERENCES", "DESK_DUPLICATE", "DESK_HUMAN_REVIEW"):
        c.execute("""INSERT INTO accounting.invoice_validation_checks (invoice_id, check_code, check_status, check_source, details, checked_at, checked_by_staff_id)
            VALUES (%s,%s,'passed',%s,%s,now(),%s) ON CONFLICT (invoice_id,check_code) DO UPDATE SET check_status='passed', check_source=EXCLUDED.check_source,
            details=EXCLUDED.details, checked_at=now(), checked_by_staff_id=EXCLUDED.checked_by_staff_id""",
            (invoice_id, check, "manual" if check == "DESK_HUMAN_REVIEW" else "system", jsonb({"app": "invoice-desk", "version": "1.0", "note": "Kontrola importu. Nie jest weryfikacją prawa do odliczenia VAT ani VIES."}), staff_id))
    updated_doc = c.execute("""UPDATE documents.accounting_documents SET document_type=%s, status='to_book', updated_at=now() WHERE id=%s RETURNING *""",
                           ("purchase_invoice" if data["invoice_type"] == "purchase" else "sales_invoice", doc_id)).fetchone()
    audit(c, "documents.accounting_documents", doc_id, staff_id, updated_doc, d)
    audit(c, "accounting.invoices", invoice_id, staff_id, {**data, "lines": lines, "source_document_id": doc_id}, old)
    return {"id": invoice_id, "status": "approved", "already_approved": False}


class Database:
    def __init__(self, url):
        self.url = url

    def connect(self):
        return psycopg.connect(self.url, row_factory=dict_row, connect_timeout=8, prepare_threshold=None,
                              options="-c statement_timeout=15000 -c lock_timeout=8000", application_name="invoice-desk")
