'use strict';
(() => {
 const el=id=>document.getElementById(id),T=(s,v)=>window.KindleI18n.t(s,v),E=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const request=(path,body)=>window.KindleAccounts.request(path,body);
 let data=null,category='new',timer=null,loading=false,sourceDirty=false,telegram=null,downloadToken=0;
 const names={new:'Novità',charts:'Classifica generale'};
 const date=value=>value?new Date(value*1000).toLocaleString(window.KindleI18n.locale,{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}):T('Mai aggiornata');
 function notice(id,message){el(id).textContent=T(message);el(id).hidden=!message;}
 function publication(value){
  if(!value)return T('Data di uscita non disponibile');
  if(/^\d{4}$/.test(value))return T('Anno di uscita: {year}',{year:value});
  const date=new Date(value.length===7?value+'-01T12:00:00':value+'T12:00:00');
  return T('Uscita: {date}',{date:date.toLocaleDateString(window.KindleI18n.locale,value.length===7?{month:'long',year:'numeric'}:{day:'numeric',month:'short',year:'numeric'})});
 }
 function metadata(book){
  const score=typeof book.rating_value==='number'?book.rating_value.toLocaleString(window.KindleI18n.locale,{maximumFractionDigits:2}):null;
  const count=Number.isInteger(book.rating_count)?T('{count} valutazioni',{count:book.rating_count.toLocaleString(window.KindleI18n.locale)}):'';
  return `<div class="recommendation-metadata"><div class="recommendation-rating">${score?`<span class="rating-score" aria-label="${E(T('Valutazione generale: {score} su 5',{score}))}">★ ${E(score)}<small>/5</small></span><a class="rating-source" href="${E(book.rating_url||book.url)}" target="_blank" rel="noopener noreferrer" data-untranslated>${E(book.rating_source||'')}${count?' · '+E(count):''} ↗</a>`:`<span class="small muted">${E(T('Valutazione non ancora disponibile'))}</span>`}</div><p class="recommendation-publication">${book.published?`<a href="${E(book.published_url||book.url)}" target="_blank" rel="noopener noreferrer">${E(publication(book.published))}</a>`:E(publication(''))}</p></div>`;
 }
 function render(){
  if(!data)return;
  const books=data.books.filter(b=>b.categories.includes(category)).sort((a,b)=>(a.ranks?.[category]||0)-(b.ranks?.[category]||0));
  el('recommendation-count').textContent=T('{shown} libri · massimo {total}',{shown:books.length,total:data.limits[category]})+(category==='new'?' · '+T('Dal più recente al meno recente'):'');
  el('recommendation-refresh').disabled=data.refreshing;
  const active=data.sources.filter(s=>s.enabled),errors=active.filter(s=>s.error);
  notice('recommendation-status',data.refreshing?'Aggiornamento delle fonti in corso…':!active.length?'Attiva o aggiungi una fonte nelle impostazioni.':errors.length?'Alcune fonti non sono disponibili. Gli ultimi risultati restano visibili con la loro data.':'');
  el('recommendation-schedule').textContent=T('Prossima scansione: {date} · Europe/Rome',{date:new Date(data.next_run*1000).toLocaleString(window.KindleI18n.locale,{weekday:'long',day:'numeric',month:'short',hour:'2-digit',minute:'2-digit',timeZone:'Europe/Rome'})});
  el('recommendation-books').innerHTML=window.KindleI18n.html(books.map((b,index)=>`<article class="card recommendation-card"><div class="recommendation-cover"><img class="book-cover" src="${E(b.cover_url||'/static/cover-placeholder.svg')}" alt="${E(b.title)}" loading="lazy" width="90" height="135"><span class="recommendation-rank">${index+1}</span></div><div class="recommendation-info"><h2 class="file-title" data-untranslated>${E(b.title)}</h2><p class="small muted" data-untranslated>${E(b.author)}</p>${metadata(b)}<details class="recommendation-evidence"><summary>${E(T('Fonti del libro ({count})',{count:b.sources.length}))}</summary><div class="recommendation-citations">${b.sources.map(s=>`<a href="${E(s.url)}" target="_blank" rel="noopener noreferrer" data-untranslated>${E(s.name)} · #${s.rank} ↗</a><time class="small muted">${E(T('Aggiornato: {date}',{date:date(s.updated)}))}${s.stale?' · '+E(T('Ultimi dati disponibili')):''}</time>`).join('')}</div></details><div class="book-actions"><a class="recommendation-link" href="${E(b.url)}" target="_blank" rel="noopener noreferrer">Vedi il libro ↗</a><button class="primary" data-recommendation-download="${index}">Scarica</button>${readingListButton('recommendation',b.url,savedRecommendation(b))}</div></div></article>`).join('')||'<div class="empty"><strong>Nessun titolo disponibile.</strong>Attiva le fonti o premi Aggiorna ora per cercare nuovi libri.</div>');
  if(!sourceDirty)renderSources();
 }
 function row(source={name:'',url:'',category:'charts',enabled:true}){
  const node=document.createElement('li');node.dataset.sourceId=source.id||'';
  const details=document.createElement('details');details.open=!source.id;
  const note=source.enabled?(source.error||(!source.checked?'In attesa di aggiornamento':'{count} libri')):'Disattivata';
  const summary=`<summary><strong data-untranslated>${E(source.name||T('Nuova fonte'))}</strong><span class="small muted">${E(T(note,{count:source.count}))}${source.updated?' · '+E(date(source.updated)):''}</span></summary>`;
  details.innerHTML=window.KindleI18n.html(summary+`<fieldset class="recommendation-source-row"><legend class="sr-only">${E(T('Fonte'))}</legend><label>Nome della fonte<input data-source-name maxlength="100" value="${E(source.name)}" required></label><label>Indirizzo della classifica o del feed<input data-source-url type="url" placeholder="https://…" value="${E(source.url)}" maxlength="2048" required></label><div class="form-pair"><label>Categoria<select data-source-category>${Object.entries(names).map(([id,label])=>`<option value="${id}" ${id===(source.category==='popular'?'charts':source.category)?'selected':''}>${label}</option>`).join('')}</select></label><label class="check-line"><input data-source-enabled type="checkbox" ${source.enabled?'checked':''}><span>Attiva</span></label></div><button class="text source-remove" type="button">Rimuovi fonte</button></fieldset>`);
  node.append(details);el('recommendation-sources').append(node);
 }
 function renderSources(){
  const list=el('recommendation-sources');
  const signature=JSON.stringify([data.sources,window.KindleI18n.language]);
  if(list.dataset.signature===signature)return;
  const open=new Set([...list.querySelectorAll('details[open]')].map(node=>node.parentElement.dataset.sourceId));
  list.replaceChildren();data.sources.forEach(row);
  for(const item of list.children)if(open.has(item.dataset.sourceId))item.firstElementChild.open=true;
  list.dataset.signature=signature;
 }
 function poll(){clearTimeout(timer);if(data?.refreshing)timer=setTimeout(()=>load(false),2000);}
 async function load(first=true){
  if(loading||!window.KindleAccounts.active)return;loading=true;
  try{data=await request('/api/recommendations');render();poll();
   if(first&&data.sources.some(s=>s.enabled)&&!data.sources.some(s=>s.checked)&&!data.refreshing){data=await request('/api/recommendations/refresh',{});render();poll();}
  }catch(error){if(error.name!=='AbortError')notice('recommendation-status',error.message);}finally{loading=false;}
 }
 el('recommendation-refresh').onclick=async()=>{el('recommendation-refresh').disabled=true;try{data=await request('/api/recommendations/refresh',{});render();poll();if(!data.refreshing)window.toast('Attendi un minuto tra due aggiornamenti.');}catch(error){notice('recommendation-status',error.message);el('recommendation-refresh').disabled=false;}};
 el('recommendation-settings').onclick=()=>{location.hash='settings';setTimeout(()=>el('recommendation-source-settings').scrollIntoView({block:'start',behavior:'smooth'}),0);};
 el('recommendation-source-add').onclick=()=>{if(el('recommendation-sources').children.length>=12){notice('recommendation-sources-error','Puoi aggiungere fino a 12 fonti.');return;}sourceDirty=true;row();el('recommendation-sources').lastElementChild.querySelector('input').focus();};
 el('recommendation-sources-form').addEventListener('input',()=>sourceDirty=true);
 el('recommendation-sources-form').addEventListener('click',event=>{if(event.target.closest('.source-remove')){sourceDirty=true;event.target.closest('li').remove();}});
 el('recommendation-sources-form').onsubmit=async event=>{
  event.preventDefault();const button=event.submitter;button.disabled=true;notice('recommendation-sources-error','');
  const sources=[...el('recommendation-sources').children].map(node=>({name:node.querySelector('[data-source-name]').value,url:node.querySelector('[data-source-url]').value,category:node.querySelector('[data-source-category]').value,enabled:node.querySelector('[data-source-enabled]').checked}));
  try{data=await request('/api/recommendations/sources',{sources});sourceDirty=false;el('recommendation-sources').dataset.signature='';render();window.toast('Fonti salvate.');}catch(error){notice('recommendation-sources-error',error.message);}finally{button.disabled=false;}
 };
 document.addEventListener('click',event=>{const tab=event.target.closest('[data-recommendation-category]');if(tab){category=tab.dataset.recommendationCategory;document.querySelectorAll('[data-recommendation-category]').forEach(b=>{b.classList.toggle('active',b===tab);b.setAttribute('aria-pressed',String(b===tab));});render();}const button=event.target.closest('[data-recommendation-download]');if(button)download(data.books.filter(b=>b.categories.includes(category)).sort((a,b)=>(a.ranks?.[category]||0)-(b.ranks?.[category]||0))[Number(button.dataset.recommendationDownload)]);});
 let searchBusy=false,saving=false,searchGeneration=0,searchOptions=[];
 let downloadLanguageDirty=false,lastDownloadLanguage=null,downloadFolderDirty=false;
 function effectiveDownloadLanguage(){return (telegram?.language_preference??'app')==='app'?window.KindleI18n.language:(telegram.language_preference||telegram.language);}
 function languageName(code){return T(({it:'Italiano',en:'Inglese',fr:'Francese',es:'Spagnolo',zh:'Cinese',ru:'Russo'})[code]||code);}
 function renderDownloadLanguage(){
  const language=effectiveDownloadLanguage();
  if(lastDownloadLanguage!==null&&lastDownloadLanguage!==language){
   searchGeneration++;searchBusy=false;saving=false;searchOptions=[];el('downloads-results').replaceChildren();notice('downloads-status','');downloadToken++;el('download-dialog').close();
  }
  lastDownloadLanguage=language;
  if(!downloadLanguageDirty)el('download-language-select').value=telegram?.language_preference||'app';
  const summary=T('Lingua dei download: {language}',{language:languageName(language)});
  el('download-language-summary').textContent=summary;el('downloads-language').textContent=summary;searchControls();
 }
 el('download-language-select').onchange=()=>downloadLanguageDirty=true;
 el('download-language-form').onsubmit=async event=>{
  event.preventDefault();const button=event.submitter;button.disabled=true;notice('download-language-error','');
  try{telegram=await request('/api/downloads/preferences',{language:el('download-language-select').value});downloadLanguageDirty=false;renderTelegram();window.toast('Lingua dei download salvata.');}
  catch(error){if(error.name!=='AbortError')notice('download-language-error',error.message);}
  finally{button.disabled=false;}
 };
 el('downloads-language-edit').onclick=()=>{location.hash='settings';setTimeout(()=>el('download-language-settings').scrollIntoView({block:'start',behavior:'smooth'}),0);};
 function renderDownloadFolder(){
  if(!downloadFolderDirty)el('download-folder-input').value=telegram.folder||'';
  const summary=T('Destinazione: {folder}',{folder:telegram.destination});
  el('download-folder-summary').textContent=summary;
  el('downloads-folder').textContent=summary;
 }
 el('download-folder-input').oninput=()=>downloadFolderDirty=true;
 el('download-folder-form').onsubmit=async event=>{
  event.preventDefault();const button=event.submitter;button.disabled=true;notice('download-folder-error','');
  try{telegram=await request('/api/downloads/folder',{folder:el('download-folder-input').value});downloadFolderDirty=false;renderTelegram();window.toast('Cartella dei download salvata.');}
  catch(error){if(error.name!=='AbortError')notice('download-folder-error',error.message);}
  finally{button.disabled=false;}
 };
 el('downloads-folder-edit').onclick=()=>{location.hash='settings';setTimeout(()=>el('download-folder-settings').scrollIntoView({block:'start',behavior:'smooth'}),0);};
 function downloadMessage(saved){
  if(!saved.destination||saved.destination==='Libri/Singoli')return T('Salvato in Libri/Singoli: {name}',{name:saved.name});
  return T('Salvato in {folder}: {name}',{folder:saved.destination,name:saved.name});
 }
 function searchControls(){
  el('downloads-search-submit').disabled=searchBusy||saving||!telegram?.connected;
  el('downloads-query').disabled=searchBusy||saving;
  document.querySelectorAll('[data-search-download]').forEach(button=>button.disabled=searchBusy||saving||button.dataset.saved==='true');
 }
 function renderSearchOptions(){
  el('downloads-results').replaceChildren();
  searchOptions.forEach((option,index)=>{
   const card=document.createElement('article');card.className='card download-result-card';
   const info=document.createElement('div');info.className='download-result-info';
   const label=document.createElement('h2');label.className='download-result-label';label.setAttribute('data-untranslated','');label.textContent=option.label;
   const meta=document.createElement('p');meta.className='small muted';meta.textContent=option.format.toUpperCase()+' · '+languageName(option.language||effectiveDownloadLanguage())+' · '+(option.size?new Intl.NumberFormat(window.KindleI18n.locale,{maximumFractionDigits:2}).format(option.size/1048576)+' MB':T('Dimensione non indicata'));
   const button=document.createElement('button');button.className='primary';button.dataset.searchDownload=String(index);button.textContent=T('Scarica {format}',{format:option.format.toUpperCase()});button.onclick=()=>saveSearchOption(option,button);
   info.append(label,meta);card.append(info,button);el('downloads-results').append(card);
  });searchControls();
 }
 async function saveSearchOption(option,button){
  if(saving||searchBusy)return;
  const generation=searchGeneration,accountEpoch=window.KindleAccounts.epoch;saving=true;searchControls();notice('downloads-status','Download in corso…');
  try{
   const saved=await request('/api/telegram/download',{token:option.token});
   if(generation!==searchGeneration||accountEpoch!==window.KindleAccounts.epoch)return;
   button.dataset.saved='true';button.textContent=T('Scaricato');el('downloads-status').textContent=downloadMessage(saved);window.toast('Libro scaricato.');await refresh();
  }catch(error){if(generation===searchGeneration&&accountEpoch===window.KindleAccounts.epoch&&error.name!=='AbortError')notice('downloads-status',error.message);}
  finally{if(generation===searchGeneration){saving=false;searchControls();}}
 }
 el('downloads-connect').onclick=()=>{location.hash='settings';setTimeout(()=>el('telegram-settings').scrollIntoView({block:'start',behavior:'smooth'}),0);};
 el('downloads-search-form').onsubmit=async event=>{
  event.preventDefault();if(searchBusy||saving)return;
  if(!telegram?.connected){notice('downloads-status','Collega Telegram per scaricare i libri.');return;}
  const query=el('downloads-query').value.trim();if(!query)return;
  const generation=++searchGeneration,accountEpoch=window.KindleAccounts.epoch;searchBusy=true;searchControls();notice('downloads-status','Ricerca delle edizioni nella lingua scelta…');
  try{
   const result=await request('/api/telegram/search',{query});
   if(generation!==searchGeneration||accountEpoch!==window.KindleAccounts.epoch)return;
   searchOptions=result.options;renderSearchOptions();notice('downloads-status',result.options.length?T('Edizioni trovate: {count}. Scegli il libro da scaricare.',{count:result.options.length}):'Nessuna edizione nella lingua scelta. Prova un altro titolo, autore o ISBN.');
  }catch(error){if(generation===searchGeneration&&accountEpoch===window.KindleAccounts.epoch&&error.name!=='AbortError')notice('downloads-status',error.message);}
  finally{if(generation===searchGeneration){searchBusy=false;searchControls();}}
 };
 async function loadTelegram(){try{telegram=await request('/api/telegram');renderTelegram();}catch(error){if(error.name!=='AbortError'){notice('telegram-error',error.message);if(!el('page-downloads').hidden)notice('downloads-status',error.message);}}}
 function renderTelegram(){if(!telegram)return;renderDownloadLanguage();renderDownloadFolder();el('downloads-connection').textContent=T(telegram.connected?'Telegram collegato. Cerca il libro che vuoi scaricare.':'Collega Telegram per cercare e scaricare i libri.');el('downloads-connect').hidden=telegram.connected;searchControls();el('telegram-status').textContent=T(telegram.connected?'Telegram collegato. Download pronti.':'Telegram da collegare.');el('telegram-connect-form').hidden=telegram.connected;el('telegram-disconnect').hidden=!telegram.configured;el('telegram-code-form').hidden=!telegram.pending;el('download-bot-link').href='https://t.me/'+telegram.bot;if(telegram.bot&&!el('telegram-connect-form').matches(':focus-within'))el('telegram-connect-form').elements.bot.value=telegram.bot;if(telegram.password_required)notice('telegram-error','Inserisci la password di verifica in due passaggi di Telegram.');}
 for(const [id,path] of [['telegram-connect-form','connect'],['telegram-code-form','verify']])el(id).onsubmit=async event=>{event.preventDefault();const button=event.submitter;button.disabled=true;notice('telegram-error','');try{telegram=await request('/api/telegram/'+path,Object.fromEntries(new FormData(event.target)));event.target.reset();renderTelegram();}catch(error){notice('telegram-error',error.message);}finally{button.disabled=false;}};
 el('telegram-disconnect').onclick=async()=>{try{telegram=await request('/api/telegram/disconnect',{});renderTelegram();el('telegram-code-form').reset();notice('telegram-error','');}catch(error){notice('telegram-error',error.message);}};
 el('download-close').onclick=()=>el('download-dialog').close();el('download-dialog').addEventListener('close',()=>downloadToken++);
 async function download(book,savedId=savedRecommendation(book||{},true)){
  if(!book)return;
  const accountEpoch=window.KindleAccounts.epoch;await loadTelegram();if(!window.KindleAccounts.active||accountEpoch!==window.KindleAccounts.epoch)return;
  if(!telegram?.connected){location.hash='settings';window.toast('Collega Telegram per scaricare i libri.');setTimeout(()=>el('telegram-settings').scrollIntoView({block:'start',behavior:'smooth'}),0);return;}
  const token=++downloadToken;el('download-title').textContent=book.title+' · '+book.author;el('download-options').replaceChildren();el('download-status').textContent=T('Ricerca delle edizioni nella lingua scelta…');el('download-dialog').showModal();
  try{const result=await request('/api/telegram/search',savedId?{reading_list_id:savedId}:{url:book.url});if(token!==downloadToken)return;el('download-status').textContent=T(result.options.length?'Edizione selezionata.':'Il bot non ha restituito un’edizione riconoscibile nella lingua scelta. Aprilo in Telegram.');
   if(result.options.length){const option=result.options[0];el('download-status').textContent=T('Download in corso…')+' '+option.format.toUpperCase()+(option.size?' · '+(option.size/1048576).toFixed(2)+' MB':'');const saved=await request('/api/telegram/download',{token:option.token});if(token!==downloadToken)return;el('download-status').textContent=downloadMessage(saved);window.toast('Libro scaricato.');await refresh();}
  }catch(error){if(token===downloadToken)el('download-status').textContent=T(error.message);}
 }
 window.addEventListener('kindle:saved-download',event=>download(event.detail,event.detail.id));
 window.addEventListener('kindle:reading-list',()=>{
  const focused=document.activeElement;
  const key=focused?.dataset.readingListKey;
  const restore=key&&el('recommendation-books').contains(focused);
  render();
  if(restore)el('recommendation-books').querySelector(`[data-reading-list-key="${CSS.escape(key)}"]`)?.focus();
 });
 window.addEventListener('kindle:page',event=>{if(['recommended','settings'].includes(event.detail.page))load();if(['downloads','settings'].includes(event.detail.page))loadTelegram();});
 window.addEventListener('kindle:account',()=>{clearTimeout(timer);data=null;telegram=null;loading=false;sourceDirty=false;downloadToken++;downloadLanguageDirty=false;lastDownloadLanguage=null;downloadFolderDirty=false;el('download-folder-form').reset();notice('download-folder-error','');el('download-folder-summary').textContent='';el('downloads-folder').textContent='';el('download-language-form').reset();notice('download-language-error','');el('download-language-summary').textContent='';el('downloads-language').textContent='';searchGeneration++;searchBusy=false;saving=false;searchOptions=[];el('downloads-search-form').reset();el('downloads-results').replaceChildren();notice('downloads-status','');el('downloads-connection').textContent=T('Telegram da collegare.');el('downloads-connect').hidden=false;searchControls();el('recommendation-books').replaceChildren();el('recommendation-sources').replaceChildren();el('recommendation-sources').dataset.signature='';el('telegram-connect-form').reset();el('telegram-code-form').reset();el('download-dialog').close();for(const id of ['telegram-error','recommendation-sources-error','recommendation-status'])notice(id,'');if(window.KindleAccounts.active)load();});
 window.addEventListener('kindle:language',()=>{render();renderTelegram();if(window.KindleAccounts.active)loadTelegram();});
})();
