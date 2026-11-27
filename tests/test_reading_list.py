import contextlib
from datetime import datetime
import io
import json
import time
import zipfile

import pytest

from app import core, reading_list, recommendations, telegram_books


@pytest.fixture
def client(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    library = tmp_path / 'library'
    library.mkdir()
    monkeypatch.setattr(core, 'DATA', data)
    monkeypatch.setattr(core, 'LIBRARY', library)
    monkeypatch.setattr(core, 'CONFIG', data / 'kindle-sync.conf')
    from app import accounts, web
    core.init()
    accounts.init()
    return web.app.test_client()


def register(client, name):
    csrf = client.get('/api/session').json['csrf']
    response = client.post('/api/auth/register', json={'name': name, 'password': 'test-password'},
                           headers={'X-CSRF-Token': csrf})
    assert response.status_code == 200, response.json
    return {'X-CSRF-Token': response.json['csrf'], 'X-Account-ID': response.json['user']['id']}


def upload(client, headers, name, content):
    response = client.post('/api/upload', headers=headers, data={'file': (io.BytesIO(content), name)})
    assert response.status_code == 201, response.json
    return response.json['file_id']


def recommend():
    source = recommendations.DEFAULTS[0]
    today = datetime.now(recommendations.ROME).date().isoformat()
    book = {'title': 'Una nuova storia', 'author': 'Luca Riva', 'isbn': '9780306406157',
            'url': 'https://www.example.com/book', 'cover': '', 'published': today, 'rank': 1}
    with core.transaction() as db:
        recommendations.initialize(db)
        core.put(db, 'recommendation_sources', json.dumps([source]))
        cache = {source['id']: {'url': source['url'], 'books': [book], 'checked': time.time()}}
        core.put(db, 'recommendation_cache', json.dumps(cache))
    return book


def epub(title='Una nuova storia', author='Luca Riva', isbn='0-306-40615-2'):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('META-INF/container.xml', '<container><rootfile full-path="book.opf"/></container>')
        archive.writestr('book.opf', f'''<package xmlns:dc="http://purl.org/dc/elements/1.1/">
            <metadata><dc:title>{title}</dc:title><dc:creator>{author}</dc:creator>
            <dc:identifier>urn:isbn:{isbn}</dc:identifier></metadata></package>''')
    return data.getvalue()


def test_saved_recommendation_links_later_upload_and_survives_refresh(client):
    headers = register(client, 'First')
    book = recommend()
    source = {'recommendation_url': book['url']}
    assert client.post('/api/reading-list', headers=headers, json=source).status_code == 200
    assert client.post('/api/reading-list', headers=headers, json=source).status_code == 200
    entry, = client.get('/api/state').json['reading_list']
    assert entry['file_id'] is None

    # A saved recommendation stays available when the chart changes.
    with core.transaction() as db:
        core.put(db, 'recommendation_cache', '{}')
    assert reading_list.download_book(entry['id'])['title'] == book['title']
    file_id = upload(client, headers, 'unrelated-filename.epub', epub())
    state = client.get('/api/state').json
    assert state['reading_list'][0]['file_id'] == file_id
    assert state['reading_list_files'] == {file_id: entry['id']}
    assert client.post('/api/reading-list', headers=headers, json={'file_id': file_id}).status_code == 200
    assert len(client.get('/api/state').json['reading_list']) == 1
    assert client.post('/api/reading-list', headers=headers, json={'remove_id': entry['id']}).status_code == 200
    assert client.get('/api/state').json['reading_list'] == []
    assert client.get('/api/state').json['files'][0]['id'] == file_id


def test_list_is_private_and_account_deletion_clears_it(client):
    first = register(client, 'First')
    file_id = upload(client, first, 'Private.txt', b'Test book')
    assert client.post('/api/reading-list', json={'file_id': file_id}).status_code == 403
    assert client.post('/api/reading-list', headers=first, json={'file_id': file_id}).status_code == 200
    entry = client.get('/api/state').json['reading_list'][0]
    second = register(client, 'Second')
    assert client.get('/api/state').json['reading_list'] == []
    assert client.post('/api/reading-list', headers=second, json={'file_id': file_id}).status_code == 400
    assert client.post('/api/reading-list', headers=second, json={'remove_id': entry['id']}).status_code == 200
    assert client.post('/api/telegram/search', headers=second, json={'reading_list_id': entry['id']}).status_code == 400
    session = client.get('/api/session').json
    response = client.post('/api/auth/login', json={'name': 'First', 'password': 'test-password'},
                           headers={'X-CSRF-Token': session['csrf']})
    assert response.status_code == 200, response.json
    assert client.get('/api/state').json['reading_list'][0]['id'] == entry['id']
    response = client.post('/api/account/delete', json={'current_password': 'test-password'},
                           headers={'X-CSRF-Token': response.json['csrf']})
    assert response.status_code == 200, response.json
    with contextlib.closing(core.connect()) as db:
        assert reading_list.entries(db) == []


def test_catalog_backfill_and_explicit_download_link(client, tmp_path, monkeypatch):
    headers = register(client, 'First')
    book = recommend()
    client.post('/api/reading-list', headers=headers, json={'recommendation_url': book['url']})
    file_id = upload(client, headers, 'old.epub', epub())
    with core.transaction() as db:
        db.execute('DELETE FROM reading_file_metadata')
    assert client.get('/api/state').json['reading_list'][0]['file_id'] is None
    reading_list.index_existing_epubs()
    entry = client.get('/api/state').json['reading_list'][0]
    assert entry['file_id'] == file_id

    downloads = tmp_path / 'downloads'
    downloads.mkdir()
    monkeypatch.setenv('BOOK_DOWNLOAD_DIR', str(downloads))
    saved = downloads / 'Downloaded.txt'
    saved.write_text('Test download')
    downloaded_id = telegram_books.register_download(saved, entry['id'])
    assert client.get('/api/state').json['reading_list'][0]['file_id'] == downloaded_id


def test_ambiguous_filenames_do_not_link(client):
    headers = register(client, 'First')
    book = recommend()
    client.post('/api/reading-list', headers=headers, json={'recommendation_url': book['url']})
    upload(client, headers, 'Una nuova storia.txt', b'First file')
    upload(client, headers, 'Una nuova storia.pdf', b'%PDF-1.5\nSecond file')
    assert client.get('/api/state').json['reading_list'][0]['file_id'] is None


def test_download_folder_is_used_and_stays_account_specific(client, tmp_path, monkeypatch):
    root = tmp_path / 'downloads'
    root.mkdir()
    monkeypatch.setenv('BOOK_DOWNLOAD_DIR', str(root))
    first = register(client, 'First')
    response = client.post('/api/downloads/folder', headers=first, json={'folder': 'Romanzi/2026'})
    assert response.status_code == 200, response.json
    assert response.json['destination'] == 'Libri/Singoli/Romanzi/2026'
    temp = tmp_path / 'download.txt'
    temp.write_text('Test book')
    saved = telegram_books.install_file(temp, 'Book.txt')
    assert saved == root / 'Romanzi' / '2026' / 'Book.txt'
    file = telegram_books.register_download(saved)
    assert any(row['folder'] == 'Singoli/Romanzi/2026' and row['id'] == file
               for row in client.get('/api/state').json['files'])
    outside = tmp_path / 'outside'
    outside.mkdir()
    (root / 'alias').symlink_to(outside, target_is_directory=True)
    assert client.post('/api/downloads/folder', headers=first, json={'folder': 'alias/unavailable'}).status_code == 400
    assert not list(outside.iterdir())
    second = register(client, 'Second')
    assert client.get('/api/telegram', headers=second).json['folder'] == ''
    assert client.post('/api/downloads/folder', headers=first, json={'folder': ''}).status_code == 409


def test_xml_declarations_are_rejected_independent_of_encoding():
    from app.covers import parse_xml
    for encoding in ('utf-8', 'utf-16'):
        assert parse_xml('<book/>'.encode(encoding)).tag == 'book'
        with pytest.raises(ValueError, match='Dichiarazioni'):
            parse_xml('<!DOCTYPE book><book/>'.encode(encoding))


def test_new_strings_in_every_locale():
    from app.i18n import LANGUAGES, catalog
    strings = ('Da leggere', 'Caricamento della lista…', 'Salva in Da leggere', 'Rimuovi da Da leggere',
               'Da scaricare o caricare', 'La lista è vuota.', 'Salva un titolo dalla Libreria o da Consigliati.',
               'Salvato in Da leggere.', 'Rimosso da Da leggere.', 'Libro non presente in Da leggere.',
               'Chiudi il libro e torna a Da leggere', 'Cartella dei download', 'Sottocartella',
               'Cartella dei download salvata.', 'Indica una sottocartella valida della cartella download.',
               'Nuova fonte')
    for lang in LANGUAGES:
        for string in strings:
            assert catalog(lang)[string]
            if lang != 'it':
                assert catalog(lang)[string] != string
