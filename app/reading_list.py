"""An account's saved books and their links to the local catalog."""
import contextlib
from pathlib import Path
import re
import time
import uuid
from xml.etree import ElementTree as ET
import zipfile

from app import core as c
from app.covers import member_path, xml
from app.recommendations import book_identity, snapshot as recommendations_snapshot


def isbn_key(value):
    value = re.sub(r'[^0-9X]', '', (value or '').upper().removeprefix('URN:ISBN:'))
    if re.fullmatch(r'[0-9]{9}[0-9X]', value):
        prefix = '978' + value[:9]
        check = sum(int(digit) * (1 if i % 2 == 0 else 3) for i, digit in enumerate(prefix))
        return prefix + str(-check % 10)
    return value if re.fullmatch(r'97[89][0-9]{10}', value) else ''


def fingerprint(book):
    return {'reading_key': book_identity(book) if book.get('title') else '',
            'reading_isbn': isbn_key(book.get('isbn'))}


def epub_metadata(path):
    try:
        with zipfile.ZipFile(path) as archive:
            if len(archive.infolist()) > 20000:
                return '', '', ''
            container = xml(archive, 'META-INF/container.xml')
            package = next(node.get('full-path') for node in container.iter()
                           if node.tag.rsplit('}', 1)[-1] == 'rootfile' and node.get('full-path'))
            root = xml(archive, member_path('', package))
        dc = '{http://purl.org/dc/elements/1.1/}'
        title = next((' '.join(node.itertext()).strip() for node in root.iter(dc + 'title')), '')
        author = ', '.join(' '.join(node.itertext()).strip() for node in root.iter(dc + 'creator'))
        isbn = next(filter(None, (isbn_key(''.join(node.itertext())) for node in root.iter(dc + 'identifier'))), '')
        return title[:500], author[:500], isbn
    except (OSError, ValueError, KeyError, StopIteration, ET.ParseError, zipfile.BadZipFile,
            RuntimeError, NotImplementedError):
        return '', '', ''


def index_epub(db, path, digest):
    if not db.execute('SELECT 1 FROM reading_file_metadata WHERE digest=?', (digest,)).fetchone():
        db.execute('INSERT INTO reading_file_metadata VALUES (?,?,?,?)', (digest, *epub_metadata(path)))


def index_existing_epubs():
    """Fill the metadata index for books already present when the app updates."""
    with contextlib.closing(c.connect()) as db:
        rows = db.execute('''SELECT f.* FROM files f WHERE f.available=1 AND f.deleted=0
            AND lower(substr(f.name,-5))='.epub' AND NOT EXISTS
            (SELECT 1 FROM reading_file_metadata m WHERE m.digest=f.digest)''').fetchall()
    indexed = 0
    for row in rows:
        try:
            path = c.safe_path(row)
            stat = path.stat()
            if (stat.st_size, stat.st_mtime) != (row['size'], row['mtime']):
                continue
            metadata = epub_metadata(path)
            after = path.stat()
            if (after.st_size, after.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
                continue
        except (OSError, ValueError):
            continue
        with c.transaction() as db:
            db.execute('INSERT OR IGNORE INTO reading_file_metadata VALUES (?,?,?,?)', (row['digest'], *metadata))
        indexed += 1
        if indexed >= 30:
            break


def title_of(filename):
    return re.sub(r'-[a-f0-9]{12}$', '', Path(filename).stem)


def entries(db):
    return [dict(row) for row in db.execute('SELECT * FROM reading_list ORDER BY created DESC,id')]


class Catalog:
    def __init__(self, db, files):
        self.files = {}
        self.metadata = {row['digest']: dict(row) for row in db.execute('SELECT * FROM reading_file_metadata')}
        self.by_digest, self.by_isbn, self.by_key, self.by_title = {}, {}, {}, {}
        for row in files:
            if not row['available']:
                continue
            self.files[row['id']] = row
            digest = row['digest']
            self.by_digest.setdefault(digest, row)
            details = self.metadata.get(digest, {})
            if details.get('isbn'):
                self.by_isbn.setdefault(details['isbn'], row)
            if details.get('title') and details.get('author'):
                self.by_key.setdefault(book_identity(details), row)
            name = book_identity({'title': title_of(row['name'])})
            self.by_title.setdefault(name, {})[digest] = row

    def match(self, entry, saved):
        row = self.by_digest.get(entry['file_digest'])
        if row is not None or not entry['url']:
            return row
        identity = fingerprint(entry)
        row = self.by_isbn.get(identity['reading_isbn']) or self.by_key.get(identity['reading_key'])
        if row is not None:
            return row
        title = book_identity({'title': entry['title']})
        files = self.by_title.get(identity['reading_key'], {})
        if len(files) == 1 and entry['author']:
            row = next(iter(files.values()))
            if not self.metadata.get(row['digest'], {}).get('author'):
                return row
        if any(other['url'] and other is not entry and book_identity({'title': other['title']}) == title
               for other in saved):
            return None
        files = self.by_title.get(title, {})
        if len(files) == 1:
            row = next(iter(files.values()))
            if not self.metadata.get(row['digest'], {}).get('author'):
                return row
        return None


def current_catalog(db, files=None):
    if files is None:
        files = db.execute('SELECT * FROM files WHERE deleted=0 ORDER BY created DESC,id')
    return Catalog(db, files)


def list_state(db, files):
    saved = entries(db)
    if not saved:
        return [], {}
    catalog = current_catalog(db, files)
    by_digest = {}
    for entry in saved:
        row = catalog.match(entry, saved)
        entry.update(fingerprint(entry), file_id=row['id'] if row is not None else None)
        if row is not None:
            by_digest.setdefault(row['digest'], entry['id'])
        entry.pop('file_digest')
    return saved, {file_id: by_digest[row['digest']] for file_id, row in catalog.files.items() if row['digest'] in by_digest}


def recommendation_matches(entry, book):
    saved, new = fingerprint(entry), fingerprint(book)
    return (entry['url'] == book['url']
            or bool(new['reading_isbn'] and saved['reading_isbn'] == new['reading_isbn'])
            or bool(new['reading_key'] and saved['reading_key'] == new['reading_key']))


def update(body):
    if len(body) != 1 or not isinstance(value := next(iter(body.values()), None), str):
        raise ValueError('Richiesta non valida.')
    action = next(iter(body))
    if action == 'remove_id':
        with c.transaction() as db:
            db.execute('DELETE FROM reading_list WHERE id=?', (value,))
        return
    if action == 'recommendation_url':
        book = next((book for book in recommendations_snapshot()['books'] if book['url'] == value), None)
        if book is None:
            raise ValueError('Libro non disponibile nei Consigliati. Aggiorna la pagina.')
    elif action != 'file_id':
        raise ValueError('Richiesta non valida.')
    with c.transaction() as db:
        saved = entries(db)
        catalog = current_catalog(db)
        if action == 'file_id':
            row = catalog.files.get(value)
            if row is None:
                raise ValueError('Libro non disponibile.')
            details = catalog.metadata.get(row['digest'], {})
            book = {'title': details.get('title') or title_of(row['name']), 'author': details.get('author', ''),
                    'isbn': details.get('isbn', ''), 'url': '', 'file_digest': row['digest']}
            for entry in saved:
                match = catalog.match(entry, saved)
                if match is not None and match['digest'] == row['digest']:
                    return
        else:
            for entry in saved:
                if recommendation_matches(entry, book):
                    db.execute('''UPDATE reading_list SET title=?,author=?,isbn=?,url=?,cover_url=? WHERE id=?''',
                               (book['title'], book['author'], book['isbn'], book['url'], book['cover_url'], entry['id']))
                    return
            prospective = {**book, 'file_digest': None}
            match = catalog.match(prospective, [*saved, prospective])
            if match is not None:
                for entry in saved:
                    if not entry['url'] and entry['file_digest'] == match['digest']:
                        db.execute('''UPDATE reading_list SET title=?,author=?,isbn=?,url=?,cover_url=? WHERE id=?''',
                                   (book['title'], book['author'], book['isbn'], book['url'], book['cover_url'], entry['id']))
                        return
        db.execute('INSERT INTO reading_list VALUES (?,?,?,?,?,?,?,?)',
                   (uuid.uuid4().hex, book['title'], book['author'], book.get('isbn', ''), book['url'],
                    book.get('cover_url'), book.get('file_digest'), time.time()))


def download_book(entry_id):
    with contextlib.closing(c.connect()) as db:
        row = db.execute("SELECT * FROM reading_list WHERE id=? AND url!=''", (entry_id,)).fetchone()
    if row is None:
        raise ValueError('Libro non presente in Da leggere.')
    return dict(row)
