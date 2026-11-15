"""Translate response messages per browser without changing the persistent ledger."""
from functools import lru_cache
import json
from pathlib import Path
import re
from flask import request

LANGUAGES=('it','en','fr','es','zh','ru')
LOCALES=Path(__file__).parent/'static'/'locales'


def language():
    saved=request.cookies.get('kindle_language')
    return saved if saved in LANGUAGES else request.accept_languages.best_match(LANGUAGES,default='it')


@lru_cache(maxsize=6)
def catalog(code):
    return json.loads((LOCALES/(code+'.json')).read_text())


@lru_cache(maxsize=6)
def patterns(code):
    result=[]
    for source in catalog(code):
        names=re.findall(r'\{(\w+)\}',source)
        if not names:continue
        parts=re.split(r'\{\w+\}',source)
        expression=re.escape(parts[0])
        numeric={'number','page','total','count','shown','minutes','progress','checked','new','queued','uncertain','invalid'}
        for name,part in zip(names,parts[1:]):expression+=(r'([0-9]+(?:[.,][0-9]+)?)' if name in numeric else r'(.+?)')+re.escape(part)
        result.append((re.compile('^'+expression+'$',re.S),source,names))
    return result


def translate(message,code,depth=0):
    if not isinstance(message,str) or depth>5:return message
    entries=catalog(code)
    if message in entries:return entries[message]
    for regex,source,names in patterns(code):
        match=regex.fullmatch(message)
        if match:
            values=dict(zip(names,match.groups()))
            return re.sub(r'\{(\w+)\}',lambda m:values.get(m[1],m[0]),entries[source])
    for regex,source,names in patterns(code):
        if not source.endswith(('.','!','?')):continue
        match=re.match(regex.pattern[:-1]+r'(?=\s|$)',message,re.S)
        if match and len(match[0])<len(message):
            values=dict(zip(names,match.groups()))
            prefix=re.sub(r'\{(\w+)\}',lambda m:values.get(m[1],m[0]),entries[source])
            return prefix+' '+translate(message[len(match[0]):].lstrip(),code,depth+1)
    for source in sorted(entries,key=len,reverse=True):
        if source.endswith(('.','!','?')) and message.startswith(source+' '):return entries[source]+' '+translate(message[len(source)+1:],code,depth+1)
    if '. ' in message:
        first,rest=message.split('. ',1)
        return translate(first+'.',code,depth+1)+' '+translate(rest,code,depth+1)
    return message


def response_messages(data,code):
    if isinstance(data,list):return [response_messages(item,code) for item in data]
    if isinstance(data,dict):return {key:translate(value,code) if key in {'error','message','detail'} and isinstance(value,str)
        else response_messages(value,code) for key,value in data.items()}
    return data
