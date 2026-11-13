"""Run `python -m app.worker`. A session advisory lock allows only one worker per database."""
import logging
import os
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path

import psycopg

from . import processing
from .ai import AIError, extract_ai
from .db import Database, document, references
from .domain import Problem
from .extraction import extract
from .storage import FileStore

WORKER_LOCK = 726219340118


def process_one(db, store, env, task, heartbeat=lambda:None):
    doc_id = task['accounting_document_id']
    try:
        with db.connect() as c:
            state = document(c, doc_id)
            if state['invoice'] or state['document']['status'] not in processing.EDITABLE:
                c.execute("UPDATE documents.processing_jobs SET status='cancelled',updated_at=now() WHERE accounting_document_id=%s", (doc_id,))
                return
            version = state['document']['updated_at']
            refs = references(c, state['document']['client_id'])
            paths = [store.resolve(c,doc_id,f['id'])[0] for f in state['files']]
        fingerprints = [store.fingerprint(p) for p in paths]
        def read():
            if task['mode'] == 'ai':
                return extract_ai(paths, store, refs, env)
            if len(paths) != 1:
                raise AIError('Dokument ma kilka załączników. Sprawdź wszystkie ręcznie albo włącz AI.')
            return extract(paths[0], env.get('OCR_LANGUAGES','pol+nld+eng'))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(read)
            while True:
                try:
                    result = future.result(timeout=15)
                    break
                except FutureTimeout:
                    if future.done():
                        raise
                    heartbeat()
        if fingerprints != [store.fingerprint(p) for p in paths]:
            raise AIError('Plik zmienił się podczas odczytu. Nie zapisano propozycji.')
        # A lost lock/connection aborts before committing any result.
        heartbeat()
        with db.connect() as c:
            processing.complete(c,task,version,result)
    except AIError as exc:
        with db.connect() as c:
            processing.fail(c,task,str(exc),exc.retryable,exc.retry_after)
        # A provider rate limit applies to the whole queue, not just this invoice.
        return min(3600, max(exc.retry_after, 30 * 2 ** (task['attempts']-1))) if exc.retryable or exc.retry_after else 0
    except Problem as exc:
        with db.connect() as c:
            processing.fail(c,task,exc.message)
    except psycopg.Error:
        raise
    except Exception as exc:
        logging.error('Preparation failed document=%s type=%s',doc_id,type(exc).__name__)
        with db.connect() as c:
            processing.fail(c,task,'Odczyt nie powiódł się. Sprawdź plik i konfigurację serwera.')


def run(env=None, stop=None):
    env = dict(os.environ) if env is None else env
    if env.get('BACKGROUND_PROCESSING','0') != '1':
        raise RuntimeError('Ustaw BACKGROUND_PROCESSING=1, aby uruchomić kolejkę.')
    mode = processing.mode_from(env)
    if mode == 'mcp':
        raise RuntimeError('Tryb MCP obsługuje zadanie ChatGPT. Nie uruchamiaj app.worker.')
    db = Database(env['DATABASE_URL'])
    storage = Path(env.get('DOCUMENT_STORAGE','./data/files')).resolve()
    store = FileStore(storage, [p for p in env.get('EXISTING_FILE_ROOTS','').split(os.pathsep) if p])
    stop = stop or threading.Event()
    interval = max(5,min(300,int(env.get('PROCESSING_POLL_SECONDS','15'))))
    with db.connect() as c:
        processing.require_schema(c)
    while not stop.is_set():
        try:
            # Dedicated connection holds the lock across commits and network requests.
            with db.connect() as lock:
                lock.autocommit = True
                if not lock.execute('SELECT pg_try_advisory_lock(%s) AS ok',(WORKER_LOCK,)).fetchone()['ok']:
                    stop.wait(interval);continue
                def heartbeat():
                    lock.execute("""INSERT INTO documents.processing_worker (id,heartbeat_at,mode) VALUES (1,now(),%s)
                        ON CONFLICT(id) DO UPDATE SET heartbeat_at=now(),mode=EXCLUDED.mode""",(mode,))
                heartbeat()
                with db.connect() as c:
                    processing.recover(c)
                next_scan = 0
                while not stop.is_set():
                    heartbeat()
                    with db.connect() as c:
                        if time.monotonic() >= next_scan:
                            processing.enqueue(c,mode)
                            next_scan = time.monotonic() + interval
                        task = processing.claim(c,mode)
                    if task:
                        cooldown = process_one(db,store,env,task,heartbeat) or 0
                        # Pace requests even when a backlog exists. Next document follows automatically.
                        until = time.monotonic() + max(cooldown, max(1,min(60,int(env.get('PROCESSING_GAP_SECONDS','2')))))
                        while not stop.is_set() and time.monotonic() < until:
                            stop.wait(min(15, max(0,until-time.monotonic())))
                            if not stop.is_set():
                                heartbeat()
                    else:
                        until = time.monotonic() + interval
                        while not stop.is_set() and time.monotonic() < until:
                            stop.wait(min(15,max(0,until-time.monotonic())))
                            if not stop.is_set():
                                heartbeat()
        except psycopg.Error as exc:
            logging.error('Worker database connection interrupted SQLSTATE=%s',exc.sqlstate)
            stop.wait(interval)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    stop = threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):
        signal.signal(sig,lambda *_:stop.set())
    run(stop=stop)
