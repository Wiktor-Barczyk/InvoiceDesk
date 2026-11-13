"""Configure preparation locally without printing secrets or changing DB settings."""
import argparse
import getpass
import os
import re
import tempfile
from pathlib import Path


def configure(path, values):
    if not path.is_file():
        raise SystemExit('Brak .env. Najpierw skonfiguruj połączenie z bazą: python3 prepare.py connect')
    lines=path.read_text(encoding='utf-8').splitlines()
    keys=set(values)
    kept=[line for line in lines if not re.match(r'^\s*(?:export\s+)?('+ '|'.join(keys) + r')\s*=',line)]
    text='\n'.join(kept).rstrip()+'\n'+''.join(f'{key}={value}\n' for key,value in values.items())
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,delete=False) as stream:
        tmp=Path(stream.name);stream.write(text);stream.flush();os.fsync(stream.fileno())
    try:
        os.chmod(tmp,0o600)
        os.replace(tmp,path)
    finally:
        if tmp.exists():tmp.unlink()


def main():
    parser=argparse.ArgumentParser(description='Włącz odczyt faktur w tle. Klucz pozostaje na serwerze.')
    parser.add_argument('--local-ocr',action='store_true',help='Tylko lokalny OCR, bez wysyłania do OpenAI.')
    args=parser.parse_args()
    values={'AI_PROVIDER':'openai','BACKGROUND_PROCESSING':'1','AI_ENABLED':'0' if args.local_ocr else '1'}
    if not args.local_ocr:
        key=getpass.getpass('Klucz API OpenAI (niewidoczny podczas wpisywania): ').strip()
        if not re.fullmatch(r'sk-[A-Za-z0-9_-]{16,1024}',key):
            raise SystemExit('Nieprawidłowy format klucza. Nie zmieniono konfiguracji.')
        values.update(OPENAI_API_KEY=key,OPENAI_MODEL='gpt-4.1-mini')
    configure(Path(__file__).resolve().parent/'.env',values)
    print('Zapisano ustawienia odczytu. Dane połączenia z bazą pozostały bez zmian.')
    print('Uruchom migrację i proces w tle według START-1.2.md.')


if __name__=='__main__':main()
