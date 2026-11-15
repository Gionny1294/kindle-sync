'use strict';
(() => {
 const languages=[['it','Italiano','it-IT'],['en','English','en-GB'],['fr','Français','fr-FR'],['es','Español','es-ES'],['zh','中文（简体）','zh-CN'],['ru','Русский','ru-RU']];
 const supported=new Set(languages.map(([code])=>code)),catalogs=new Map();let language='it',catalog={},patterns=[],reverse=[],changing=false,started=false,resolveReady;
 const assetVersion=new URL(document.currentScript.src).searchParams.get('v')||'dev';
 const ready=new Promise(resolve=>resolveReady=resolve),protectedUI='#reader-content,#reader-measure,#reader-pdf-text,.file-title,[data-untranslated]';
 const escapeRegex=s=>s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
 function pattern(source){const names=[];let last=0,regex='^';for(const match of source.matchAll(/\{(\w+)\}/g)){regex+=escapeRegex(source.slice(last,match.index))+(['number','page','total','count','shown','minutes','progress','checked','new','queued','uncertain','invalid'].includes(match[1])?'([0-9]+(?:[.,][0-9]+)?)':'([\\s\\S]+?)');names.push(match[1]);last=match.index+match[0].length;}if(!names.length)return null;return {regex:new RegExp(regex+escapeRegex(source.slice(last))+'$'),names};}
 function render(source,values){const translated=catalog[source]??source;const text=typeof translated==='object'?(translated[new Intl.PluralRules(locale()).select(Number(values.count))]||translated.other):translated;return String(text).replace(/\{(\w+)\}/g,(match,key)=>values[key]??match);}
 function locale(){return languages.find(([code])=>code===language)[2];}
 function t(message,values,depth=0){
  if(message===null||message===undefined)return '';message=String(message);if(depth>5)return message;
  if(values)return render(message,values);
  if(Object.hasOwn(catalog,message))return render(message,{});
  for(const [translated,source,p] of reverse){if(!p){if(message===translated)return render(source,{});}else{const match=message.match(p.regex);if(match)return render(source,Object.fromEntries(p.names.map((name,i)=>[name,match[i+1]])));}}
  for(const [source,p] of patterns){const match=message.match(p.regex);if(match)return render(source,Object.fromEntries(p.names.map((name,i)=>[name,match[i+1]])));}
  for(const [source,p] of patterns){if(!/[.!?]$/.test(source))continue;const match=message.match(new RegExp(p.regex.source.slice(0,-1)+'(?=\\s|$)'));if(match&&match[0].length<message.length)return render(source,Object.fromEntries(p.names.map((name,i)=>[name,match[i+1]])))+' '+t(message.slice(match[0].length).trimStart(),undefined,depth+1);}
  for(const source of Object.keys(catalog).sort((a,b)=>b.length-a.length))if(/[.!?]$/.test(source)&&message.startsWith(source+' '))return render(source,{})+' '+t(message.slice(source.length+1),undefined,depth+1);
  if(message.includes(' · '))return message.split(' · ').map(part=>t(part,undefined,depth+1)).join(' · ');
  const split=message.indexOf('. ');if(split>=0)return t(message.slice(0,split+1),undefined,depth+1)+' '+t(message.slice(split+2),undefined,depth+1);
  return message;
 }
 function translateNode(node,source){const leading=source.match(/^\s*/)[0],trailing=source.match(/\s*$/)[0],text=source.trim();node.nodeValue=text?leading+t(text)+trailing:source;}
 function translatable(node){return node.parentElement&&!node.parentElement.closest('script,style,#language-welcome,[data-language-options],'+protectedUI);}
 const originals=[],attributes=[];
 const walker=document.createTreeWalker(document.documentElement,NodeFilter.SHOW_TEXT);let node;
 while((node=walker.nextNode()))if(translatable(node)&&node.nodeValue.trim())originals.push([node,node.nodeValue]);
 for(const element of document.querySelectorAll('[placeholder],[aria-label],meta[name=description]'))for(const name of ['placeholder','aria-label','content'])if(element.hasAttribute(name)&&!element.closest(protectedUI))attributes.push([element,name,element.getAttribute(name)]);
 function html(markup){
  const template=document.createElement('template');template.innerHTML=String(markup);
  const walker=document.createTreeWalker(template.content,NodeFilter.SHOW_TEXT);let node;
  while((node=walker.nextNode()))if(node.parentElement&&!node.parentElement.closest(protectedUI+',.file-meta'))translateNode(node,node.nodeValue);
  for(const element of template.content.querySelectorAll('[alt],[aria-label],[title]'))for(const name of ['alt','aria-label','title'])if(element.hasAttribute(name)&&!element.closest('[data-untranslated]'))element.setAttribute(name,t(element.getAttribute(name)));
  return template.innerHTML;
 }
 function apply(){
  document.documentElement.lang=language==='zh'?'zh-CN':language;
  for(const [node,source] of originals)if(node.isConnected)translateNode(node,source);
  for(const [element,name,source] of attributes)if(element.isConnected)element.setAttribute(name,t(source));
  for(const button of document.querySelectorAll('[data-language-code]')){const active=button.dataset.languageCode===language;button.setAttribute('aria-pressed',String(active));button.classList.toggle('chosen',active);button.querySelector('.language-check').textContent=active?'✓':'';}
 }
 function finish(){document.body.classList.remove('language-pending');document.getElementById('language-welcome').hidden=true;document.getElementById('language-loading').hidden=true;document.getElementById('shell').inert=false;if(!started){started=true;resolveReady();}}
 async function setLanguage(code){
  if(!supported.has(code)||changing)return;changing=true;
  for(const button of document.querySelectorAll('[data-language-code]'))button.disabled=true;
  try{
   if(!catalogs.has(code)){const response=await fetch('/static/locales/'+code+'.json?v='+assetVersion,{cache:'no-cache'});if(!response.ok)throw new Error();catalogs.set(code,await response.json());}
   language=code;catalog=catalogs.get(code);patterns=Object.keys(catalog).map(source=>[source,pattern(source)]).filter(([,p])=>p);reverse=[];
   for(const [other,entries] of catalogs)if(other!==code)for(const [source,translated] of Object.entries(entries)){if(typeof translated==='string'&&source!==translated)reverse.push([translated,source,pattern(translated)]);}
   try{localStorage.setItem('kindle-language',code);}catch{}
   document.cookie='kindle_language='+code+'; Path=/; Max-Age=31536000; SameSite=Lax';
   document.getElementById('language-error').hidden=true;document.getElementById('language-settings-error').hidden=true;apply();finish();window.dispatchEvent(new CustomEvent('kindle:language',{detail:{language:code}}));
  }catch{
   if(!started){document.getElementById('language-loading').hidden=true;document.getElementById('language-welcome').hidden=false;}
   const error=document.getElementById(started?'language-settings-error':'language-error');error.textContent=t('Impossibile caricare la lingua. Controlla la connessione e riprova.');error.hidden=false;
  }finally{changing=false;for(const button of document.querySelectorAll('[data-language-code]'))button.disabled=false;}
 }
 for(const container of document.querySelectorAll('[data-language-options]'))for(const [code,name] of languages){
  const button=document.createElement('button');button.type='button';button.className='language-option';button.dataset.languageCode=code;button.lang=code==='zh'?'zh-CN':code;button.setAttribute('aria-pressed','false');
  const flag=document.createElement('img');flag.src='/static/flags/'+code+'.svg';flag.alt='';flag.width=45;flag.height=30;
  const label=document.createElement('span');label.textContent=name;const check=document.createElement('span');check.className='language-check';check.setAttribute('aria-hidden','true');button.append(flag,label,check);container.append(button);
 }
 document.addEventListener('click',event=>{const button=event.target.closest('[data-language-code]');if(button)setLanguage(button.dataset.languageCode);});
 window.addEventListener('storage',event=>{if(event.key==='kindle-language'&&supported.has(event.newValue)&&event.newValue!==language)setLanguage(event.newValue);});
 window.KindleI18n={t,html,ready,setLanguage,get language(){return language;},get locale(){return locale();}};
 document.getElementById('shell').inert=true;let saved;
 try{saved=localStorage.getItem('kindle-language');}catch{}
 if(!supported.has(saved))saved=document.cookie.split('; ').find(value=>value.startsWith('kindle_language='))?.split('=')[1];
 if(supported.has(saved))setLanguage(saved);else{document.getElementById('language-loading').hidden=true;document.getElementById('language-welcome').hidden=false;document.querySelector('#language-welcome [data-language-code]').focus();}
})();
