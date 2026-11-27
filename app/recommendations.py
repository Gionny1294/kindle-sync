"""Public book charts and recent releases from account-owned sources."""
import concurrent.futures
import contextlib
from datetime import datetime, timedelta
import hashlib
import http.client
import io
import ipaddress
import json
import re
import socket
import ssl
import threading
import time
import unicodedata
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode
from zoneinfo import ZoneInfo

import html5lib
from PIL import Image
from app import core as c, accounts
from app.covers import parse_xml

CATEGORIES=('new','charts','popular')
DEFAULTS=[
    {'id':'ibs-new','name':'IBS · Novità della settimana','url':'https://www.ibs.it/libri/ultima-settimana?Products_per_page=50','category':'new','enabled':True},
    {'id':'ibs-charts','name':'IBS · Classifica settimanale','url':'https://www.ibs.it/classifica/libri/1week/sold','category':'charts','enabled':True},
    {'id':'feltrinelli-charts','name':'Feltrinelli · Classifica settimanale','url':'https://www.lafeltrinelli.it/classifica/libri/1week/sold','category':'charts','enabled':True},
    {'id':'goodreads-month','name':'Goodreads · Più letti del mese','url':'https://www.goodreads.com/book/most_read?category=all&country=all&duration=m','category':'charts','enabled':True},
    {'id':'mondadori-charts','name':'Mondadori · Più venduti della settimana','url':'https://www.mondadoristore.it/libri/italiani','category':'charts','enabled':True},
    {'id':'mondadori-new','name':'Mondadori · Novità','url':'https://www.mondadoristore.it/libri-novita-e-ultime-uscite?page=1','category':'new','enabled':True},
]
MAX_BYTES=3*1024*1024
ROME=ZoneInfo('Europe/Rome')


def next_run(now=None):
    local=datetime.fromtimestamp(time.time() if now is None else now,ROME)
    target=local.replace(hour=local.hour//6*6,minute=0,second=0,microsecond=0)+timedelta(hours=6)
    return target.timestamp()


def read_json(db,key,default):
    row=db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
    return json.loads(row[0]) if row else default


def initialize(db):
    if not db.execute("SELECT 1 FROM settings WHERE key='recommendation_sources'").fetchone():
        c.put(db,'recommendation_sources',json.dumps(DEFAULTS))
        c.put(db,'recommendation_next',next_run())
    if read_json(db,'recommendation_six_hour_schedule',0)!=2:
        c.put(db,'recommendation_next',time.time())
        c.put(db,'recommendation_six_hour_schedule',2)
    if not db.execute("SELECT 1 FROM settings WHERE key='recommendation_mondadori_added'").fetchone():
        sources=read_json(db,'recommendation_sources',[])
        urls={source['url'] for source in sources}
        for source in DEFAULTS:
            if source['id'].startswith('mondadori-') and source['url'] not in urls and len(sources)<12:sources.append(source)
        c.put(db,'recommendation_sources',json.dumps(sources));c.put(db,'recommendation_mondadori_added',1)


def public_url(value):
    if not isinstance(value,str) or len(value)>2048 or any(ord(ch)<33 for ch in value) or '\\' in value:
        raise ValueError('Inserisci un indirizzo HTTPS pubblico valido.')
    try:
        p=urlsplit(value)
        if p.scheme!='https' or not p.hostname or p.username or p.password or p.port not in (None,443):raise ValueError()
        host=p.hostname.encode('idna').decode('ascii').lower().rstrip('.')
        if '.' not in host or host.endswith(('.local','.localhost','.internal')):raise ValueError()
        try:
            if not ipaddress.ip_address(host).is_global:raise ValueError()
        except ValueError:
            if re.fullmatch(r'[0-9.:]+',host):raise ValueError()
        if not re.fullmatch(r'[a-z0-9.-]+',host):raise ValueError()
        return urlunsplit(('https',host,p.path or '/',p.query,''))
    except (ValueError,UnicodeError):
        raise ValueError('Inserisci un indirizzo HTTPS pubblico valido.') from None


def fetch(url):
    """Bounded HTTPS with public DNS addresses pinned for each redirect and TLS SNI."""
    deadline=time.monotonic()+18
    for _ in range(4):
        url=public_url(url);p=urlsplit(url)
        addresses=socket.getaddrinfo(p.hostname,443,type=socket.SOCK_STREAM)
        ips=[ipaddress.ip_address(address[4][0]) for address in addresses]
        if not ips or any(not ip.is_global or ip.is_multicast or getattr(ip,'is_site_local',False) for ip in ips):
            raise ValueError('La fonte deve usare un indirizzo pubblico.')
        connection=http.client.HTTPSConnection(p.hostname,timeout=6,context=ssl.create_default_context())
        # Keep the original host for certificate verification; try only checked public IPs.
        def pinned_connect():
            failure=None
            for address in addresses[:3]:
                raw=socket.socket(address[0],address[1],address[2]);raw.settimeout(6)
                try:
                    raw.connect(address[4])
                    connection.sock=connection._context.wrap_socket(raw,server_hostname=p.hostname)
                    return
                except OSError as exc:
                    raw.close();failure=exc
                except BaseException:
                    raw.close();raise
            raise failure
        connection.connect=pinned_connect
        try:
            connection.request('GET',urlunsplit(('','',p.path or '/',p.query,'')),headers={
                'User-Agent':'Mozilla/5.0 (compatible; KindleSync/1.0; public book charts)',
                'Accept':'text/html, application/rss+xml, application/atom+xml, application/xml, image/*;q=0.8',
                'Accept-Encoding':'identity'})
            response=connection.getresponse()
            if response.status in (301,302,303,307,308):
                url=urljoin(url,response.getheader('Location',''));continue
            if response.status!=200:raise ValueError('Fonte non disponibile (HTTP '+str(response.status)+').')
            content=bytearray()
            while True:
                if time.monotonic()>deadline:raise ValueError('La fonte non risponde in tempo.')
                chunk=response.read(min(65536,MAX_BYTES+1-len(content)))
                if not chunk:break
                content.extend(chunk)
                if len(content)>MAX_BYTES:raise ValueError('La pagina della fonte è troppo grande.')
            return bytes(content),response.getheader('Content-Type',''),url
        finally:connection.close()
    raise ValueError('Troppi reindirizzamenti della fonte.')


def text(node):
    return ' '.join(''.join(node.itertext()).split()) if node is not None else ''


def has_class(node,value):return value in node.get('class','').split()


def safe_link(base,value):
    if not isinstance(value,str):return ''
    try:return public_url(urljoin(base,value)) if value else ''
    except ValueError:return ''


def source_name(url):
    host=urlsplit(url).hostname or ''
    return {'www.ibs.it':'IBS','ibs.it':'IBS','www.lafeltrinelli.it':'Feltrinelli','lafeltrinelli.it':'Feltrinelli','www.goodreads.com':'Goodreads','goodreads.com':'Goodreads','www.mondadoristore.it':'Mondadori','mondadoristore.it':'Mondadori'}.get(host,host)


def publication_date(value):
    value=str(value or '').strip()
    if not re.fullmatch(r'\d{4}(?:-\d{2}(?:-\d{2})?)?',value):return ''
    try:datetime.strptime(value,{4:'%Y',7:'%Y-%m',10:'%Y-%m-%d'}[len(value)])
    except ValueError:return ''
    return value


def rating_fields(value,count=None,url='',best=5):
    try:
        score=float(str(value).replace(',','.'));maximum=float(best)
        votes=int(str(count).replace(',','').replace(' ','').replace('.','')) if count is not None else None
        if maximum<=0 or not 0<score<=maximum or votes is not None and votes<=0:return {}
        score=round(score/maximum*5,2)
    except (TypeError,ValueError,OverflowError):return {}
    return dict(rating_value=score,rating_count=votes,rating_source=source_name(url),rating_url=url,
                rating=str(score).rstrip('0').rstrip('.')+'/5')


def schema_records(doc, record_types=('Book','Product')):
    def visit(node):
        if isinstance(node,list):
            for child in node:yield from visit(child)
        elif isinstance(node,dict):
            types=node.get('@type',[]);types=[types] if isinstance(types,str) else (types if isinstance(types,list) else [])
            if any(kind in types for kind in record_types):
                yield node
                if isinstance(node.get('hasVariant'),list):yield from visit(node['hasVariant'])
            else:
                for child in node.values():
                    if isinstance(child,(dict,list)):yield from visit(child)
    for script in doc.iter('script'):
        if script.get('type')=='application/ld+json':
            try:yield from visit(json.loads(script.text or ''))
            except (ValueError,TypeError,RecursionError):continue


def synopsis_text(value):
    if not isinstance(value,str) or not value.strip():return ''
    fragment=html5lib.parseFragment(value[:16000],namespaceHTMLElements=False)

    def collect(node):
        if not isinstance(node.tag,str) or node.tag in ('script','style'):return ''
        if node.tag=='br':return '\n'
        content=(node.text or '')+''.join(collect(child)+(child.tail or '') for child in node)
        return '\n'+content+'\n' if node.tag in ('p','div','li','blockquote') else content

    paragraphs=[' '.join(part.split()) for part in collect(fragment).splitlines()]
    return '\n\n'.join(filter(None,paragraphs))


def catalogue_genres(value):
    labels=[]
    generic={'libri','books','ebook','ebooks','e-book','kindle'}
    for item in value if isinstance(value,list) else [value]:
        if isinstance(item,dict):item=item.get('name')
        if not isinstance(item,str) or '://' in item or len(item)>180:continue
        label=' '.join(synopsis_text(item).split())
        if label and label.casefold() not in generic and label not in labels:labels.append(label)
    return labels[:3]


def breadcrumb_genres(base,page_url,title,breadcrumb):
    if source_name(base) not in ('IBS','Feltrinelli') or not isinstance(breadcrumb,list):return []
    trail=[(safe_link(base,item.get('item')),item.get('name')) for item in breadcrumb if isinstance(item,dict)]
    if len(trail)<3:return []
    last_url,last_name=trail[-1]
    if urlsplit(last_url)._replace(query='',fragment='').geturl().rstrip('/')!=page_url:return []
    if ' '.join(str(last_name).split()).casefold()!=title:return []
    for url,name in trail[:-1]:
        if urlsplit(url).hostname==urlsplit(base).hostname and urlsplit(url).path.startswith('/libri/'):
            return catalogue_genres(name)
    return []


def detail_book_text(content,base,book):
    doc=html5lib.parse(content,namespaceHTMLElements=False)
    wanted=book.get('isbn','').replace('-','')
    title=' '.join(book['title'].split()).casefold()
    book_url=urlsplit(book['url'])._replace(query='',fragment='').geturl().rstrip('/')
    page_url=urlsplit(base)._replace(query='',fragment='').geturl().rstrip('/')
    matches=[];genres=[];breadcrumbs=[]
    for node in schema_records(doc,('Book','Product','BreadcrumbList')):
        if 'itemListElement' in node:
            breadcrumbs.append(node['itemListElement']);continue
        isbn=str(node.get('isbn') or node.get('gtin13') or node.get('gtin') or '').replace('-','')
        if wanted and isbn and isbn!=wanted:continue
        link=safe_link(base,node.get('url') or node.get('@id'))
        link=urlsplit(link)._replace(query='',fragment='').geturl().rstrip('/')
        if wanted and isbn==wanted:
            score=3
        elif link==book_url:
            score=2
        elif link in ('',page_url) and ' '.join(str(node.get('name','')).split()).casefold()==title:
            score=1
        else:continue
        description=synopsis_text(node.get('description'))
        if description:matches.append((score,len(description),description))
        labels=catalogue_genres(node.get('genre'))
        if labels:genres.append((score,labels))
        elif labels:=catalogue_genres(node.get('category')):genres.append((0,labels))
    labels=max(genres,key=lambda match:match[0],default=(0,[]))[1]
    if not labels:
        for breadcrumb in breadcrumbs:
            labels=breadcrumb_genres(base,page_url,title,breadcrumb)
            if labels:break
    return max(matches,default=(0,0,''))[2],labels


def book_synopsis(book):
    urls=[book['url']]
    isbn=book.get('isbn','').replace('-','')
    if re.fullmatch(r'97[89][0-9]{10}',isbn) and source_name(book['url'])!='Mondadori':
        urls.append('https://www.mondadoristore.it/p/'+isbn)
    reached=False;genres=[]
    for url in urls:
        try:content,_,base=fetch(url)
        except (OSError,http.client.HTTPException,ValueError):continue
        reached=True
        description,labels=detail_book_text(content,base,book)
        genres=genres or labels
        if description:return dict(synopsis=description,genres=genres,source_url=base,source_name=source_name(base))
    if not reached:
        raise ValueError('Impossibile recuperare la trama. Riprova tra poco.')
    return dict(synopsis='',genres=genres)


def detail_metadata(content,base,book):
    """Read only the selected book's metadata, excluding related/recommended products."""
    doc=html5lib.parse(content,namespaceHTMLElements=False)
    records=list(schema_records(doc));matched=[]
    wanted=book.get('isbn','');title=book.get('title','').strip().casefold()
    for node in records:
        isbn=str(node.get('isbn') or node.get('gtin13') or '').replace('-','')
        link=safe_link(base,node.get('url',''))
        if wanted and isbn and wanted!=isbn:continue
        if link and link.rstrip('/')!=book['url'].rstrip('/') and not (wanted and isbn==wanted):continue
        if wanted and isbn==wanted or link==book['url'] or not link and str(node.get('name','')).strip().casefold()==title:
            matched.append(node)
    result={}
    for node in matched:
        date=publication_date(node.get('datePublished'))
        if date and len(date)>=len(result.get('published','')):result.update(published=date,published_source=source_name(base),published_url=base)
        image=node.get('image','')
        if isinstance(image,list):image=image[0] if image else ''
        if isinstance(image,dict):image=image.get('url','')
        if image and isinstance(image,str):result['cover']=safe_link(base,image)
        aggregate=node.get('aggregateRating')
        if isinstance(aggregate,dict):result.update(rating_fields(aggregate.get('ratingValue'),aggregate.get('ratingCount',aggregate.get('reviewCount')),base,aggregate.get('bestRating',5)))
    # Goodreads exposes the edition's publication timestamp in its normalized book record.
    if matched and source_name(base)=='Goodreads' and not result.get('published'):
        for script in doc.iter('script'):
            if script.get('id')!='__NEXT_DATA__':continue
            try:
                state=json.loads(script.text or '{}')['props']['pageProps']['apolloState']
                for node in state.values():
                    if not isinstance(node,dict) or node.get('__typename')!='Book':continue
                    if str(node.get('titleComplete') or node.get('title','')).strip().casefold()!=title:continue
                    stamp=node.get('details',{}).get('publicationTime')
                    if isinstance(stamp,(float,int)) and stamp>0:
                        date=datetime.fromtimestamp(stamp/1000,ROME).date().isoformat()
                        result.update(published=date,published_source='Goodreads',published_url=base);break
            except (ValueError,TypeError,KeyError,OverflowError):continue
    if source_name(base)=='Mondadori':result.update(mondadori_metadata(doc,wanted,base))
    return result


def mondadori_metadata(doc,isbn,base):
    result={}
    def visit(node):
        if isinstance(node,list):
            for child in node:visit(child)
        elif isinstance(node,dict):
            if str(node.get('bp_master_ean',''))==isbn and node.get('bp_publication_date'):
                try:
                    date=datetime.strptime(node['bp_publication_date'],'%d/%m/%Y').date().isoformat()
                    result.update(published=date,published_source='Mondadori',published_url=base)
                except (ValueError,TypeError):pass
            if str(node.get('code',''))==isbn:
                date=publication_date(str(node.get('ultimaPubblicazione',''))[:10])
                if date:result.update(published=date,published_source='Mondadori',published_url=base)
                result.update(rating_fields(node.get('averageRating'),node.get('numberOfReviews'),base))
            for child in node.values():
                if isinstance(child,(dict,list)):visit(child)
    for script in doc.iter('script'):
        if script.get('id')=='ng-state':
            try:visit(json.loads(script.text or '{}'))
            except (ValueError,TypeError,RecursionError):continue
    return result


METADATA_FIELDS=('rating','rating_value','rating_count','rating_source','rating_url','published','published_source','published_url','cover','metadata_updated')


def enrich_cached():
    """Date release candidates and enrich books shown in the overall chart."""
    visible=snapshot()['books']
    visible_urls={book['url'] for book in visible};visible_isbns={book['isbn'] for book in visible if book.get('isbn')}
    with contextlib.closing(c.connect()) as db:
        sources=read_json(db,'recommendation_sources',[]);cache=read_json(db,'recommendation_cache',{})
    targets={}
    for source in sources:
        if not source['enabled']:continue
        for book in cache.get(source['id'],{}).get('books',[]):
            if source['category']!='new' and book['url'] not in visible_urls and book.get('isbn') not in visible_isbns:continue
            host=urlsplit(book['url']).hostname
            if host not in ('www.ibs.it','ibs.it','www.lafeltrinelli.it','lafeltrinelli.it','www.goodreads.com','goodreads.com','www.mondadoristore.it','mondadoristore.it'):continue
            key=book.get('isbn') or book['url']
            targets.setdefault(key,book)
    deadline=time.monotonic()+100
    def load(pair):
        key,book=pair
        if time.monotonic()>deadline:return key,{}
        try:
            content,_,base=fetch(book['url']);metadata=detail_metadata(content,base,book)
            if metadata:metadata['metadata_updated']=time.time()
            return key,metadata
        except Exception:return key,{}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for key,metadata in pool.map(load,targets.items()):
            if not metadata:continue
            with c.transaction() as db:
                current=read_json(db,'recommendation_cache',{})
                for entry in current.values():
                    for book in entry.get('books',[]):
                        if (book.get('isbn') or book['url'])==key:
                            # A detail-page vote supersedes the coarse chart stars.
                            book.update(metadata)
                c.put(db,'recommendation_cache',json.dumps(current))


def parse_books(content,base,limit=50,*,enrich=True):
    """Known chart cards, schema.org Book/ItemList, or a book-oriented RSS/Atom feed."""
    books=[];seen=set()
    def add(title,url,author='',cover='',isbn='',rating='',published=''):
        title=' '.join(str(title or '').split())[:350];url=safe_link(base,url)
        if not title or not url or url in seen:return
        seen.add(url)
        books.append(dict(title=title,url=url,author=str(author or '')[:250],cover=safe_link(base,cover),
                          isbn=str(isbn or '')[:32],rating=str(rating or '')[:60],published=str(published or '')[:32],rank=len(books)+1))
        return books[-1]
    stripped=content[:1024].replace(b'\x00',b'').lstrip(b'\xef\xbb\xbf\xff\xfe \t\r\n')
    if stripped.startswith(b'<?xml') or stripped.startswith((b'<rss',b'<feed')):
        try:root=parse_xml(content)
        except ValueError:raise ValueError('Feed non valido.') from None
        for node in root.iter():
            node.tag=node.tag.rsplit('}',1)[-1]
        for item in list(root.iter('item'))+list(root.iter('entry')):
            link=item.find('link');href=(link.get('href') or text(link)) if link is not None else ''
            author=item.find('author');image=item.find('thumbnail')
            add(text(item.find('title')),href,text(author),image.get('url','') if image is not None else '',published=text(item.find('datePublished')))
        return books[:limit]
    doc=html5lib.parse(content,namespaceHTMLElements=False)
    parents={child:parent for parent in doc.iter() for child in parent}
    host=urlsplit(base).hostname or ''
    if host in ('www.mondadoristore.it','mondadoristore.it'):
        if urlsplit(base).path.rstrip('/')=='/libri/italiani':
            roots=[node for node in doc.iter('app-mondadori-classifica-carosel') if 'I più venduti della settimana' in text(node)]
            cards=[card for root in roots for card in root.iter('app-mondadori-product-item-elastic')]
        else:cards=list(doc.iter('app-mondadori-product-grid-item'))+list(doc.iter('app-mondadori-product-item-elastic'))
        for card in cards:
            title=next((node for node in card.iter('a') if has_class(node,'name')),None)
            link=next((node.get('href','') for node in card.iter('a') if '/p/' in node.get('href','')),'')
            author=next((text(node) for node in card.iter() if has_class(node,'author-names')),'')
            cover=next((node.get('src','') for node in card.iter('img')),'')
            isbn=re.search(r'/p/(\d{13})',link)
            before=len(books)
            add(text(title),urljoin('https://www.mondadoristore.it/',link),author,cover,isbn[1] if isbn else '')
            if enrich and len(books)>before and isbn:books[-1].update(mondadori_metadata(doc,isbn[1],books[-1]['url']))
        if books:return books[:limit]
    for link in doc.iter('a'):
        kind=''
        if host in ('www.ibs.it','ibs.it','www.lafeltrinelli.it','lafeltrinelli.it'):
            ident=link.get('id','')
            if ident.startswith(('product-title-','plpProductTitle_')):kind='store'
        elif host in ('www.goodreads.com','goodreads.com'):
            if has_class(link,'bookTitle') or link.get('data-testid')=='bookTitle':kind='goodreads'
        if not kind:continue
        card=link
        for _ in range(9):
            card=parents.get(card)
            if card is None:break
            if (kind=='store' and any(has_class(card,k) for k in ('cc-item-row','cc-product-list-item'))) or (kind=='goodreads' and (card.tag=='tr' or has_class(card,'BookListItem'))):break
        if card is None:continue
        author=[];cover='';rating='';published='';rating_value=None;rating_count=None
        for node in card.iter():
            if any(has_class(node,k) for k in ('cc-author-name','authorName','ContributorLink__name')) and text(node):author.append(text(node))
            if node.tag=='img' and not cover and (kind!='store' or has_class(node,'cc-img')):cover=node.get('src') or node.get('data-src','')
            if has_class(node,'minirating'):
                rating=text(node).split('—')[0].strip()
                value=re.search(r'(\d+(?:[.,]\d+)?)\s+avg rating',text(node))
                count=re.search(r'([0-9,]+)\s+ratings',text(node))
                if value:rating_value=value[1]
                if count:rating_count=count[1]
            if has_class(node,'AverageRating'):
                rating=node.get('aria-label','')
                value=re.search(r'(\d+(?:\.\d+)?)\s+stars',rating)
                if value:rating_value=value[1]
            if has_class(node,'cc-rating-average'):rating_value=text(node)
            if has_class(node,'cc-rating-number'):rating_count=re.sub(r'[^0-9]','',text(node)) or None
            if has_class(node,'cc-publisher'):
                year=re.search(r'\b((?:19|20)\d{2})\b',text(node))
                if year:published=year[1]
        if kind=='goodreads':
            year=re.search(r'\bpublished\s+((?:19|20)\d{2})\b',text(card))
            if year:published=year[1]
        added=add(text(link),link.get('href',''),', '.join(dict.fromkeys(author)),cover,link.get('data-ean',''),rating,published)
        if added:
            if published:added.update(published_source=source_name(base),published_url=base)
            added.update(rating_fields(rating_value,rating_count,base))
    if books:return books[:limit]
    def schema(node):
        if isinstance(node,list):
            for item in node:schema(item)
        elif isinstance(node,dict):
            types=node.get('@type',[]);types=[types] if isinstance(types,str) else (types if isinstance(types,list) else [])
            if 'Book' in types:
                author=node.get('author','');author=[author] if not isinstance(author,list) else author
                names=', '.join(str(a.get('name','') if isinstance(a,dict) else a) for a in author)
                image=node.get('image','');image=image[0] if isinstance(image,list) and image else image
                if isinstance(image,dict):image=image.get('url','')
                added=add(node.get('name'),node.get('url',''),names,image,node.get('isbn',''),published=publication_date(node.get('datePublished','')))
                if added:
                    aggregate=node.get('aggregateRating',{})
                    if isinstance(aggregate,dict):added.update(rating_fields(aggregate.get('ratingValue'),aggregate.get('ratingCount',aggregate.get('reviewCount')),base,aggregate.get('bestRating',5)))
                    if added['published']:added.update(published_source=source_name(base),published_url=base)
            else:
                for value in node.values():
                    if isinstance(value,(dict,list)):schema(value)
    for script in doc.iter('script'):
        if script.get('type')=='application/ld+json':
            try:schema(json.loads(script.text or ''))
            except (ValueError,TypeError,AttributeError):continue
    return books[:limit]


def save_sources(body):
    rows=body.get('sources') if isinstance(body,dict) else None
    if not isinstance(rows,list) or len(rows)>12:raise ValueError('Puoi aggiungere fino a 12 fonti.')
    clean=[];urls=set()
    for row in rows:
        if not isinstance(row,dict):raise ValueError('Fonte non valida.')
        name=row.get('name');category=row.get('category');enabled=row.get('enabled')
        if not isinstance(name,str) or not 1<=len(name.strip())<=100 or category not in CATEGORIES or not isinstance(enabled,bool):raise ValueError('Completa nome e categoria della fonte.')
        url=public_url(row.get('url'));ident=hashlib.sha256(url.encode()).hexdigest()[:20]
        # Stable IDs are derived from the address, never taken from user input.
        if url in urls:raise ValueError('Questa fonte è già presente.')
        urls.add(url)
        clean.append(dict(id=ident,name=name.strip(),url=url,category=category,enabled=enabled))
    with c.transaction() as db:
        initialize(db)
        old=read_json(db,'recommendation_cache',{})
        by_url={entry.get('url'):entry for entry in old.values()}
        cache={row['id']:by_url[row['url']] for row in clean if row['url'] in by_url}
        c.put(db,'recommendation_sources',json.dumps(clean))
        c.put(db,'recommendation_cache',json.dumps(cache))
    return snapshot()


def base_book_title(title):
    return re.sub(r'\.?\s*Ediz\.\s*(?:italiana|illustrata|a colori)\.?','',title,flags=re.I).strip()


def book_identity(book):
    title=base_book_title(book['title'])
    value=unicodedata.normalize('NFKD',title+' '+book.get('author','')).casefold()
    return re.sub(r'[\W_]+','', ''.join(ch for ch in value if not unicodedata.combining(ch)),flags=re.UNICODE)


def identity_groups(books):
    """Resolve title/ISBN aliases before merging, including bridges between editions."""
    parents={}
    def root(key):
        parents.setdefault(key,key)
        while parents[key]!=key:
            parents[key]=parents[parents[key]];key=parents[key]
        return key
    for book in books:
        title='title:'+book_identity(book)
        if book.get('isbn'):parents[root('isbn:'+book['isbn'])]=root(title)
        else:root(title)
    return {key:root(key) for key in parents}


def snapshot():
    now=time.time()
    with c.transaction() as db:
        initialize(db)
        sources=read_json(db,'recommendation_sources',[]);cache=read_json(db,'recommendation_cache',{})
        busy=read_json(db,'recommendation_busy',0)>now
        due=read_json(db,'recommendation_next',next_run())
    books={};reports=[]
    aliases=identity_groups(book for source in sources if source['enabled'] for book in cache.get(source['id'],{}).get('books',[]))
    today=datetime.fromtimestamp(now,ROME).date()
    first_day=(today-timedelta(days=1)).isoformat()
    last_day=today.isoformat()
    for source in sources:
        entry=cache.get(source['id'],{});reports.append({**source,'updated':entry.get('updated'),'checked':entry.get('checked'),
            'error':entry.get('error',''),'count':len(entry.get('books',[]))})
        if not source['enabled']:continue
        category='charts' if source['category']=='popular' else source['category']
        for book in entry.get('books',[]):
            key=aliases['title:'+book_identity(book)]
            item=books.setdefault(key,{**{k:v for k,v in book.items() if k!='cover'},'categories':[],'sources':[],
                '_scores':{'new':{},'charts':{}},'_new_publication':None,'cover_url':None})
            if not item.get('cover_url') and book.get('cover'):item['cover_url']='/api/recommendations/covers/'+hashlib.sha256(book['cover'].encode()).hexdigest()
            if book.get('rating_value') is not None and (item.get('rating_value') is None or (book.get('rating_count') or 0)>(item.get('rating_count') or 0)):
                for field in ('rating','rating_value','rating_count','rating_source','rating_url'):item[field]=book.get(field)
            if len(book.get('published',''))>len(item.get('published','')):
                for field in ('published','published_source','published_url'):item[field]=book.get(field)
            if category=='new':
                released=publication_date(book.get('published'))
                previous=item['_new_publication']
                if len(released)==10 and first_day<=released<=last_day and (previous is None or released>previous['published']):
                    item['_new_publication']={'published':released,'published_source':book.get('published_source'),
                        'published_url':book.get('published_url')}
            if category not in item['categories']:item['categories'].append(category)
            provider=source_name(source['url']);score=1/(60+book['rank'])
            item['_scores'][category][provider]=max(score,item['_scores'][category].get(provider,0))
            item['sources'].append({'name':source['name'],'url':source['url'],'rank':book['rank'],'category':category,'updated':book.get('last_seen',entry.get('updated')),'stale':bool(entry.get('error'))})
    limits={'new':None,'charts':10};selected={}
    for category,limit in limits.items():
        if category=='new':
            ranked=sorted(((key,b) for key,b in books.items() if b['_new_publication']),
                key=lambda pair:(-datetime.fromisoformat(pair[1]['_new_publication']['published']).toordinal(),book_identity(pair[1]),pair[1]['url']))
        else:
            ranked=sorted(((key,b) for key,b in books.items() if category in b['categories']),
                key=lambda pair:(-sum(pair[1]['_scores'][category].values()),min(s['rank'] for s in pair[1]['sources'] if s['category']==category),pair[1]['title']))
        for rank,(key,book) in enumerate(ranked[:limit],1):
            item=selected.setdefault(key,{**{k:v for k,v in book.items() if k not in ('_scores','_new_publication')},'categories':[],'ranks':{}})
            if category=='new':item.update(book['_new_publication'])
            item['categories'].append(category);item['ranks'][category]=rank
    return dict(books=list(selected.values()),sources=reports,refreshing=busy,next_run=due,timezone='Europe/Rome',limits=limits)


def refresh_sources(sources):
    def load(source):
        try:
            for attempt in range(2):
                try:
                    url=source['url'];parts=urlsplit(url)
                    if source['category']=='new' and source_name(url) in ('IBS','Feltrinelli'):
                        query=dict(parse_qsl(parts.query));query['sortPublication_date']='desc';query['Products_per_page']='100'
                        url=urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(query),''))
                    content,_mime,base=fetch(url);break
                except (OSError,http.client.HTTPException):
                    if attempt:raise
                    time.sleep(1)
            books=parse_books(content,base,100 if source['category']=='new' else 10)
            if not books:raise ValueError('Nessun libro trovato. Usa una classifica compatibile o un feed RSS/Atom.')
            return source,books[:100 if source['category']=='new' else 10],''
        except Exception as exc:
            message=str(exc) if isinstance(exc,ValueError) else 'Fonte non raggiungibile. Riprova più tardi.'
            return source,[],message
    # Only primitive snapshots cross threads. Each DB write remains in the account context.
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for source,books,error in pool.map(load,sources):
            with c.transaction() as db:
                current=read_json(db,'recommendation_sources',[])
                if not any(s['id']==source['id'] and s['url']==source['url'] and s['enabled'] for s in current):continue
                cache=read_json(db,'recommendation_cache',{});old=cache.get(source['id'],{})
                if not error:
                    previous={book['url']:book for book in old.get('books',[])}
                    for book in books:
                        book['last_seen']=time.time()
                        saved=previous.get(book['url'],{})
                        for field in METADATA_FIELDS:
                            if saved.get('metadata_updated') and saved.get(field) is not None:book[field]=saved[field]
                    if source['category']=='new':
                        incoming={book['url']:book for book in books}
                        for book in old.get('books',[]):incoming.setdefault(book['url'],book)
                        books=list(incoming.values())
                entry={'url':source['url'],'books':books if not error else old.get('books',[]),'updated':old.get('updated') if error else time.time(),'checked':time.time(),'error':error}
                cache[source['id']]=entry;c.put(db,'recommendation_cache',json.dumps(cache))
    enrich_cached()
    prune_release_history()


def prune_release_history():
    today=datetime.now(ROME).date()
    first_day=(today-timedelta(days=1)).isoformat()
    last_day=today.isoformat()
    with c.transaction() as db:
        sources=read_json(db,'recommendation_sources',[]);cache=read_json(db,'recommendation_cache',{})
        for source in sources:
            if source['category']!='new' or source['id'] not in cache:continue
            books=cache[source['id']].get('books',[])
            released=[];pending=[]
            for book in books:
                date=publication_date(book.get('published'))
                if len(date)==10 and date<=last_day:
                    released.append((date,book))
                else:
                    pending.append(book)
            released.sort(key=lambda pair:(-datetime.fromisoformat(pair[0]).toordinal(),book_identity(pair[1]),pair[1]['url']))
            retained=[];identities=set();aliases=identity_groups(books)
            for date,book in released:
                if len(retained)>=50 and date<first_day:break
                identity=aliases['title:'+book_identity(book)]
                if identity in identities:continue
                retained.append(book);identities.add(identity)
            cache[source['id']]['books']=retained+pending[:50]
        c.put(db,'recommendation_cache',json.dumps(cache))


def start_refresh(account,scheduled=False):
    now=time.time()
    with c.transaction() as db:
        initialize(db)
        if scheduled and read_json(db,'recommendation_next',now+1)>now:return False
        if read_json(db,'recommendation_busy',0)>now or read_json(db,'recommendation_last_start',0)>now-60:return False
        sources=[s for s in read_json(db,'recommendation_sources',[]) if s['enabled']]
        c.put(db,'recommendation_next',next_run(now))
        if not sources:return False
        c.put(db,'recommendation_busy',now+300);c.put(db,'recommendation_last_start',now)
    def run():
        with accounts.access(account) as exists:
            if not exists:return
            try:refresh_sources(sources)
            finally:
                with c.transaction() as db:c.put(db,'recommendation_busy',0)
    threading.Thread(target=run,name='book-recommendations',daemon=True).start()
    return True


def cover_path(digest):
    if not re.fullmatch('[a-f0-9]{64}',digest):return None
    with contextlib.closing(c.connect()) as db:
        sources=read_json(db,'recommendation_sources',[]);cache=read_json(db,'recommendation_cache',{})
    remote=None
    for source in sources:
        if not source['enabled']:continue
        for book in cache.get(source['id'],{}).get('books',[]):
            if book['cover'] and hashlib.sha256(book['cover'].encode()).hexdigest()==digest:remote=book['cover'];break
    if not remote:return None
    return cached_cover(remote,'recommendation-covers')


def cached_cover(remote,directory):
    digest=hashlib.sha256(remote.encode()).hexdigest()
    folder=c.data_dir()/directory;folder.mkdir(exist_ok=True)
    path=folder/(digest+'.webp')
    if path.exists():return path
    try:
        data,_,_=fetch(remote)
        with Image.open(io.BytesIO(data),formats=['JPEG','PNG','WEBP','GIF','AVIF']) as image:
            if image.width*image.height>16000000:return None
            image.thumbnail((180,270));converted=image.convert('RGB');output=io.BytesIO();converted.save(output,format='WEBP',quality=80)
        # Atomic replacement prevents concurrent image requests seeing partial data.
        import tempfile
        with tempfile.NamedTemporaryFile(dir=folder,delete=False) as temp:
            temp.write(output.getvalue());temp_path=temp.name
        from pathlib import Path
        Path(temp_path).replace(path)
        return path
    except Exception:return None
