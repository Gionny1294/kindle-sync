"""Match the community rating from existing Goodreads metadata or an imported CSV."""
import math
import re
from urllib.parse import urlsplit

RATING_KEYS=('rating_value','rating_count','rating_source','rating_url')
HOSTS={'www.goodreads.com','goodreads.com'}


def book_url(value):
    if not isinstance(value,str) or not value:
        return ''
    try:
        parts=urlsplit(value)
        if parts.scheme!='https' or parts.netloc.lower() not in HOSTS:
            return ''
    except ValueError:
        return ''
    match=re.fullmatch(r'/book/show/([1-9]\d{0,19})(?:[.-][^/]+)?/?',parts.path,re.ASCII)
    return 'https://www.goodreads.com/book/show/'+match[1] if match else ''


def numeric_rating(value,count=None):
    try:
        score=round(float(str(value).strip().replace(',','.')),2)
    except (ValueError,TypeError,OverflowError):
        return {}
    if not math.isfinite(score) or not 0<score<=5:
        return {}
    raw=re.sub(r'[,.\s]','',str(count or ''))
    votes=int(raw) if re.fullmatch(r'\d{1,12}',raw,re.ASCII) else None
    return {'rating_value':score,'rating_count':votes or None}


def csv_rating(value, count, url):
    score=numeric_rating(value,count)
    return {**score,'rating_url':book_url(url),'rating_source':'Goodreads'} if score else {}


def extract(book):
    if book.get('rating_source')!='Goodreads':
        return {}
    score=numeric_rating(book.get('rating_value'),book.get('rating_count'))
    link=book_url(book.get('rating_url')) or book_url(book.get('url'))
    return {**score,'rating_url':link,'rating_source':'Goodreads'} if score else {}


def prefer(candidate,current):
    if not candidate or not candidate.get('rating_value'):
        return False
    if not current or not current.get('rating_value'):
        return True
    return (candidate.get('rating_source')=='Goodreads',candidate.get('rating_count') or 0) > (
        current.get('rating_source')=='Goodreads',current.get('rating_count') or 0)


def remember(db,entry_id,book):
    if rating:=extract(book):
        db.execute('''INSERT INTO reading_goodreads_ratings VALUES (?,?,?,?)
            ON CONFLICT(entry_id) DO UPDATE SET score=excluded.score,votes=excluded.votes,url=excluded.url
            WHERE excluded.votes IS NOT NULL OR reading_goodreads_ratings.votes IS NULL''',
            (entry_id,rating['rating_value'],rating['rating_count'],rating['rating_url']))


def keys(book):
    from app import reading_list, recommendations
    for url in (book.get('url'),book.get('rating_url')):
        if canonical:=book_url(url):
            yield 'url',canonical
    for value in (book.get('isbn'),*(book.get('isbns') or [])):
        if isinstance(value,str) and (isbn:=reading_list.isbn_key(value)):
            yield 'isbn',isbn
    if isinstance(book.get('title'),str) and book['title'].strip() and isinstance(book.get('author'),str) and book['author'].strip():
        yield 'title',recommendations.book_identity(book)


class Ratings:
    def __init__(self, db, cache=None):
        from app import recommendations
        self.by_key={}
        if cache is None:
            cache=recommendations.read_json(db,'recommendation_cache',{})
        for source in cache.values():
            for book in source.get('books',[]):
                self.add(book)
        rows=db.execute('''SELECT r.title,r.author,r.isbn,r.url,g.score AS rating_value,
                           g.votes AS rating_count,g.url AS rating_url
            FROM reading_goodreads_ratings g JOIN reading_list r ON r.id=g.entry_id''')
        for row in rows:
            self.add(dict(row,rating_source='Goodreads'))

    def add(self,book):
        if rating:=extract(book):
            key_id=rating['rating_url']
            for key in keys(book):
                group=self.by_key.setdefault(key,{})
                if prefer(rating,group.get(key_id)):
                    group[key_id]=rating

    def find(self,book):
        direct=extract(book)
        if direct:
            return direct
        for key in keys(book):
            group=self.by_key.get(key,{})
            # Title and author alone are ambiguous when different Goodreads works match.
            if key[0]=='title' and len({url for url in group if url})>1:
                continue
            if group:
                return max(group.values(),key=lambda item:(item['rating_count'] or 0,bool(item['rating_url'])))
        return {}
