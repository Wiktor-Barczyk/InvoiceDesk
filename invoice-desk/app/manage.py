"""Explicit account creation and isolated demonstration seeding. No production migrations."""
import argparse
import getpass
import hashlib
import os
from pathlib import Path

from .demo_data import sample_invoice
from .db import Database, audit, insert, store_upload
from .extraction import extract, inspect_file
from .security import hash_password


def seed_demo(db, storage, password, languages="pol+nld+eng"):
    with db.connect() as c:
        if c.execute("SELECT current_database() AS name").fetchone()["name"] != "invoice_desk_demo":
            raise RuntimeError("Demo wymaga osobnej bazy o nazwie invoice_desk_demo.")
        if c.execute("SELECT count(*) AS n FROM core.clients").fetchone()["n"]:
            return
        staff = insert(c, "core.staff_users", {
            "email": "demo@example.test", "full_name": "Demo Accountant",
            "role": "accountant", "password": hash_password(password),
        })
        cl = insert(c, "core.clients", {
            "odoo_partner_id": 1, "company_name": "Example Construction B.V.", "legal_form": "bv",
        })
        audit(c, "core.clients", cl["id"], staff["id"], {"demo": True, "company_name": cl["company_name"]})
    raw = sample_invoice(); info=inspect_file(raw); sha=hashlib.sha256(raw).hexdigest()
    relative=f"{sha[:2]}/{sha}.pdf"; target=storage/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    ocr=extract(target,languages)
    with db.connect() as c:
        store_upload(c,cl["id"],staff["id"],sha,relative,"FV-2026-0924-demo.pdf",info,len(raw),ocr)


def main():
    parser=argparse.ArgumentParser(description="Utwórz konto pracownika w istniejącej bazie V5.")
    parser.add_argument("command",choices=["create-user", "migrate", "enable-mcp", "revoke-mcp", "grant-chief-accountant"])
    parser.add_argument("email",nargs="?")
    args=parser.parse_args()
    if args.command == "grant-chief-accountant":
        from dotenv import load_dotenv
        load_dotenv(".env", override=True, interpolate=False)
        email=(args.email or "").strip().lower()
        if "@" not in email:
            parser.error("Podaj dokładny adres e-mail istniejącego księgowego.")
        with Database(os.environ["DATABASE_URL"]).connect() as c:
            matches=c.execute("SELECT id,role FROM core.staff_users WHERE lower(email)=%s AND is_active FOR UPDATE",(email,)).fetchall()
            if len(matches)!=1 or matches[0]["role"] not in ("accountant","chief_accountant"):
                raise SystemExit("Adres nie wskazuje jednego aktywnego konta księgowego.")
            staff=matches[0]
            if staff["role"]=="accountant":
                c.execute("UPDATE core.staff_users SET role='chief_accountant' WHERE id=%s",(staff["id"],))
                audit(c,"core.staff_users",staff["id"],None,{"role":"chief_accountant"},{"role":"accountant"})
        print("Konto ma rolę głównego księgowego:",email)
        return
    if args.command in ('enable-mcp', 'revoke-mcp'):
        from .mcp_jobs import manage
        manage(Database(os.environ['DATABASE_URL']), args.command)
        return
    if args.command == "migrate":
        db=Database(os.environ["DATABASE_URL"])
        with db.connect() as c:
            if not c.execute("SELECT to_regclass('documents.accounting_documents') AS t").fetchone()['t']:
                raise SystemExit("Brak istniejącego schematu V5. Przerwano bez zmian.")
            c.execute("SELECT pg_advisory_xact_lock(726219340119)")
            for migration in sorted((Path(__file__).parent/'migrations').glob('*.sql')):
                c.execute(migration.read_text())
        print("Dodano/zweryfikowano tabele kolejki. Zachowano dotychczasowe dokumenty i faktury.")
        return
    email=input("E-mail: ").strip().lower(); name=input("Imię i nazwisko: ").strip()
    role=input("Rola (admin/chief_accountant/accountant/reviewer/read_only) [accountant]: ").strip() or "accountant"
    if role not in ("admin","chief_accountant","accountant","reviewer","read_only") or "@" not in email or not name:
        raise SystemExit("Nieprawidłowe dane konta.")
    password=getpass.getpass("Nowe hasło (minimum 12 znaków): ")
    if len(password)<12 or password!=getpass.getpass("Powtórz hasło: "):
        raise SystemExit("Hasła muszą być takie same i mieć minimum 12 znaków.")
    db=Database(os.environ["DATABASE_URL"])
    with db.connect() as c:
        if c.execute("SELECT id FROM core.staff_users WHERE lower(email)=%s",(email,)).fetchone():
            raise SystemExit("Konto już istnieje. Nie zmieniono jego hasła.")
        u=insert(c,"core.staff_users",{"email":email,"full_name":name,"role":role,"password":hash_password(password)})
        audit(c,"core.staff_users",u["id"],u["id"],{"email":email,"full_name":name,"role":role})
        print(f"Utworzono konto #{u['id']}: {email}")


if __name__=="__main__":main()
