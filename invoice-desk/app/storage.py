"""Authenticated callers resolve originals; bounded caches avoid repeated hashing/rendering."""
import hashlib
import threading
from collections import OrderedDict
from pathlib import Path

from .domain import Problem
from .extraction import image_bytes, inspect_file


class FileStore:
    def __init__(self, storage, extra_roots=()):
        self.storage = Path(storage).resolve()
        self.roots = [self.storage, *map(lambda p: Path(p).resolve(), extra_roots)]
        self.verified, self.metadata, self.previews = OrderedDict(), OrderedDict(), OrderedDict()
        self.preview_size = 0
        self.mutex = threading.RLock()

    def fingerprint(self, path):
        s = path.stat()
        return (str(path), s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)

    def verified_file(self, path, sha):
        if not path.is_file() or path.stat().st_size > 25 * 1024 * 1024:
            return False
        key = (*self.fingerprint(path), sha)
        with self.mutex:
            if key in self.verified:
                self.verified.move_to_end(key)
                return True
        good = hashlib.sha256(path.read_bytes()).hexdigest() == sha
        if good and key[:-1] == self.fingerprint(path):
            with self.mutex:
                self.verified[key] = True
                while len(self.verified) > 256:
                    self.verified.popitem(last=False)
            return True
        return False

    def resolve(self, c, doc_id, file_id=None):
        rows = c.execute("""SELECT f.*, af.original_filename FROM documents.accounting_document_files af
            JOIN documents.stored_files f ON f.id=af.stored_file_id
            WHERE af.accounting_document_id=%s ORDER BY (af.file_role='original') DESC, af.id""", (doc_id,)).fetchall()
        row = next((x for x in rows if x['id'] == file_id), None) if file_id else (rows[0] if rows else None)
        if not row:
            raise Problem('Brak pliku źródłowego albo plik nie należy do dokumentu.', 404)
        ext = {'application/pdf': '.pdf', 'image/png': '.png', 'image/jpeg': '.jpg'}.get(row['mime_type'])
        if not ext:
            raise Problem('Nieobsługiwany format dokumentu.', 409)
        cached = (self.storage / row['sha256'][:2] / (row['sha256'] + ext)).resolve()
        if cached.is_relative_to(self.storage) and self.verified_file(cached, row['sha256']):
            return cached, row
        if row['storage_backend'] not in ('filesystem', 'nextcloud'):
            raise Problem('Plik wymaga lokalnego magazynu albo zamontowanego Nextcloud.', 409)
        path = Path(row['storage_path'])
        path = (self.storage / path).resolve() if not path.is_absolute() else path.resolve()
        if not any(path.is_relative_to(root) for root in self.roots):
            raise Problem('Administrator musi udostępnić katalog w EXISTING_FILE_ROOTS.', 409)
        if not path.is_file():
            raise Problem('Plik źródłowy jest niedostępny.', 404)
        if not self.verified_file(path, row['sha256']):
            raise Problem('Zawartość pliku różni się od oryginału (SHA256) albo przekracza 25 MB.', 409)
        return path, row

    def info(self, path):
        key = self.fingerprint(path)
        with self.mutex:
            if key in self.metadata:
                self.metadata.move_to_end(key)
                return self.metadata[key]
        value = inspect_file(path.read_bytes())
        with self.mutex:
            self.metadata[key] = value
            while len(self.metadata) > 256:
                self.metadata.popitem(last=False)
        return value

    def preview(self, path, page):
        key = (*self.fingerprint(path), page)
        # One render per cache miss; memory stays bounded even with parallel requests.
        with self.mutex:
            if key in self.previews:
                self.previews.move_to_end(key)
                return self.previews[key]
            value = image_bytes(path, page)
            if len(value) <= 12 * 1024 * 1024:
                self.previews[key] = value
                self.preview_size += len(value)
                while self.preview_size > 32 * 1024 * 1024:
                    self.preview_size -= len(self.previews.popitem(last=False)[1])
            return value
