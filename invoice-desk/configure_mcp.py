"""Configure MCP/OAuth locally. Existing database connection and API key are preserved."""
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit
from configure_ai import configure


def main():
    from dotenv import dotenv_values
    from app.db import Database
    root=Path(__file__).resolve().parent
    values=dotenv_values(root/'.env',interpolate=False)
    if not values.get('DATABASE_URL'):raise SystemExit('Brak DATABASE_URL w .env.')
    public=input('Publiczny adres HTTPS tego portalu księgowego (bez / na końcu): ').strip().rstrip('/')
    u=urlsplit(public)
    if u.scheme!='https' or not u.hostname or u.username or u.password or u.path or u.query or u.fragment:
        raise SystemExit('Wymagany adres HTTPS portalu bez ścieżki. Nie zmieniono ustawień.')
    email=input('E-mail istniejącego pracownika, który połączy ChatGPT: ').strip().lower()
    with Database(values['DATABASE_URL']).connect() as c:
        staff=c.execute("SELECT id,role FROM core.staff_users WHERE lower(email)=%s AND is_active",(email,)).fetchone()
    if not staff or staff['role'] not in ('admin','accountant','reviewer'):
        raise SystemExit('Brak aktywnego konta z uprawnieniami do zapisu. Nie zmieniono ustawień.')
    client=values.get('MCP_CLIENT_ID') or 'invoice-desk-'+secrets.token_hex(16)
    secret=values.get('MCP_CLIENT_SECRET') or secrets.token_urlsafe(48)
    backup=root/'.env.before-mcp'
    try:
        fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd,'wb') as out:out.write((root/'.env').read_bytes())
    configure(root/'.env',{'BACKGROUND_PROCESSING':'1','AI_ENABLED':'0','AI_PROVIDER':'mcp',
        'MCP_PUBLIC_URL':public,'APP_ORIGIN':public,'COOKIE_SECURE':'1',
        'MCP_CLIENT_ID':client,'MCP_CLIENT_SECRET':secret,'MCP_ALLOWED_STAFF_IDS':str(staff['id'])})
    print('\nZapisano tryb MCP. Ten tryb nie wywołuje OpenAI API.')
    print('Adres MCP:',public+'/mcp')
    print('OAuth Client ID:',client)
    print('OAuth Client Secret:',secret)
    print('Wklej powyższe dane wyłącznie do konfiguracji połączenia w ChatGPT. Nie wysyłaj ich w rozmowie.')
    print('Następnie: .venv/bin/python run.py migrate && .venv/bin/python run.py enable-mcp')


if __name__=='__main__':main()
