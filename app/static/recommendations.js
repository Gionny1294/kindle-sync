'use strict';
(() => {
 const el=id=>document.getElementById(id),T=(s,v)=>window.KindleI18n.t(s,v),E=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const request=(path,body)=>window.KindleAccounts.request(path,body);
 let data=null,category='new',timer=null,loading=false,sourceDirty=false,telegram=null,downloadToken=0;
 let bookMarkup='',loadingSynopses=false;
 const synopses=new Map(),pendingSynopses=new Set();
 const synopsisObserver=new IntersectionObserver(entries=>{
  for(const entry of entries){
   if(entry.isIntersecting)pendingSynopses.add(entry.target);
   else pendingSynopses.delete(entry.target);
  }
  loadVisibleSynopses();
 },{rootMargin:'0px 0px 100px'});
 const synopsisSizeObserver=new ResizeObserver(entries=>{
  for(const {target} of entries)measureSynopsis(target);
 });
 const names={new:'Novità',charts:'Classifica'};
 const date=value=>value?new Date(value*1000).toLocaleString(window.KindleI18n.locale,{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}):T('Mai aggiornata');
 function notice(id,message){el(id).textContent=T(message);el(id).hidden=!message;}
 function measureSynopsis(box){
  if(!box.offsetWidth||box.classList.contains('is-expanded'))return;
  const text=box.querySelector('.synopsis-text'),button=box.querySelector('.synopsis-toggle');
  box.classList.add('is-measuring');
  button.hidden=text.scrollHeight<=text.clientHeight+1;
  box.classList.remove('is-measuring');
 }
 function setSynopsisExpanded(box,expanded){
  const button=box.querySelector('.synopsis-toggle');
  box.classList.toggle('is-expanded',expanded);
  button.setAttribute('aria-expanded',String(expanded));
  button.setAttribute('aria-label',T(expanded?'Chiudi trama':'Leggi la trama'));
  button.textContent=expanded?'⌃':'…';
 }
 function displaySynopsis(box,text,available){
  box.querySelector('.synopsis-text').textContent=text;
  box.classList.toggle('has-synopsis',available);
  if(available){
   synopsisSizeObserver.observe(box);
   measureSynopsis(box);
  }else{
   synopsisSizeObserver.unobserve(box);
   box.querySelector('.synopsis-toggle').hidden=true;
  }
 }
 document.addEventListener('click',event=>{
  const button=event.target.closest('.synopsis-toggle');
  if(!button)return;
  const box=button.closest('.synopsis-disclosure');
  setSynopsisExpanded(box,!box.classList.contains('is-expanded'));
  measureSynopsis(box);
 });
 function publication(value){
  if(!value)return T('Data di uscita non disponibile');
  if(/^\d{4}$/.test(value))return T('Anno di uscita: {year}',{year:value});
  const date=new Date(value.length===7?value+'-01T12:00:00':value+'T12:00:00');
  return T('Uscita: {date}',{date:date.toLocaleDateString(window.KindleI18n.locale,value.length===7?{month:'long',year:'numeric'}:{day:'numeric',month:'short',year:'numeric'})});
 }
 function ratingMarkup(book){return bookRatingMarkup(book,{placeholder:true});}
 function metadata(book){
  return `<div class="recommendation-metadata">
   <p class="recommendation-publication">${book.published?`<a href="${E(book.published_url||book.url)}" target="_blank" rel="noopener noreferrer">${E(publication(book.published))}</a>`:E(publication(''))}</p>
   <div class="recommendation-rating">${ratingMarkup(book)}</div>
  </div>`;
 }
 function bookCard(book,index){
  return `<article class="card recommendation-card">
   <div class="recommendation-header">
    <div class="recommendation-cover book-cover-column">
     <img class="book-cover" src="${E(book.cover_url||'/static/cover-placeholder.svg')}" alt="" loading="lazy" width="84" height="126">
     ${category==='charts'?`<span class="recommendation-rank">${index+1}</span>`:''}
     ${bookSourceLink(book.url)}
    </div>
    <div class="recommendation-info">
     <h2 class="file-title" data-untranslated>${E(book.title)}</h2>
     <p class="small muted" data-untranslated>${E(book.author)}</p>
     <ul class="recommendation-genres" data-untranslated hidden></ul>
     ${metadata(book)}
    </div>
   </div>
   <div class="recommendation-synopsis synopsis-disclosure muted" data-recommendation-synopsis="${E(book.url)}">
    <p id="recommendation-synopsis-${index}" class="synopsis-text">Caricamento della trama…</p>
    <button class="synopsis-toggle" type="button" aria-expanded="false" aria-label="Leggi la trama" aria-controls="recommendation-synopsis-${index}" hidden>…</button>
   </div>
   <div class="recommendation-actions">
    <button class="primary" type="button" data-recommendation-download="${index}">Scarica</button>
    ${readingListButton('recommendation',book.url,savedRecommendation(book))}
   </div>
  </article>`;
 }
 function showSynopsis(node,result,expanded=false){
  const genres=node.closest('.recommendation-card').querySelector('.recommendation-genres');
  for(const genre of result.genres||[]){
   const label=document.createElement('li');
   label.textContent=genre;
   genres.append(label);
  }
  genres.hidden=!genres.children.length;
  if(expanded){
   setSynopsisExpanded(node,true);
   node.querySelector('.synopsis-toggle').hidden=false;
  }
  displaySynopsis(node,result.synopsis||T(result.error||'Trama non disponibile.'),Boolean(result.synopsis));
  node.classList.toggle('muted',!result.synopsis);
 }
 async function loadVisibleSynopses(){
  if(loadingSynopses)return;
  loadingSynopses=true;
  try{
   for(const node of pendingSynopses){
    pendingSynopses.delete(node);
    if(!node.isConnected||el('page-recommended').hidden)continue;
    synopsisObserver.unobserve(node);
    const url=node.dataset.recommendationSynopsis,epoch=window.KindleAccounts.epoch;
    let result=synopses.get(url);
    if(!result){
     try{
      result=await request('/api/recommendations/synopsis',{url});
     }catch(error){
      if(error.name==='AbortError')continue;
      result={error:error.message};
     }
     if(epoch!==window.KindleAccounts.epoch)continue;
     synopses.set(url,result);
    }
    if(node.isConnected)showSynopsis(node,result);
   }
  }finally{loadingSynopses=false;}
 }
 function render(){
  if(!data)return;
  const books=data.books.filter(b=>b.categories.includes(category)).sort((a,b)=>(a.ranks?.[category]||0)-(b.ranks?.[category]||0));
  el('recommendation-count').textContent=category==='new'
   ? T('{count} libri',{count:books.length})+' · '+T('Usciti oggi e ieri')
   : T('{shown} libri · massimo {total}',{shown:books.length,total:data.limits[category]});
  el('recommendation-refresh').disabled=data.refreshing;
  const active=data.sources.filter(s=>s.enabled),errors=active.filter(s=>s.error);
  notice('recommendation-status',data.refreshing?'Aggiornamento delle fonti in corso…':!active.length?'Attiva o aggiungi una fonte nelle impostazioni.':errors.length?'Alcune fonti non sono disponibili. Gli ultimi risultati restano visibili con la loro data.':'');
  el('recommendation-schedule').textContent=T('Prossima scansione: {date} · Europe/Rome',{date:new Date(data.next_run*1000).toLocaleString(window.KindleI18n.locale,{weekday:'long',day:'numeric',month:'short',hour:'2-digit',minute:'2-digit',timeZone:'Europe/Rome'})});
  const bookList = el('recommendation-books');
  const markup=window.KindleI18n.html(books.map(bookCard).join('')||`<div class="empty"><strong>${category==='new'?'Nessuna uscita trovata per oggi o ieri.':'Nessun titolo disponibile.'}</strong></div>`);
  if(bookMarkup!==markup){
   const expanded=new Set();
   for(const node of bookList.querySelectorAll('[data-recommendation-synopsis]')){
    if(node.classList.contains('is-expanded'))expanded.add(node.dataset.recommendationSynopsis);
    synopsisSizeObserver.unobserve(node);
   }
   synopsisObserver.disconnect();
   pendingSynopses.clear();
   bookList.innerHTML=markup;
   bookMarkup=markup;
   for(const node of bookList.querySelectorAll('[data-recommendation-synopsis]')){
    const result=synopses.get(node.dataset.recommendationSynopsis);
    if(result)showSynopsis(node,result,expanded.has(node.dataset.recommendationSynopsis));
    else synopsisObserver.observe(node);
   }
  }
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
 function poll(){clearTimeout(timer);if(data?.refreshing||!el('page-recommended').hidden)timer=setTimeout(()=>load(false),data?.refreshing?2000:60000);}
 async function load(first=true){
  if(loading||!window.KindleAccounts.active)return;loading=true;
  try{data=await request('/api/recommendations');render();poll();
   if(first&&data.sources.some(s=>s.enabled)&&!data.sources.some(s=>s.checked)&&!data.refreshing){data=await request('/api/recommendations/refresh',{});render();poll();}
  }catch(error){if(error.name!=='AbortError')notice('recommendation-status',error.message);}finally{loading=false;}
 }
 el('recommendation-refresh').onclick=async()=>{
  el('recommendation-refresh').disabled=true;
  try{
   data=await request('/api/recommendations/refresh',{});
   for(const [url,result] of synopses)if(result.error){synopses.delete(url);bookMarkup='';}
   render();poll();
   if(!data.refreshing)window.toast('Attendi un minuto tra due aggiornamenti.');
  }catch(error){notice('recommendation-status',error.message);el('recommendation-refresh').disabled=false;}
 };
 el('recommendation-source-add').onclick=()=>{if(el('recommendation-sources').children.length>=12){notice('recommendation-sources-error','Puoi aggiungere fino a 12 fonti.');return;}sourceDirty=true;row();el('recommendation-sources').lastElementChild.querySelector('input').focus();};
 el('recommendation-sources-form').addEventListener('input',()=>sourceDirty=true);
 el('recommendation-sources-form').addEventListener('click',event=>{if(event.target.closest('.source-remove')){sourceDirty=true;event.target.closest('li').remove();}});
 el('recommendation-sources-form').onsubmit=async event=>{
  event.preventDefault();const button=event.submitter;button.disabled=true;notice('recommendation-sources-error','');
  const sources=[...el('recommendation-sources').children].map(node=>({name:node.querySelector('[data-source-name]').value,url:node.querySelector('[data-source-url]').value,category:node.querySelector('[data-source-category]').value,enabled:node.querySelector('[data-source-enabled]').checked}));
  try{data=await request('/api/recommendations/sources',{sources});sourceDirty=false;el('recommendation-sources').dataset.signature='';render();window.toast('Fonti salvate.');}catch(error){notice('recommendation-sources-error',error.message);}finally{button.disabled=false;}
 };
 document.addEventListener('click',event=>{const tab=event.target.closest('[data-recommendation-category]');if(tab){category=tab.dataset.recommendationCategory;document.querySelectorAll('[data-recommendation-category]').forEach(b=>{b.classList.toggle('active',b===tab);b.setAttribute('aria-pressed',String(b===tab));});render();}const button=event.target.closest('[data-recommendation-download]');if(button)download(data.books.filter(b=>b.categories.includes(category)).sort((a,b)=>(a.ranks?.[category]||0)-(b.ranks?.[category]||0))[Number(button.dataset.recommendationDownload)]);});
 const visibleCatalogueBatch=12;
 let searchBusy=false,searchGeneration=0,catalogueDownloading=false,loadingCatalogueDetails=false;
 let catalogue=null;
 const pendingCatalogueDetails=new Set();
 const catalogueObserver=new IntersectionObserver(entries=>{
  for(const entry of entries){
   if(entry.isIntersecting)pendingCatalogueDetails.add(entry.target);
   else pendingCatalogueDetails.delete(entry.target);
  }
  loadCatalogueDetails();
 },{rootMargin:'0px 0px 100px'});
 let downloadLanguageDirty=false,lastDownloadLanguage=null,downloadFolderDirty=false;
 function effectiveDownloadLanguage(){return (telegram?.language_preference??'app')==='app'?window.KindleI18n.language:(telegram.language_preference||telegram.language);}
 function languageName(code){return T(({it:'Italiano',en:'Inglese',fr:'Francese',es:'Spagnolo',zh:'Cinese',ru:'Russo'})[code]||code);}
 function renderDownloadLanguage(){
  const language=effectiveDownloadLanguage();
  if(lastDownloadLanguage!==null&&lastDownloadLanguage!==language){
   resetDownloadSearch();downloadToken++;el('download-dialog').close();
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
  const busy=searchBusy||catalogueDownloading;
  el('downloads-search-submit').disabled=busy||!window.KindleAccounts.active;
  el('downloads-query').disabled=busy;
  el('downloads-more').disabled=busy;
  for(const button of el('downloads-results').querySelectorAll('[data-catalogue-download]')){
   button.disabled=catalogueDownloading||button.dataset.downloaded==='true';
  }
 }
 function resetDownloadSearch(){
  searchGeneration++;searchBusy=false;catalogueDownloading=false;catalogue=null;
  catalogueObserver.disconnect();pendingCatalogueDetails.clear();
  const results=el('downloads-results');
  for(const box of results.querySelectorAll('.synopsis-disclosure'))synopsisSizeObserver.unobserve(box);
  results.replaceChildren();el('downloads-more').hidden=true;
  notice('downloads-status','');searchControls();
 }
 function editionFormat(option){
  const size=option.size?' · '+new Intl.NumberFormat(window.KindleI18n.locale,{maximumFractionDigits:2}).format(option.size/1048576)+' MB':'';
  return option.format.toUpperCase()+size;
 }
 function catalogueCard(book){
  return `<article class="card download-result-card" data-catalogue-book="${E(book.id)}">
   <div class="download-preview-header">
    <img class="book-cover" src="${E(book.cover_url||'/static/cover-placeholder.svg')}" alt="" width="94" height="141" loading="lazy" decoding="async">
    <div class="download-preview-info">
     <h2 tabindex="-1" data-untranslated>${E(book.title)}</h2>
     ${book.author?`<p class="muted" data-untranslated>${E(book.author)}</p>`:''}
     <span class="pill catalogue-in-library" data-catalogue-library hidden>Già in libreria</span>
     ${book.local_only?'':`<div class="recommendation-rating">${ratingMarkup(book)}</div>`}
    </div>
   </div>
   ${book.local_only?'':`<div class="download-preview-description">
    <h3>Trama</h3>
    <div class="synopsis-disclosure muted">
     <p id="catalogue-synopsis-${E(book.id)}" class="synopsis-text">Caricamento della trama…</p>
     <button class="synopsis-toggle" type="button" aria-expanded="false" aria-label="Leggi la trama" aria-controls="catalogue-synopsis-${E(book.id)}" hidden>…</button>
    </div>
   </div>`}
   <div class="download-preview-actions">
    <button class="primary" type="button" data-catalogue-download>Scarica</button>
    <button class="primary" type="button" data-catalogue-open hidden>Leggi</button>
    ${book.url?readingListButton('catalogue',book.id,savedRecommendation(book)):''}
    <div data-catalogue-read-mark hidden></div>
    <p class="small catalogue-download-status" role="status" hidden></p>
   </div>
  </article>`;
 }
 function showCatalogueBatch(){
  const results=el('downloads-results'),start=results.children.length;
  const books=catalogue.books.slice(start,start+visibleCatalogueBatch);
  results.insertAdjacentHTML('beforeend',window.KindleI18n.html(books.map(catalogueCard).join('')));
  for(const [index,card] of [...results.children].slice(start).entries()){
   if(!books[index].local_only)catalogueObserver.observe(card);
  }
  el('downloads-more').hidden=!(catalogue.more||results.children.length<catalogue.books.length);
  refreshCatalogueReadingList();
  searchControls();
  if(books.length&&!el('page-downloads').hidden)results.children[start].querySelector('h2').focus();
 }
 async function loadCatalogueDetails(){
  if(loadingCatalogueDetails)return;
  loadingCatalogueDetails=true;
  try{
   for(const card of pendingCatalogueDetails){
    pendingCatalogueDetails.delete(card);
    if(!card.isConnected||el('page-downloads').hidden)continue;
    catalogueObserver.unobserve(card);
    let details={};
    try{details=await request('/api/downloads/catalogue/details',{id:card.dataset.catalogueBook});}
    catch(error){if(error.name==='AbortError')continue;}
    if(!card.isConnected)continue;
    const box=card.querySelector('.synopsis-disclosure');
    box.classList.toggle('muted',!details.synopsis);
    displaySynopsis(box,details.synopsis||T('Trama non disponibile.'),Boolean(details.synopsis));
    if(details.rating_value)card.querySelector('.recommendation-rating').innerHTML=ratingMarkup(details);
   }
  }finally{loadingCatalogueDetails=false;}
 }
 function refreshCatalogueReadingList(){
  if(!catalogue)return;
  const cards=el('downloads-results').children;
  for(let index=0;index<cards.length;index++){
   const card=cards[index],book=catalogue.books[index];
   const file=book.file_id&&state?.files.find(file=>file.id===book.file_id&&file.available);
   const saved=state?.reading_list_files[book.file_id]||savedRecommendation(book)||'';
   const inLibrary=card.querySelector('[data-catalogue-library]');
   inLibrary.hidden=!file;
   inLibrary.textContent=T('Già in libreria');
   const open=card.querySelector('[data-catalogue-open]');
   open.hidden=!file;
   open.dataset.read=file?.id||'';
   open.textContent=T('Leggi');
   const download=card.querySelector('[data-catalogue-download]');
   download.hidden=Boolean(file||book.local_only);
   download.textContent=T('Scarica');
   const button=card.querySelector('[data-reading-list-source="catalogue"]');
   if(button){
    button.hidden=Boolean(file);
    button.dataset.readingListId=saved;
    const label=T(readingListLabel(saved));
    if(button.textContent!==label)button.textContent=label;
   }
   const mark=card.querySelector('[data-catalogue-read-mark]');
   const target=file||readMarkState(`reading-list/${saved}`);
   const signature=JSON.stringify([file?.id,saved,window.KindleI18n.language]);
   if(mark.dataset.signature!==signature){
    mark.innerHTML=target?window.KindleI18n.html(readingMarkCheckbox(target,book.title,file?'files':'reading-list')):'';
    mark.dataset.signature=signature;
   }
   mark.hidden=!target;
  }
  syncReadingMarks(el('downloads-results'));
 }
 function retranslateCatalogue(){
  refreshCatalogueReadingList();
  for(const box of el('downloads-results').querySelectorAll('.synopsis-disclosure')){
   setSynopsisExpanded(box,box.classList.contains('is-expanded'));
   if(!box.classList.contains('has-synopsis')){
    const text=box.querySelector('.synopsis-text');
    text.textContent=T(text.textContent);
   }
   const card=box.closest('.download-result-card');
   card.querySelector('h3').textContent=T('Trama');
  }
 }
 async function downloadEdition(option,isCurrent,showStatus){
  showStatus(T('Download in corso…')+' '+editionFormat(option));
  const saved=await request('/api/telegram/download',{token:option.token});
  if(!isCurrent())return;
  showStatus(downloadMessage(saved));
  window.toast('Libro scaricato.');
  await refresh();
  return saved;
 }
 async function downloadFirst(search,isCurrent,showStatus,emptyMessage){
  showStatus(T('Ricerca delle edizioni nella lingua scelta…'));
  const result=await request('/api/telegram/search',search);
  if(!isCurrent())return;
  const [option]=result.options;
  if(!option){showStatus(T(emptyMessage));return;}
  await downloadEdition(option,isCurrent,showStatus);
 }
 el('downloads-connect').onclick=()=>{location.hash='settings';setTimeout(()=>el('telegram-settings').scrollIntoView({block:'start',behavior:'smooth'}),0);};
 async function fetchCataloguePage(){
  const generation=searchGeneration,accountEpoch=window.KindleAccounts.epoch;
  const isCurrent=()=>generation===searchGeneration&&accountEpoch===window.KindleAccounts.epoch;
  searchBusy=true;searchControls();
  notice('downloads-status','Ricerca del libro in corso…');
  try{
   const page=catalogue.page+1;
   const result=await request('/api/downloads/catalogue/search',{query:catalogue.query,page,author_search:catalogue.authorSearch});
   if(!isCurrent())return;
   catalogue.page=page;catalogue.more=result.more;catalogue.authorSearch=result.author_search;
   for(const book of result.books){
    if(!catalogue.keys.has(book.key)&&(!book.file_id||!catalogue.files.has(book.file_id))){
     catalogue.keys.add(book.key);
     if(book.file_id)catalogue.files.add(book.file_id);
     catalogue.books.push(book);
    }
   }
   if(!catalogue.books.length&&!catalogue.more){notice('downloads-status','Nessun libro trovato.');return;}
   notice('downloads-status','');showCatalogueBatch();
  }catch(error){if(isCurrent()&&error.name!=='AbortError')notice('downloads-status',error.message);}
  finally{if(isCurrent()){searchBusy=false;searchControls();}}
 }
 el('downloads-query').addEventListener('input',()=>{if(catalogue&&!searchBusy&&!catalogueDownloading)resetDownloadSearch();});
 el('downloads-search-form').onsubmit=event=>{
  event.preventDefault();
  if(searchBusy||catalogueDownloading)return;
  const query=el('downloads-query').value.trim();
  if(!query)return;
  resetDownloadSearch();
  catalogue={query,page:0,more:false,authorSearch:false,books:[],keys:new Set(),files:new Set()};
  fetchCataloguePage();
 };
 el('downloads-more').onclick=()=>{
  if(!catalogue||searchBusy||catalogueDownloading)return;
  if(el('downloads-results').children.length<catalogue.books.length)showCatalogueBatch();
  else if(catalogue.more)fetchCataloguePage();
 };
 el('downloads-results').addEventListener('click',async event=>{
  const button=event.target.closest('[data-catalogue-download]');
  if(!button||catalogueDownloading||button.dataset.downloaded==='true')return;
  if(!telegram?.connected){
   window.toast('Collega Telegram per scaricare i libri.');
   el('downloads-connect').click();
   return;
  }
  const card=button.closest('.download-result-card'),status=card.querySelector('.catalogue-download-status');
  const book=catalogue.books.find(book=>book.id===card.dataset.catalogueBook);
  const accountEpoch=window.KindleAccounts.epoch;
  const isCurrent=()=>card.isConnected&&accountEpoch===window.KindleAccounts.epoch;
  const showStatus=message=>{status.textContent=T(message);status.hidden=!message;};
  catalogueDownloading=true;searchControls();
  try{
   showStatus('Ricerca delle edizioni nella lingua scelta…');
   const result=await request('/api/downloads/catalogue/edition',{id:card.dataset.catalogueBook});
   if(!isCurrent())return;
   if(result.file_id){
    book.file_id=result.file_id;
    catalogue.files.add(result.file_id);
    await refresh();
    if(isCurrent()){refreshCatalogueReadingList();showStatus('');}
    return;
   }
   const [option]=result.options;
   if(!option){showStatus('Nessuna edizione nella lingua scelta. Prova un altro titolo, autore o ISBN.');return;}
   const saved=await downloadEdition(option,isCurrent,showStatus);
   if(isCurrent()){
    book.file_id=saved?.file_id;
    button.dataset.downloaded=String(!book.file_id);
    if(book.file_id)catalogue.files.add(book.file_id);
    refreshCatalogueReadingList();
   }
  }catch(error){if(isCurrent()&&error.name!=='AbortError')showStatus(error.message);}
  finally{if(isCurrent()){catalogueDownloading=false;searchControls();}}
 });
 async function loadTelegram(){try{telegram=await request('/api/telegram');renderTelegram();}catch(error){if(error.name!=='AbortError'){notice('telegram-error',error.message);if(!el('page-downloads').hidden)notice('downloads-status',error.message);}}}
 function renderTelegram(){if(!telegram)return;renderDownloadLanguage();renderDownloadFolder();notice('downloads-connection',telegram.connected?'':'Collega Telegram per scaricare i libri.');el('downloads-connect').hidden=telegram.connected;searchControls();el('telegram-status').textContent=T(telegram.connected?'Telegram collegato. Download pronti.':'Telegram da collegare.');el('telegram-connect-form').hidden=telegram.connected;el('telegram-disconnect').hidden=!telegram.configured;el('telegram-code-form').hidden=!telegram.pending;el('download-bot-link').href='https://t.me/'+telegram.bot;if(telegram.bot&&!el('telegram-connect-form').matches(':focus-within'))el('telegram-connect-form').elements.bot.value=telegram.bot;if(telegram.password_required)notice('telegram-error','Inserisci la password di verifica in due passaggi di Telegram.');}
 for(const [id,path] of [['telegram-connect-form','connect'],['telegram-code-form','verify']])el(id).onsubmit=async event=>{event.preventDefault();const button=event.submitter;button.disabled=true;notice('telegram-error','');try{telegram=await request('/api/telegram/'+path,Object.fromEntries(new FormData(event.target)));event.target.reset();renderTelegram();}catch(error){notice('telegram-error',error.message);}finally{button.disabled=false;}};
 el('telegram-disconnect').onclick=async()=>{try{telegram=await request('/api/telegram/disconnect',{});resetDownloadSearch();renderTelegram();el('telegram-code-form').reset();notice('telegram-error','');}catch(error){notice('telegram-error',error.message);}};
 el('download-close').onclick=()=>el('download-dialog').close();el('download-dialog').addEventListener('close',()=>downloadToken++);
 async function download(book,savedId=savedRecommendation(book||{},true)){
  if(!book)return;
  const accountEpoch=window.KindleAccounts.epoch;await loadTelegram();if(!window.KindleAccounts.active||accountEpoch!==window.KindleAccounts.epoch)return;
  if(!telegram?.connected){location.hash='settings';window.toast('Collega Telegram per scaricare i libri.');setTimeout(()=>el('telegram-settings').scrollIntoView({block:'start',behavior:'smooth'}),0);return;}
  const token=++downloadToken;
  const isCurrent=()=>token===downloadToken&&accountEpoch===window.KindleAccounts.epoch;
  const showStatus=message=>{el('download-status').textContent=message;};
  el('download-title').textContent=book.title+(book.author?' · '+book.author:'');
  el('download-dialog').showModal();
  try{
   await downloadFirst(savedId?{reading_list_id:savedId}:{url:book.url},isCurrent,showStatus,
    'Il bot non ha restituito un’edizione riconoscibile nella lingua scelta. Aprilo in Telegram.');
  }catch(error){if(isCurrent()&&error.name!=='AbortError')showStatus(T(error.message));}
 }
 window.addEventListener('kindle:saved-download',event=>download(event.detail,event.detail.id));
 window.addEventListener('kindle:state',refreshCatalogueReadingList);
 window.addEventListener('kindle:reading-list',()=>{
  const focused=document.activeElement;
  const key=focused?.dataset.readingListKey;
  const restore=key&&el('recommendation-books').contains(focused);
  render();
  if(restore)el('recommendation-books').querySelector(`[data-reading-list-key="${CSS.escape(key)}"]`)?.focus();
 });
 window.addEventListener('kindle:page',event=>{if(['recommended','settings'].includes(event.detail.page))load();if(['downloads','settings'].includes(event.detail.page))loadTelegram();});
 window.addEventListener('kindle:account',()=>{clearTimeout(timer);data=null;telegram=null;loading=false;sourceDirty=false;downloadToken++;downloadLanguageDirty=false;lastDownloadLanguage=null;downloadFolderDirty=false;el('download-folder-form').reset();notice('download-folder-error','');el('download-folder-summary').textContent='';el('downloads-folder').textContent='';el('download-language-form').reset();notice('download-language-error','');el('download-language-summary').textContent='';el('downloads-language').textContent='';resetDownloadSearch();el('downloads-search-form').reset();notice('downloads-connection','Telegram da collegare.');el('downloads-connect').hidden=false;searchControls();for(const node of el('recommendation-books').querySelectorAll('[data-recommendation-synopsis]'))synopsisSizeObserver.unobserve(node);el('recommendation-books').replaceChildren();bookMarkup='';synopsisObserver.disconnect();pendingSynopses.clear();synopses.clear();el('recommendation-sources').replaceChildren();el('recommendation-sources').dataset.signature='';el('telegram-connect-form').reset();el('telegram-code-form').reset();el('download-dialog').close();for(const id of ['telegram-error','recommendation-sources-error','recommendation-status'])notice(id,'');if(window.KindleAccounts.active)load();});
 window.addEventListener('kindle:language',()=>{render();renderTelegram();retranslateCatalogue();if(window.KindleAccounts.active)loadTelegram();});
})();
