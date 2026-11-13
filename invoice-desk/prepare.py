"""Configure without placing database passwords in shell history. Standard library only."""
import argparse
import getpass
import os
import secrets
from pathlib import Path
from urllib.parse import quote


def write(path, values):
    if path.exists():
        raise SystemExit(f"Plik {path} już istnieje. Zachowano istniejącą konfigurację.")
    # Values generated below cannot contain newlines. Exclusive create never overwrites secrets.
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w") as out:
        for k,v in values.items():
            if '\n' in v or '\r' in v:
                raise ValueError("Konfiguracja nie może zawierać nowych linii")
            out.write(f"{k}={v}\n")


def main():
    parser=argparse.ArgumentParser();parser.add_argument("mode",choices=["demo","connect"]);args=parser.parse_args()
    root=Path(__file__).resolve().parent
    if args.mode=="demo":
        password=secrets.token_urlsafe(16)
        write(root/".env.demo",{"POSTGRES_PASSWORD":secrets.token_hex(24),"APP_SESSION_SECRET":secrets.token_hex(32),"DEMO_PASSWORD":password})
        print("Login: demo@example.test")
        print("Hasło:",password)
        print("Uruchom: docker compose --env-file .env.demo -f compose.demo.yml up --build -d")
        print("Adres: http://localhost:8000")
    else:
        host=input("Host PostgreSQL [host.docker.internal]: ").strip() or "host.docker.internal"
        port=input("Port [5432]: ").strip() or "5432"
        database=input("Nazwa istniejącej bazy V5: ").strip()
        username=input("Użytkownik PostgreSQL: ").strip()
        password=getpass.getpass("Hasło PostgreSQL: ")
        sslmode=input("SSL (verify-full/require/disable) [require]: ").strip() or "require"
        if not port.isdigit() or not 1<=int(port)<=65535 or not host or any(c in host for c in '/?#@ \r\n') or not database or not username or sslmode not in ("verify-full","require","disable"):
            raise SystemExit("Nieprawidłowe parametry połączenia.")
        if ':' in host and not host.startswith('['):host='['+host+']'
        url=f"postgresql://{quote(username,safe='')}:{quote(password,safe='')}@{host}:{port}/{quote(database,safe='')}?sslmode={sslmode}"
        write(root/".env",{"DATABASE_URL":url,"APP_SESSION_SECRET":secrets.token_hex(32),"COOKIE_SECURE":"0","OCR_LANGUAGES":"pol+nld+eng","APP_ORIGIN":"http://localhost:8001"})
        print("Zapisano konfigurację istniejącej bazy. Nie wykonano migracji.")
        print("Uruchom: docker compose up --build -d")
        print("Utwórz konto: docker compose exec app python -m app.manage create-user")
        print("Adres: http://localhost:8001")


if __name__=="__main__":main()
