import hashlib
import hmac
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import date
from urllib.parse import urlsplit

import psycopg
from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import inventaris, ledger, posting, processing
from .storage import FileStore
from .db import Database, approve, audit, client, document, list_documents, references, serial, store_upload
from .domain import Problem, positive_id
from .extraction import extract, image_bytes, inspect_file
from .security import hash_password, read_session, sign_session, verify_password
from .splitting import (check_split, prepare_parts, save_split, split_context,
                        split_signature, undo_split, validate_regions)

ROOT = Path(__file__).resolve().parent


def create_app(config=None):
    env = dict(os.environ)
    env.update(config or {})
    url = env.get("DATABASE_URL", "")
    secret = env.get("APP_SESSION_SECRET", "")
    if not url or len(secret) < 32:
        raise RuntimeError("Ustaw DATABASE_URL i APP_SESSION_SECRET (minimum 32 znaki). Patrz README.md.")
    db = Database(url)
    storage = Path(env.get("DOCUMENT_STORAGE", "./data/files")).resolve()
    storage.mkdir(parents=True, exist_ok=True)
    extra_roots = [Path(x).resolve() for x in env.get("EXISTING_FILE_ROOTS", "").split(os.pathsep) if x]
    files = FileStore(storage, extra_roots)
    background = env.get("BACKGROUND_PROCESSING", "0") == "1"
    processing_mode = processing.mode_from(env)
    if processing_mode in ("ai", "mcp") and not background:
        raise RuntimeError("AI_ENABLED=1 wymaga BACKGROUND_PROCESSING=1.")
    demo = env.get("APP_DEMO") == "1"
    secure = env.get("COOKIE_SECURE", "0") == "1"
    languages = env.get("OCR_LANGUAGES", "pol+nld+eng")
    attempts, attempts_lock = {}, threading.Lock()
    dummy_hash = hash_password("unused-login-timing-dummy")

    @asynccontextmanager
    async def lifespan(app):
        with db.connect() as c:
            required = ["core.staff_users", "core.clients", "documents.stored_files", "documents.accounting_documents",
                        "documents.document_ocr_runs", "documents.document_imports", "documents.document_import_items",
                        "accounting.invoices", "accounting.invoice_lines", "audit.change_log"]
            for name in required:
                if not c.execute("SELECT to_regclass(%s) AS object", (name,)).fetchone()["object"]:
                    raise RuntimeError(f"Baza nie ma schematu V5: brak {name}. Aplikacja nie wykonuje migracji istniejącej bazy.")
            if background:
                processing.require_schema(c)
                if processing_mode == "mcp":
                    from .mcp_server import require_schema
                    require_schema(c)
        if demo and env.get("DEMO_SEED") == "1":
            from .manage import seed_demo
            seed_demo(db, storage, env["DEMO_PASSWORD"], languages)
        yield

    app = FastAPI(title="Invoice Desk", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.db = db

    @app.exception_handler(Problem)
    async def problem_handler(request, error):
        return JSONResponse({"detail": error.message}, status_code=error.status)

    @app.exception_handler(psycopg.Error)
    async def database_error(request, error):
        # Codes only: PostgreSQL exception text may contain document contents.
        logging.error("Database operation failed, SQLSTATE=%s", error.sqlstate)
        return JSONResponse({"detail": "Baza odrzuciła operację. Nie zatwierdzono zmian. Odśwież dokument; jeśli błąd powróci, skontaktuj się z administratorem."}, status_code=503)

    @app.middleware("http")
    async def boundaries(request, call_next):
        if request.url.path == "/api/documents/upload" and request.method == "POST":
            try:
                length = int(request.headers.get("content-length", "0"))
            except ValueError:
                length = 0
            if not 0 < length <= 26 * 1024 * 1024:
                return JSONResponse({"detail": "Przesyłka musi mieć określony rozmiar i mieścić się w limicie 25 MB pliku."}, status_code=413)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            allowed_origin = env.get("APP_ORIGIN") or str(request.base_url).rstrip("/")
            if origin and origin.rstrip("/") != allowed_origin.rstrip("/"):
                return JSONResponse({"detail": "Niedozwolone źródło żądania."}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "Niedozwolone źródło żądania."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "private, max-age=86400" if request.url.path.startswith("/static/") and request.url.query else "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        return response

    def user(request: Request):
        session = read_session(request.cookies.get("invoice_session", ""), secret)
        if not session:
            raise Problem("Zaloguj się, aby kontynuować.", 401)
        with db.connect() as c:
            staff = c.execute("SELECT id, full_name, email, role FROM core.staff_users WHERE id=%s AND is_active", (session["id"],)).fetchone()
        if not staff:
            raise Problem("Konto jest nieaktywne.", 401)
        if request.method not in ("GET", "HEAD") and not hmac.compare_digest(request.headers.get("x-csrf-token", ""), session["csrf"]):
            raise Problem("Sesja formularza wygasła. Odśwież stronę.", 403)
        return {**staff, "csrf": session["csrf"]}

    def writer(staff=Depends(user)):
        if staff["role"] not in ("admin", "chief_accountant", "accountant", "reviewer"):
            raise Problem("To konto ma dostęp tylko do odczytu.", 403)
        return staff

    source_path = files.resolve

    @app.post("/api/login")
    async def login(request: Request):
        raw = await request.body()
        if len(raw) > 8192:
            raise Problem("Nieprawidłowe dane logowania.")
        try:
            body = json.loads(raw)
            email, password = str(body.get("email", "")).strip().lower(), str(body.get("password", ""))
        except (ValueError, AttributeError):
            raise Problem("Nieprawidłowe dane logowania.")
        if not email or len(email) > 320 or not 1 <= len(password) <= 512:
            raise Problem("Podaj e-mail i hasło.")
        ip = request.client.host
        now = time.time()
        with attempts_lock:
            for key in list(attempts):
                attempts[key] = [stamp for stamp in attempts[key] if now - stamp < 900]
                if not attempts[key]:
                    del attempts[key]
            recent = attempts.setdefault(ip, [])
            if len(recent) >= 8:
                raise Problem("Zbyt wiele prób. Spróbuj ponownie za 15 minut.", 429)
            recent.append(now)
        def check_login():
            with db.connect() as c:
                account = c.execute("SELECT * FROM core.staff_users WHERE lower(email)=%s AND is_active", (email,)).fetchone()
                good = verify_password(password, account["password"] if account else dummy_hash)
                if not account or not good:
                    raise Problem("Nieprawidłowy e-mail lub hasło.", 401)
                c.execute("UPDATE core.staff_users SET last_login=now() WHERE id=%s", (account["id"],))
                c.execute("INSERT INTO audit.change_log (schema_name,table_name,record_id,action,staff_user_id) VALUES ('core','staff_users',%s,'LOGIN',%s)", (account["id"], account["id"]))
                return account["id"]
        account_id = await run_in_threadpool(check_login)
        with attempts_lock:
            attempts.pop(ip, None)
        response = JSONResponse({"ok": True})
        response.set_cookie("invoice_session", sign_session(account_id, secret), httponly=True, secure=secure, samesite="strict", max_age=28800, path="/")
        return response

    @app.post("/api/logout")
    def logout(staff=Depends(user)):
        response = JSONResponse({"ok": True})
        response.delete_cookie("invoice_session", path="/")
        return response

    @app.get("/api/me")
    def me(staff=Depends(user)):
        return {**staff, "demo": demo}

    def response(value):
        return Response(json.dumps(value, default=serial, ensure_ascii=False), media_type="application/json")

    @app.get("/api/clients")
    def clients(staff=Depends(user)):
        with db.connect() as c:
            return response(c.execute("SELECT id, company_name, kvk_number FROM core.clients WHERE is_active ORDER BY company_name").fetchall())

    @app.get("/api/references")
    def refs(client_id: int, staff=Depends(user)):
        with db.connect() as c:
            return response(references(c, client_id))

    @app.put("/api/booking-mappings/{category_id}")
    async def booking_mapping(category_id: int, request: Request, staff=Depends(writer)):
        from .booking import set_mapping
        from .mcp_auth import body
        try:
            data = json.loads(await body(request, 8192))
        except (ValueError, UnicodeError):
            raise Problem("Nieprawidłowe powiązanie kategorii.")
        if not isinstance(data, dict) or set(data) != {"client_id", "account_id", "expected_account_id"}:
            raise Problem("Nieprawidłowe dane powiązania.")
        client_id = positive_id(data['client_id'], 'Klient')
        account_id = positive_id(data['account_id'], 'Konto')
        expected = positive_id(data['expected_account_id'], 'Dotychczasowe konto', True)
        category_id = positive_id(category_id, 'Kategoria')
        def save_mapping():
            with db.connect() as c:
                return set_mapping(c, client_id, category_id, account_id, expected, staff['id'])
        return response(await run_in_threadpool(save_mapping))

    @app.get("/api/ledger")
    def ledger_entries(client_id: int, date_from: date, date_to: date, offset: int = 0, account_id: int | None = None, staff=Depends(user)):
        with db.connect() as c:
            return response(ledger.entries(c, client_id, date_from, date_to, offset, account_id))

    @app.get("/api/ledger/accounts")
    def ledger_account_balances(client_id: int, date_from: date, date_to: date, staff=Depends(user)):
        from .ledger_accounts import balances
        with db.connect() as c:
            return response(balances(c, client_id, date_from, date_to))


    @app.get("/api/ledger/{entry_id}")
    def ledger_lines(entry_id: int, client_id: int, staff=Depends(user)):
        with db.connect() as c:
            return response(ledger.lines(c, client_id, entry_id))

    @app.get("/api/ledger/{entry_id}/history")
    def ledger_history(entry_id: int, client_id: int, staff=Depends(user)):
        with db.connect() as c:
            return response(ledger.history(c, client_id, entry_id))

    @app.get("/api/inventaris")
    def inventaris_list(client_id: int, summary: bool = False, offset: int = 0, staff=Depends(user)):
        if not 0 <= offset <= 100000:
            raise Problem("Nieprawidłowa strona listy.")
        with db.connect() as c:
            return response(inventaris.inventory(c, client_id, summary, offset))

    @app.get("/api/inventaris/{line_id}")
    def inventaris_details(line_id: int, client_id: int, staff=Depends(user)):
        with db.connect() as c:
            return response(inventaris.details(c, client_id, line_id))

    @app.put("/api/inventaris/{line_id}")
    async def inventaris_save(line_id: int, client_id: int, request: Request, staff=Depends(writer)):
        from .mcp_auth import body
        try:
            data = json.loads(await body(request, 8192))
        except (ValueError, UnicodeError):
            raise Problem("Niepoprawny formularz amortyzacji.")
        def save():
            with db.connect() as c:
                return inventaris.save(c, client_id, line_id, data, staff["id"])
        return response(await run_in_threadpool(save))

    @app.get("/api/documents")
    def documents(client_id: int, offset: int = 0, status: str = "all", search: str = "", staff=Depends(user)):
        if offset < 0:
            raise Problem("Nieprawidłowy numer strony.")
        with db.connect() as c:
            return response(list_documents(c, client_id, offset, status, search, background))

    @app.get("/api/documents/{doc_id}")
    def get_document(doc_id: int, staff=Depends(user)):
        with db.connect() as c:
            state = document(c, doc_id)
            state["split"] = split_context(c, doc_id)
            state["preparation"] = processing.job(c, doc_id) if background else None
            state["background"] = background
            # A separate VAT date is no longer part of the portal's API.
            if state['invoice']:
                state['invoice'].pop('vat_date', None)
            if state['ocr']:
                state['ocr']['extracted_fields'].pop('vat_date', None)
            # Do not send paths or storage credentials to the browser.
            state["files"] = [{k: f[k] for k in ("id", "mime_type", "original_filename", "file_role")} for f in state["files"]]
            return response(state)

    @app.get("/api/documents/{doc_id}/file")
    def get_file(doc_id: int, file_id: int | None = None, staff=Depends(user)):
        with db.connect() as c:
            path, row = source_path(c, doc_id, file_id)
            return FileResponse(path, media_type=row["mime_type"], filename=Path(row["original_filename"]).name)

    @app.get("/api/documents/{doc_id}/pages")
    def page_info(doc_id: int, file_id: int | None = None, staff=Depends(user)):
        with db.connect() as c:
            path, _ = source_path(c, doc_id, file_id)
            return files.info(path)

    @app.get("/api/documents/{doc_id}/preview/{page}")
    def preview(doc_id: int, page: int, file_id: int | None = None, staff=Depends(user)):
        with db.connect() as c:
            path, _ = source_path(c, doc_id, file_id)
            return Response(files.preview(path, page), media_type="image/png")

    @app.get("/api/processing")
    def processing_summary(client_id: int, doc_ids: str = "", staff=Depends(user)):
        if not background:
            return {"enabled": False, "mode": "ocr", "counts": {}, "worker": None}
        with db.connect() as c:
            client(c, client_id)
            if len(doc_ids)>5000 or (doc_ids and len(doc_ids.split(','))>200):
                raise Problem("Zbyt dużo identyfikatorów dokumentów.")
            ids = [positive_id(x,"Dokument") for x in doc_ids.split(',')] if doc_ids else []
            return response(processing.summary(c, client_id, processing_mode, ids))

    @app.get("/api/documents/{doc_id}/preparation")
    def document_preparation(doc_id: int, staff=Depends(user)):
        with db.connect() as c:
            d = c.execute("SELECT updated_at FROM documents.accounting_documents WHERE id=%s", (doc_id,)).fetchone()
            if not d:
                raise Problem("Nie znaleziono dokumentu.", 404)
            return response({"version": d["updated_at"], "job": processing.job(c,doc_id) if background else None})

    @app.post("/api/processing/retry")
    def retry_client_preparation(client_id: int, staff=Depends(writer)):
        if not background:
            raise Problem("Kolejka jest wyłączona.", 409)
        with db.connect() as c:
            client(c,client_id)
            return processing.retry_client(c,client_id,processing_mode,staff["id"])

    @app.post("/api/documents/{doc_id}/prepare")
    def prepare_document(doc_id: int, staff=Depends(writer)):
        if not background:
            raise Problem("Kolejka jest wyłączona. Administrator musi włączyć przetwarzanie w tle.", 409)
        with db.connect() as c:
            return response(processing.retry(c,doc_id,processing_mode,staff["id"]))

    @app.post("/api/documents/upload")
    def upload(client_id: int = Form(...), file: UploadFile = File(...), staff=Depends(writer)):
        with db.connect() as c:
            client(c, client_id)
        raw = file.file.read(25 * 1024 * 1024 + 1)
        if len(raw) > 25 * 1024 * 1024:
            raise Problem("Maksymalna wielkość faktury to 25 MB.")
        info = inspect_file(raw)
        sha = hashlib.sha256(raw).hexdigest()
        relative = f"{sha[:2]}/{sha}{info['extension']}"
        path = storage / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        # Content addressed, atomic replacement; identical concurrent uploads are safe.
        import tempfile
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
            temp.write(raw)
            temp.flush()
            os.fsync(temp.fileno())
            temp_name = temp.name
        os.replace(temp_name, path)
        ocr = None if background else extract(path, languages)
        filename = Path((file.filename or "faktura").replace("\\", "/")).name[:500]
        # Automatic split on upload
        with db.connect() as c:
            result = store_upload(c, client_id, staff["id"], sha, relative, filename, info, len(raw), ocr)
            version = document(c,result['id'])['document']['updated_at'].isoformat()
        # Commit the original before layout analysis; no OCR under database locks.
        from .auto_split import run
        from .splitting import active_batch, batch_children
        automatic = None
        if result['duplicate']:
            with db.connect() as c:
                batch = active_batch(c,result['id'])
                if batch:
                    automatic = {'status':'split','children':batch_children(c,batch['id'])}
        else:
            try:
                automatic = run(db,files,storage,result['id'],version,languages,background,processing_mode,staff['id'])
            except Exception:
                # The upload succeeded. Keep the source available if detection fails.
                logging.error('Automatic document split failed for document %s',result['id'])
                automatic = {'status':'manual','regions':[],'failed':True,
                             'message':'Plik zapisano. Automatyczny podział nie powiódł się; możesz ponowić go lub wybrać podział ręczny.'}
        if automatic and automatic['status']=='split':
            return {**result,'background':background,'split_children':automatic['children']}
        if background:
            with db.connect() as c:
                processing.enqueue(c,processing_mode,doc_id=result['id'])
        return {**result,'background':background,'auto_split':automatic}

    async def split_body(request):
        raw = await request.body()
        if len(raw) > 65536:
            raise Problem("Zbyt dużo danych podziału.")
        try:
            data = json.loads(raw)
        except ValueError:
            raise Problem("Niepoprawny formularz podziału.")
        if not isinstance(data, dict) or not isinstance(data.get("version"), str):
            raise Problem("Brak wersji dokumentu. Otwórz go ponownie.")
        return data

    @app.post("/api/documents/{doc_id}/auto-split")
    async def auto_split_document(doc_id: int, request: Request, staff=Depends(writer)):
        data = await split_body(request)
        from .auto_split import run
        return response(await run_in_threadpool(run,db,files,storage,doc_id,data['version'],
                                                languages,background,processing_mode,staff['id'],data.get('force') is True))


    @app.post("/api/documents/{doc_id}/split")
    async def split_document(doc_id: int, request: Request, staff=Depends(writer)):
        data = await split_body(request)
        file_id = positive_id(data.get("file_id"), "Plik źródłowy")
        def split():
            with db.connect() as c:
                path, row = source_path(c, doc_id, file_id)
                regions = validate_regions(data.get("regions"), inspect_file(path.read_bytes())["pages"])
                signature = split_signature(data["version"], file_id, regions)
                state, existing = check_split(c, doc_id, data["version"], file_id, signature)
                if existing:
                    return {"children": split_context(c, doc_id)["children"], "already_split": True}
            # Release DB locks during OCR; validate the version again at commit.
            parts = prepare_parts(path, regions, languages, background=background)
            if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
                raise Problem("Oryginał zmienił się podczas podziału. Nie zapisano wycinków.", 409)
            with db.connect() as c:
                result = save_split(c, doc_id, data["version"], file_id, signature, parts, storage, staff["id"])
                if background:
                    for child in result["children"]:
                        processing.enqueue(c, processing_mode, doc_id=child["id"])
                    c.execute("UPDATE documents.processing_jobs SET status='cancelled',updated_at=now() WHERE accounting_document_id=%s", (doc_id,))
                return result
        return response(await run_in_threadpool(split))

    @app.post("/api/documents/{doc_id}/unsplit")
    async def unsplit_document(doc_id: int, request: Request, staff=Depends(writer)):
        data = await split_body(request)
        batch_id = positive_id(data.get("batch_id"), "Podział")
        def undo():
            with db.connect() as c:
                return undo_split(c, doc_id, data["version"], batch_id, staff["id"])
        return response(await run_in_threadpool(undo))

    @app.post("/api/documents/{doc_id}/book")
    async def book_invoice(doc_id: int, request: Request, staff=Depends(writer)):
        from .mcp_auth import body
        try:
            payload = json.loads(await body(request, 1024 * 1024))
        except (ValueError, UnicodeError):
            raise Problem("Niepoprawny formularz księgowania.")
        def save():
            with db.connect() as c:
                return posting.book(c, doc_id, payload, staff)
        return response(await run_in_threadpool(save))

    @app.post("/api/documents/{doc_id}/approve")
    async def approve_invoice(doc_id: int, request: Request, staff=Depends(writer)):
        raw = await request.body()
        if len(raw) > 1024 * 1024:
            raise Problem("Formularz jest zbyt duży.")
        try:
            data = json.loads(raw)
        except ValueError:
            raise Problem("Niepoprawny formularz.")
        if not isinstance(data, dict):
            raise Problem("Niepoprawny formularz.")
        def save():
            with db.connect() as c:
                return approve(c, doc_id, data, staff["id"])
        return await run_in_threadpool(save)

    if processing_mode == "mcp":
        from .mcp_server import install_mcp
        install_mcp(app, db, files, env, writer)

    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    @app.get("/")
    def index():
        return FileResponse(ROOT / "static" / "index.html")

    return app
