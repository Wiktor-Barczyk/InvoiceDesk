"""Human-selected regions become separate documents; originals remain immutable.

V5 import batches/items record provenance and undo history. No schema migration.
"""
import hashlib
import io
import json
import math
import os
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

from .db import SPLIT_INVOICE_OWNER, audit, client, document, insert, jsonb
from .domain import Problem
from .extraction import extract, image_bytes

SPLIT_FORMAT = "invoice-desk-split-v1"
EDITABLE = ("received", "to_book", "needs_explanation", "waiting_for_documents")
MAX_PARTS = 20


def validate_regions(regions, pages):
    if not isinstance(regions, list) or not 2 <= len(regions) <= MAX_PARTS:
        raise Problem(f"Zaznacz od 2 do {MAX_PARTS} dokumentów.")
    result = []
    for index, r in enumerate(regions, 1):
        if not isinstance(r, dict):
            raise Problem("Nieprawidłowe zaznaczenie.")
        page = r.get("page")
        if type(page) is not int or not 1 <= page <= pages:
            raise Problem("Nieprawidłowy numer strony zaznaczenia.")
        box = r.get("box")
        if (not isinstance(box, list) or len(box) != 4 or
                any(type(n) not in (int, float) or not math.isfinite(n) for n in box)):
            raise Problem("Nieprawidłowe współrzędne zaznaczenia.")
        x0, y0, x1, y1 = box
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1) or min(x1-x0, y1-y0) < .01:
            raise Problem("Zaznaczenie jest za małe lub wychodzi poza dokument.")
        rotation = r.get("rotation", 0)
        if type(rotation) is not int or rotation not in (0, 90, 180, 270):
            raise Problem("Nieprawidłowy obrót wycinka.")
        for previous in result:
            if previous["page"] == page:
                a, b, c, d = previous["box"]
                overlap = max(0, min(c, x1)-max(a, x0)) * max(0, min(d, y1)-max(b, y0))
                # Touching borders are allowed, shared document content is not.
                if overlap > .001 * min((c-a)*(d-b), (x1-x0)*(y1-y0)):
                    raise Problem("Zaznaczenia nachodzą na siebie. Popraw je, aby nie ująć dokumentu dwa razy.")
        result.append({"page": page, "box": box, "rotation": rotation})
    return result


def crop_bytes(path, region):
    with path.open("rb") as stream:
        is_pdf = stream.read(5) == b"%PDF-"
    if is_pdf:
        source = Image.open(io.BytesIO(image_bytes(path, region["page"], 3, max_dimension=3500)))
    else:
        source = Image.open(path)
    with source:
        oriented = ImageOps.exif_transpose(source)
        w, h = oriented.size
        x0, y0, x1, y1 = region["box"]
        bounds = (math.floor(x0*w), math.floor(y0*h), math.ceil(x1*w), math.ceil(y1*h))
        if min(bounds[2]-bounds[0], bounds[3]-bounds[1]) < 32:
            raise Problem("Wycinek ma za małą rozdzielczość. Zaznacz większy obszar.")
        crop = oriented.crop(bounds).convert("RGB")
    crop = crop.rotate(-region["rotation"], expand=True)
    crop.thumbnail((3500, 3500))
    output = io.BytesIO()
    crop.save(output, "PNG")
    return output.getvalue()


def prepare_parts(path, regions, languages, background=False):
    """OCR outside the transaction, one crop at a time, no source mutation."""
    parts, total_bytes, hashes = [], 0, set()
    with tempfile.TemporaryDirectory(prefix="invoice-split-") as temp:
        for index, region in enumerate(regions, 1):
            raw = crop_bytes(path, region)
            total_bytes += len(raw)
            if len(raw) > 25 * 1024 * 1024 or total_bytes > 64 * 1024 * 1024:
                raise Problem("Wycinki są zbyt duże. Zmniejsz rozdzielczość pliku i spróbuj ponownie.")
            sha = hashlib.sha256(raw).hexdigest()
            if sha in hashes:
                raise Problem("Dwa wycinki mają identyczną zawartość. Sprawdź zaznaczenia, aby nie zapisać duplikatu.")
            hashes.add(sha)
            target = Path(temp) / "crop.png"
            target.write_bytes(raw)
            parts.append({"raw": raw, "sha": sha,
                          "region": region, "ocr": None if background else extract(target, languages), "position": index})
    return parts


def active_batch(c, doc_id):
    return c.execute("""SELECT * FROM documents.document_imports
        WHERE source_template_version=%s AND status='completed'
        AND import_details @> %s ORDER BY id DESC LIMIT 1""",
        (SPLIT_FORMAT, jsonb({"source_document_id": doc_id, "state": "active"}))).fetchone()


def batch_children(c, batch_id):
    return c.execute(f"""SELECT d.id,d.description,d.status,it.source_key,invoice.status AS invoice_status
        FROM documents.document_import_items it JOIN documents.accounting_documents d
          ON d.id=it.created_record_id
        LEFT JOIN LATERAL (
          SELECT inv.status FROM accounting.invoice_source_documents sd JOIN accounting.invoices inv ON inv.id=sd.invoice_id
          WHERE sd.accounting_document_id=d.id AND sd.document_role='source'
            AND inv.client_id=d.client_id AND {SPLIT_INVOICE_OWNER}
          ORDER BY inv.id LIMIT 1
        ) invoice ON true
        WHERE it.document_import_id=%s AND it.created_record_type='documents.accounting_documents'
        ORDER BY it.id""", (batch_id,)).fetchall()


def split_context(c, doc_id):
    batch = active_batch(c, doc_id)
    origin = False
    if not batch:
        batch = c.execute("""SELECT b.* FROM documents.document_import_items it
            JOIN documents.document_imports b ON b.id=it.document_import_id
            WHERE it.created_record_id=%s AND it.created_record_type='documents.accounting_documents'
              AND b.source_template_version=%s AND b.status='completed'
              AND b.import_details->>'state'='active' ORDER BY b.id DESC LIMIT 1""",
            (doc_id, SPLIT_FORMAT)).fetchone()
        origin = True
    if not batch:
        return None
    details = batch["import_details"]
    return {"batch_id": batch["id"], "source_document_id": details["source_document_id"],
            "is_child": origin, "children": batch_children(c, batch["id"])}


def check_split(c, doc_id, version, file_id, signature):
    state = document(c, doc_id, for_update=True)
    d = state["document"]
    existing = active_batch(c, doc_id)
    if existing:
        if existing["import_details"]["signature"] == signature:
            return state, existing
        raise Problem("Ten plik został już rozdzielony. Otwórz oryginał, aby zobaczyć podział.", 409)
    if d["updated_at"].isoformat() != version:
        raise Problem("Dokument zmienił się w innej sesji. Otwórz go ponownie.", 409)
    if state["invoice"] or d["status"] not in EDITABLE:
        raise Problem("Można rozdzielić tylko dokument bez zapisanej faktury, oczekujący na sprawdzenie.", 409)
    if split_context(c, doc_id):
        raise Problem("To wycinek. Wróć do oryginału i cofnij podział, aby poprawić zaznaczenia.", 409)
    originals = [f for f in state["files"] if f["file_role"] == "original"]
    if len(originals) != 1 or len(state["files"]) != 1 or originals[0]["id"] != file_id:
        raise Problem("Podział obsługuje dokument z jednym plikiem źródłowym. Otwórz jego oryginał.", 409)
    client(c, d["client_id"])
    return state, None


def split_signature(version, file_id, regions):
    return hashlib.sha256(json.dumps([version, file_id, regions], sort_keys=True).encode()).hexdigest()


def save_split(c, doc_id, version, file_id, signature, parts, storage, staff_id):
    state, existing = check_split(c, doc_id, version, file_id, signature)
    if existing:
        return {"children": batch_children(c, existing["id"]), "already_split": True}
    d, source = state["document"], state["files"][0]
    batch = insert(c, "documents.document_imports", {
        "client_id": d["client_id"], "import_type": "api", "stored_file_id": file_id,
        "original_filename": source["original_filename"], "file_hash": source["sha256"],
        "source_template_version": SPLIT_FORMAT, "created_by_staff_id": staff_id,
        "import_details": jsonb({"source_document_id": doc_id, "signature": signature,
                                "source_version": version, "original_status": d["status"], "state": "active"})})
    for part in parts:
        sha = part["sha"]
        relative = f"{sha[:2]}/{sha}.png"
        target = storage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        # On DB rollback an unreferenced content-addressed file may remain. Never
        # delete it here: another transaction may have linked the same hash.
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as tmp:
            temp_name = tmp.name
            try:
                tmp.write(part["raw"])
                tmp.flush()
                os.fsync(tmp.fileno())
            except BaseException:
                Path(temp_name).unlink(missing_ok=True)
                raise
        try:
            os.replace(temp_name, target)
        finally:
            Path(temp_name).unlink(missing_ok=True)
        stored = c.execute("""INSERT INTO documents.stored_files
            (sha256,storage_backend,storage_path,mime_type,file_size_bytes,original_file_id)
            VALUES (%s,'filesystem',%s,'image/png',%s,%s)
            ON CONFLICT (sha256) DO NOTHING RETURNING *""",
            (sha, relative, len(part["raw"]), file_id)).fetchone()
        if stored:
            audit(c, "documents.stored_files", stored["id"], staff_id, stored)
        else:
            stored = c.execute("SELECT * FROM documents.stored_files WHERE sha256=%s", (sha,)).fetchone()
        pos = part["position"]
        name = f"{Path(source['original_filename']).stem[:400]} — dokument {pos}.png"
        child = insert(c, "documents.accounting_documents", {
            "client_id": d["client_id"], "document_type": "unknown", "source": "api",
            "source_reference": f"invoice-desk:split:{doc_id}:{pos}", "description": name,
            "status": "to_book", "created_by_staff_id": staff_id, "received_at": d["received_at"]})
        audit(c, "documents.accounting_documents", child["id"], staff_id, child)
        link = insert(c, "documents.accounting_document_files", {
            "accounting_document_id": child["id"], "stored_file_id": stored["id"], "original_filename": name})
        audit(c, "documents.accounting_document_files", link["id"], staff_id, link)
        ocr = part["ocr"]
        if ocr is not None:
            run = insert(c, "documents.document_ocr_runs", {
                "accounting_document_id": child["id"], "ocr_engine": ocr["engine"], "ocr_engine_version": ocr["version"],
                "raw_result": jsonb(ocr["raw"]), "extracted_fields": jsonb(ocr["fields"]),
                "status": "completed" if any(p["text"].strip() for p in ocr["raw"]["pages"]) else "failed"})
            audit(c, "documents.document_ocr_runs", run["id"], staff_id, {"accounting_document_id": child["id"], "status": run["status"]})
        item = insert(c, "documents.document_import_items", {
            "document_import_id": batch["id"], "source_key": str(pos), "source_position": str(part["region"]["page"]),
            "imported_item_type": "document", "status": "imported", "created_record_type": "documents.accounting_documents",
            "created_record_id": child["id"], "raw_source_data": jsonb({**part["region"], "source_sha256": source["sha256"]})})
        audit(c, "documents.document_import_items", item["id"], staff_id, item)
    changed = c.execute("UPDATE documents.accounting_documents SET status='archived',updated_at=now() WHERE id=%s RETURNING *", (doc_id,)).fetchone()
    audit(c, "documents.accounting_documents", doc_id, staff_id, changed, d)
    batch = c.execute("UPDATE documents.document_imports SET status='completed',completed_at=now() WHERE id=%s RETURNING *", (batch["id"],)).fetchone()
    audit(c, "documents.document_imports", batch["id"], staff_id, batch)
    return {"children": batch_children(c, batch["id"]), "already_split": False}


def undo_split(c, doc_id, version, batch_id, staff_id):
    state = document(c, doc_id, for_update=True)
    d = state["document"]
    batch = active_batch(c, doc_id)
    if not batch or batch["id"] != batch_id or d["updated_at"].isoformat() != version:
        raise Problem("Podział zmienił się. Otwórz oryginał ponownie.", 409)
    if d["status"] != "archived" or state["invoice"]:
        raise Problem("Oryginał został zmieniony przez inny proces. Nie cofnięto podziału.", 409)
    children = batch_children(c, batch_id)
    ids = [ch["id"] for ch in children]
    rows = c.execute("SELECT * FROM documents.accounting_documents WHERE id=ANY(%s) ORDER BY id FOR UPDATE", (ids,)).fetchall()
    if any(ch["status"] not in EDITABLE for ch in rows):
        raise Problem("Jeden z wycinków został już przetworzony. Nie można cofnąć podziału.", 409)
    if c.execute("SELECT 1 FROM accounting.invoice_source_documents WHERE accounting_document_id=ANY(%s) LIMIT 1", (ids,)).fetchone():
        raise Problem("Co najmniej jeden wycinek ma zapisaną fakturę. Nie można cofnąć podziału.", 409)
    for ch in rows:
        changed = c.execute("UPDATE documents.accounting_documents SET status='archived',updated_at=now() WHERE id=%s RETURNING *", (ch["id"],)).fetchone()
        audit(c, "documents.accounting_documents", ch["id"], staff_id, changed, ch)
    changed = c.execute("UPDATE documents.accounting_documents SET status=%s,updated_at=now() WHERE id=%s RETURNING *", (batch["import_details"]["original_status"], doc_id)).fetchone()
    audit(c, "documents.accounting_documents", doc_id, staff_id, changed, d)
    details = {**batch["import_details"], "state": "undone", "undone_by_staff_id": staff_id}
    changed_batch = c.execute("UPDATE documents.document_imports SET import_details=%s WHERE id=%s RETURNING *", (jsonb(details), batch_id)).fetchone()
    audit(c, "documents.document_imports", batch_id, staff_id, changed_batch, batch)
    return {"id": doc_id, "undone": True}
