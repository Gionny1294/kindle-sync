"""An account's saved books and their links to the local catalog."""
import contextlib
import json
import math
from pathlib import Path
import re
import time
import uuid
from xml.etree import ElementTree as ET
import zipfile

from app import core as c, goodreads_ratings
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
    return [dict(row) for row in db.execute('''SELECT r.*,COALESCE(d.is_read,0) AS is_read
        FROM reading_list r LEFT JOIN reading_list_details d ON d.entry_id=r.id ORDER BY r.created DESC,r.id''')]


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

    def match(self, entry, saved, *, allow_title_only=True):
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
        if not allow_title_only:
            return None
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


def completion_info(row):
    if not row:
        return {'finished_at':None,'personal_rating':None}
    return {'finished_at':row['finished_at'],
            'personal_rating':row['rating_half']/2 if row['rating_half'] is not None else None}


def rating_parts(value):
    if value is None:
        return None
    if type(value) not in (int,float) or not 1<=value<=5 or not math.isfinite(value) or value*2%1:
        raise ValueError('Scegli un voto da 1 a 5, anche a mezzi punti.')
    return int(value*2)


def file_completion(db):
    return {row['digest']:completion_info(row) for row in db.execute('SELECT * FROM reading_file_finish')}


def list_state(db, files):
    saved = entries(db)
    added={row['entry_id']:row['added_at'] for row in db.execute('SELECT * FROM reading_library_dates')}
    finished={row['entry_id']:completion_info(row) for row in db.execute('SELECT * FROM reading_finish')}
    by_file=file_completion(db)
    file_marks = {row['digest']: bool(row['is_read']) for row in db.execute('SELECT * FROM reading_file_status')}
    catalog = current_catalog(db, files) if saved else None
    by_digest = {}
    linked_marks = {}
    linked = []
    for entry in saved:
        row = catalog.match(entry, saved)
        if row is not None:
            digest = row['digest']
            by_digest.setdefault(digest, entry['id'])
            linked_marks[digest] = linked_marks.get(digest, False) or bool(entry['is_read'])
            linked.append((entry, digest))
        entry.update(fingerprint(entry),file_id=row['id'] if row is not None else None,is_read=bool(entry['is_read']),
                     added_at=added.get(entry['id'],entry['created']),**finished.get(entry['id'],completion_info(None)))
    marks = linked_marks | file_marks
    for entry,digest in linked:
        entry['is_read']=marks[digest]
        if digest in by_file:
            entry.update(by_file[digest])
    for entry in saved:
        if not entry['is_read']:
            entry.update(completion_info(None))
    for entry in saved:
        entry.pop('file_digest')
    return saved, {row['id']: by_digest[row['digest']] for row in files if row['available'] and row['digest'] in by_digest}, marks


def recommendation_matches(entry, book):
    saved, new = fingerprint(entry), fingerprint(book)
    return (entry['url'] == book['url']
            or bool(new['reading_isbn'] and saved['reading_isbn'] == new['reading_isbn'])
            or bool(new['reading_key'] and saved['reading_key'] == new['reading_key']))


def update_completion(db, table, key, identifier, is_read, finished_at, rating_half):
    if not is_read:
        db.execute(f'DELETE FROM {table} WHERE {key}=?',(identifier,))
    elif finished_at is not None or rating_half is not None:
        db.execute(f"""INSERT INTO {table}({key},finished_at,rating_half) VALUES (?,?,?)
            ON CONFLICT({key}) DO UPDATE SET
              finished_at=COALESCE({table}.finished_at,excluded.finished_at),
              rating_half=COALESCE(excluded.rating_half,{table}.rating_half)""",
            (identifier,finished_at,rating_half))


def update_read_mark(db,entry_id,is_read,*,finished_at=None,rating_half=None):
    db.execute("""INSERT INTO reading_list_details(entry_id,is_read) VALUES (?,?)
        ON CONFLICT(entry_id) DO UPDATE SET is_read=excluded.is_read""",(entry_id,int(is_read)))
    update_completion(db,'reading_finish','entry_id',entry_id,is_read,finished_at,rating_half)


def set_file_mark(db,digest,is_read,saved,catalog,*,finished_at=None,rating_half=None):
    db.execute("""INSERT INTO reading_file_status(digest,is_read) VALUES (?,?)
        ON CONFLICT(digest) DO UPDATE SET is_read=excluded.is_read""",(digest,int(is_read)))
    update_completion(db,'reading_file_finish','digest',digest,is_read,finished_at,rating_half)
    for entry in saved:
        match=catalog.match(entry,saved)
        if match is not None and match['digest']==digest:
            update_read_mark(db,entry['id'],is_read,finished_at=finished_at,rating_half=rating_half)


def set_read(entry_id,is_read,value=None):
    rating=rating_parts(value)
    with c.transaction() as db:
        saved=entries(db)
        entry=next((book for book in saved if book['id']==entry_id),None)
        if entry is None:
            return False
        catalog=current_catalog(db)
        when=time.time() if is_read else None
        if file:=catalog.match(entry,saved):
            set_file_mark(db,file['digest'],is_read,saved,catalog,finished_at=when,rating_half=rating)
        else:
            update_read_mark(db,entry_id,is_read,finished_at=when,rating_half=rating)
        return True


def set_file_read(file_id,is_read,value=None):
    rating=rating_parts(value)
    with c.transaction() as db:
        catalog=current_catalog(db)
        if not (file:=catalog.files.get(file_id)):
            return False
        set_file_mark(db,file['digest'],is_read,entries(db),catalog,
                      finished_at=time.time() if is_read else None,rating_half=rating)
        return True


def cover_info(entry_id):
    with contextlib.closing(c.connect()) as db:
        row = db.execute('''SELECT r.*,COALESCE(d.cover_sources,'[]') AS cover_sources
            FROM reading_list r LEFT JOIN reading_list_details d ON d.entry_id=r.id WHERE r.id=?''', (entry_id,)).fetchone()
    return dict(row, cover_sources=json.loads(row['cover_sources'])) if row is not None else None


def remember_covers(db, entry_id, sources):
    if sources:
        db.execute('''INSERT INTO reading_list_details(entry_id,cover_sources)
            SELECT id,? FROM reading_list WHERE id=?
            ON CONFLICT(entry_id) DO UPDATE SET cover_sources=excluded.cover_sources,cover_checked=0''',
            (json.dumps(sources), entry_id))


def claim_cover_lookup(entry_id):
    now = time.time()
    with c.transaction() as db:
        changed = db.execute('''INSERT INTO reading_list_details(entry_id,cover_checked)
            SELECT id,? FROM reading_list WHERE id=?
            ON CONFLICT(entry_id) DO UPDATE SET cover_checked=excluded.cover_checked
            WHERE reading_list_details.cover_checked<?''', (now, entry_id, now - 6 * 3600))
        return bool(changed.rowcount)


def set_view(db, entry_id, view, enabled):
    # Keep legacy saved titles available in both views until the user separates them.
    if view not in ('in_library', 'in_queue'):
        raise ValueError('Richiesta non valida.')
    previous=db.execute('SELECT in_library FROM reading_entry_views WHERE entry_id=?',(entry_id,)).fetchone()
    db.execute('INSERT OR IGNORE INTO reading_entry_views VALUES (?,1,1)',(entry_id,))
    db.execute(f'UPDATE reading_entry_views SET {view}=? WHERE entry_id=?',(int(enabled),entry_id))
    if view=='in_library':
        if enabled and previous is not None and not previous['in_library']:
            db.execute('INSERT OR IGNORE INTO reading_library_dates VALUES (?,?)',(entry_id,time.time()))
        elif not enabled:
            db.execute('DELETE FROM reading_library_dates WHERE entry_id=?',(entry_id,))
            db.execute('DELETE FROM reading_shelf_books WHERE entry_id=?',(entry_id,))
            db.execute('DELETE FROM reading_shelf_order WHERE entry_id=?',(entry_id,))
            db.execute('DELETE FROM reading_shelf_layout WHERE item=?',('book:'+entry_id,))
    db.execute('''DELETE FROM reading_list WHERE id=? AND EXISTS
        (SELECT 1 FROM reading_entry_views WHERE entry_id=? AND in_library=0 AND in_queue=0)''',
        (entry_id, entry_id))


def add_view(db, entry_id, view):
    db.execute('INSERT INTO reading_entry_views VALUES (?,?,?)',
               (entry_id, int(view == 'in_library'), int(view == 'in_queue')))


def split_views(db, books):
    membership = {row['entry_id']:(row['in_library'],row['in_queue'])
                  for row in db.execute('SELECT entry_id,in_library,in_queue FROM reading_entry_views')}
    library, queue = [], []
    for book in books:
        in_library, in_queue = membership.get(book['id'], (True, True))
        if in_library:
            library.append(book)
        if in_queue:
            queue.append(book)
    return library, queue


def update(body, *, catalogue_book=None, view='in_queue'):
    if len(body) != 1 or not isinstance(value := next(iter(body.values()), None), str):
        raise ValueError('Richiesta non valida.')
    action = next(iter(body))
    if action == 'remove_id':
        with c.transaction() as db:
            if db.execute('SELECT 1 FROM reading_list WHERE id=?', (value,)).fetchone():
                set_view(db, value, view, False)
        return
    if action == 'recommendation_url':
        book = next((book for book in recommendations_snapshot(include_cover_source=True)['books'] if book['url'] == value), None)
        if book is None:
            raise ValueError('Libro non disponibile nei Consigliati. Aggiorna la pagina.')
    elif action == 'catalogue_id':
        book = catalogue_book
        if not book or not book['url']:
            raise ValueError('Libro non disponibile.')
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
                    set_view(db, entry['id'], view, True)
                    return entry['id']
        else:
            for entry in saved:
                if recommendation_matches(entry, book):
                    remember_covers(db, entry['id'], book.get('cover_sources'))
                    goodreads_ratings.remember(db,entry['id'],book)
                    set_view(db, entry['id'], view, True)
                    if action == 'catalogue_id' and entry['url']:
                        return entry['id']
                    db.execute('''UPDATE reading_list SET title=?,author=?,isbn=?,url=?,cover_url=? WHERE id=?''',
                               (book['title'], book['author'], book['isbn'], book['url'], book['cover_url'], entry['id']))
                    return entry['id']
            prospective = {**book, 'file_digest': None}
            match = catalog.match(prospective, [*saved, prospective])
            if match is not None:
                for entry in saved:
                    if not entry['url'] and entry['file_digest'] == match['digest']:
                        db.execute('''UPDATE reading_list SET title=?,author=?,isbn=?,url=?,cover_url=? WHERE id=?''',
                                   (book['title'], book['author'], book['isbn'], book['url'], book['cover_url'], entry['id']))
                        remember_covers(db, entry['id'], book.get('cover_sources'))
                        goodreads_ratings.remember(db,entry['id'],book)
                        set_view(db, entry['id'], view, True)
                        return entry['id']
        entry_id = uuid.uuid4().hex
        db.execute('INSERT INTO reading_list VALUES (?,?,?,?,?,?,?,?)',
                   (entry_id, book['title'], book['author'], book.get('isbn', ''), book['url'],
                    book.get('cover_url'), book.get('file_digest'), time.time()))
        add_view(db, entry_id, view)
        remember_covers(db, entry_id, book.get('cover_sources'))
        goodreads_ratings.remember(db,entry_id,book)
        return entry_id


def download_book(entry_id):
    with contextlib.closing(c.connect()) as db:
        row = db.execute("SELECT * FROM reading_list WHERE id=? AND url!=''", (entry_id,)).fetchone()
    if row is None:
        raise ValueError('Libro non presente in Da leggere.')
    return dict(row)
