'use strict';
(() => {
 const el=id=>document.getElementById(id),frame=()=>new Promise(resolve=>requestAnimationFrame(resolve));
 const storage={get(key){try{return JSON.parse(localStorage.getItem(key));}catch{return null;}},set(key,value){try{localStorage.setItem(key,JSON.stringify(value));}catch{}}};
 const savedPreferences=storage.get('kindle-reader-preferences')||{};
 const preferences={size:Math.max(16,Math.min(30,Number(savedPreferences.size)||20)),theme:Math.max(0,Math.min(2,Number(savedPreferences.theme)||0)),font:savedPreferences.font==='sans'?'sans':'serif',line:[1.5,1.7,1.9].includes(Number(savedPreferences.line))?Number(savedPreferences.line):1.7};
 function preferenceKey(){return 'kindle-reader-preferences-'+window.KindleAccounts.user?.id;}
 function readingKey(digest){return 'kindle-reading-'+window.KindleAccounts.user?.id+'-'+digest;}
 window.addEventListener('kindle:account',event=>{window.closeBook();if(!event.detail.user)return;const saved=storage.get(preferenceKey())||(event.detail.user.legacy?storage.get('kindle-reader-preferences'):null)||{};Object.assign(preferences,{size:20,theme:0,font:'serif',line:1.7},saved);});
 const GAP=48,themes=['paper','sepia','night'];
 let current=null,pdf=null,pdfTask=null,renderTask=null,chapter=0,page=0,counts=[],busy=false,ready=false;
 let returnPage='files';
 function closeLabel(){const labels={'reading-list':'Chiudi il libro e torna a Da leggere',downloads:'Chiudi il libro e torna a Download'};el('reader-close').setAttribute('aria-label',window.KindleI18n.t(labels[returnPage]||'Chiudi il libro e torna alla libreria'));}
 let session=0,revision=0,layout=0,indexRun=0,indexKey='',controller=null,layoutWidth=0,layoutHeight=0,stride=0;
 let cache=new Map(),requests=new Map(),lastLocation=null,resizeTimer,lastSwipe=0,touch=null,pdfZoom=1;
 function styles(){const view=el('reader-view');view.dataset.theme=themes[preferences.theme];view.dataset.font=preferences.font;view.style.setProperty('--reader-size',preferences.size+'px');view.style.setProperty('--reader-leading',preferences.line);el('reader-size-label').textContent=window.KindleI18n.t(preferences.size+' px');el('reader-font').value=preferences.font;el('reader-leading').value=String(preferences.line);el('reader-theme').textContent=window.KindleI18n.t(['☾ Tema chiaro','☾ Tema seppia','☾ Tema notte'][preferences.theme]);storage.set(preferenceKey(),preferences);}
 function sectionTitle(index){const section=current?.chapters[index];return section?.title_key?window.KindleI18n.t(section.title_key,{number:section.title_number}):section?.title||'';}
 function totalPages(){return pdf?pdf.numPages:counts.length&&counts.every(n=>Number.isInteger(n)&&n>0)?counts.reduce((sum,n)=>sum+n,0):0;}
 function globalPage(){if(pdf)return chapter;let n=page;for(let i=0;i<chapter;i++){if(!counts[i])return null;n+=counts[i];}return n;}
 function controls(){
  const total=totalPages(),position=globalPage(),sectionCount=current?.chapters.length||0;
  el('reader-prev').disabled=busy||!current||(pdf?chapter===0:chapter===0&&page===0);
  el('reader-next').disabled=busy||!current||(pdf?chapter>=pdf.numPages-1:chapter>=sectionCount-1&&page>=((counts[chapter]||1)-1));
  el('reader-toc').disabled=busy||!current;
  el('reader-toc').value=String(chapter);
  el('reader-position').textContent=window.KindleI18n.t(current?'Pagina '+((position??page)+1)+(total?' di '+total:' · conteggio…'):'');
  el('reader-percent').textContent=window.KindleI18n.t(total&&position!==null?Math.round((position+1)/total*100)+'%':'Le pagine si adattano allo schermo');
  const range=el('reader-progress');range.disabled=busy||!total;range.max=Math.max(1,total);range.value=(position??0)+1;range.setAttribute('aria-valuetext',el('reader-position').textContent);
  el('reader-page-number').max=total||1;el('reader-page-number').placeholder=total?'1–'+total:'Conteggio in corso';el('reader-page-number').disabled=busy||!total;el('reader-jump-button').disabled=busy||!total;
  el('reader-section-label').textContent=current?(pdf?window.KindleI18n.t('PDF · una pagina alla volta'):(/^(sconosciuto|unknown|untitled)$/i.test(sectionTitle(chapter)||'')?window.KindleI18n.t('Lettura a pagine'):sectionTitle(chapter))||window.KindleI18n.t('Lettura a pagine')):'';
  el('reader-size-label').textContent=window.KindleI18n.t(pdf?Math.round(pdfZoom*100)+'%':preferences.size+' px');
  for(const id of ['reader-smaller','reader-larger','reader-font','reader-leading'])el(id).disabled=busy||!current||Boolean(pdf&&['reader-font','reader-leading'].includes(id));
  el('reader-count-note').textContent=window.KindleI18n.t(total?(pdf?'Numerazione originale del PDF.':'Pagine calcolate per questo schermo e queste impostazioni.'):'Calcolo delle pagine del libro in corso. Puoi già leggere e sfogliare.');
 }
 function status(message){el('reader-status').textContent=window.KindleI18n.t(message);el('reader-status').hidden=false;}
 function menus(which=null){for(const name of ['contents','settings']){el('reader-'+name).hidden=name!==which;el('reader-'+name+'-toggle').setAttribute('aria-expanded',String(name===which));}}
 async function getJSON(url){const run=session,response=await fetch(url,{signal:controller.signal,headers:window.KindleAccounts.headers()});if([401,409].includes(response.status))window.KindleAccounts.showPicker(true);if(run!==session)throw new DOMException('Account cambiato.','AbortError');const data=await response.json();if(!response.ok)throw new Error(data.error||'Impossibile aprire il libro.');return data;}
 async function getChapter(index){
  if(cache.has(index)){const html=cache.get(index);cache.delete(index);cache.set(index,html);return html;}
  if(requests.has(index))return requests.get(index);
  const run=session;
  const request=getJSON('/api/files/'+current.id+'/reader/chapters/'+index).then(data=>{
   if(run!==session)return '';
   cache.set(index,data.html);while(cache.size>8||[...cache.values()].reduce((n,s)=>n+s.length,0)>8*1024*1024&&cache.size>1)cache.delete(cache.keys().next().value);
   return data.html;
  }).finally(()=>{if(run===session)requests.delete(index);});
  requests.set(index,request);return request;
 }
 async function settle(content){await Promise.all([...content.querySelectorAll('img')].map(img=>img.decode().catch(()=>{})));await document.fonts.ready;await frame();}
 function normalizeParagraphs(content,offset=null){
  const empty=new Set([...content.querySelectorAll('p')].filter(p=>!p.textContent.replace(/[\s\u00a0\u200b\ufeff]/g,'')&&!p.querySelector('img,table,hr')));
  let removed=0,seen=0;
  if(Number.isFinite(offset)){const walker=document.createTreeWalker(content,NodeFilter.SHOW_TEXT);let node;while((node=walker.nextNode())){if(seen>=offset)break;if(empty.has(node.parentElement.closest('p')))removed+=Math.min(node.length,offset-seen);seen+=node.length;}}
  for(const paragraph of empty){const anchors=document.createDocumentFragment();for(const node of [paragraph,...paragraph.querySelectorAll('[id]')]){if(node.id){const anchor=document.createElement('span');anchor.id=node.id;anchors.append(anchor);}}paragraph.replaceWith(anchors);}
  return Number.isFinite(offset)?Math.max(0,offset-removed):offset;
 }
 function pageCount(content,width){return Math.max(1,Math.ceil((content.scrollWidth+GAP-1)/(width+GAP)));}
 function geometry(content,width,height){content.style.width=width+'px';content.style.height=height+'px';content.style.columnWidth=width+'px';content.style.columnGap=GAP+'px';content.style.setProperty('--page-height',height+'px');content.style.transform='none';}
 function textLocation(){
  const content=el('reader-content'),windowRect=el('reader-window').getBoundingClientRect(),walker=document.createTreeWalker(content,NodeFilter.SHOW_TEXT);
  let node,offset=0;
  while((node=walker.nextNode())){
   if(!node.textContent.trim()){offset+=node.length;continue;}
   const range=document.createRange();range.selectNodeContents(node);
   if([...range.getClientRects()].some(rect=>rect.right>windowRect.left+1&&rect.left<windowRect.right-1&&rect.bottom>windowRect.top&&rect.top<windowRect.bottom)){
    let low=0,high=node.length;
    while(low<high){const mid=Math.floor((low+high)/2);range.setStart(node,mid);range.setEnd(node,Math.min(mid+1,node.length));const rect=range.getBoundingClientRect();if(rect.right<=windowRect.left+1)low=mid+1;else high=mid;}
    return offset+low;
   }
   offset+=node.length;
  }
  return null;
 }
 function readingLocation(){return {version:3,chapter,page,offset:pdf?null:textLocation(),ratio:page/Math.max(1,(counts[chapter]||1)-1)};}
 function progress(){if(!current||!ready)return;lastLocation=readingLocation();storage.set(readingKey(current.digest),lastLocation);}
 function pageForOffset(offset){
  if(!Number.isFinite(offset))return null;
  const content=el('reader-content'),walker=document.createTreeWalker(content,NodeFilter.SHOW_TEXT);let node;
  while((node=walker.nextNode())){if(offset<node.length){const range=document.createRange();range.setStart(node,offset);range.setEnd(node,Math.min(offset+1,node.length));return Math.floor((range.getBoundingClientRect().left-el('reader-window').getBoundingClientRect().left+1)/stride);}offset-=node.length;}
  return null;
 }
 function setPage(next){page=Math.max(0,Math.min((counts[chapter]||1)-1,next));el('reader-content').style.transform='translateX('+(-page*stride)+'px)';el('reader-scroll').scrollTop=0;el('reader-scroll').scrollLeft=0;controls();progress();}
 async function indexPages(){
  if(!current||pdf)return;
  const key=session+':'+layout;if(indexKey===key)return;indexKey=key;
  const run=session,generation=layout,index=++indexRun;
  try{
   for(let i=0;i<current.chapters.length;i++){
    if(counts[i])continue;
    const html=await getChapter(i);if(run!==session||generation!==layout||index!==indexRun)return;
    const measure=el('reader-measure');measure.dataset.owner=String(index);measure.hidden=false;measure.lang=current.language||'und';measure.innerHTML=html;normalizeParagraphs(measure);geometry(measure,layoutWidth,layoutHeight);
    await settle(measure);if(run!==session||generation!==layout||index!==indexRun)return;
    counts[i]=pageCount(measure,layoutWidth);measure.replaceChildren();measure.hidden=true;controls();
    await new Promise(resolve=>setTimeout(resolve,0));
   }
  }catch(error){if(run===session&&generation===layout&&error.name!=='AbortError')el('reader-count-note').textContent=window.KindleI18n.t('Una parte del libro non è leggibile. Puoi continuare con le frecce o scegliere una voce nell’indice.');}
  finally{if(index===indexRun){el('reader-measure').replaceChildren();el('reader-measure').hidden=true;}}
 }
 async function showChapter(index,position={},fragment='',keepMenu=false){
  if(!current)return;
  const run=session,change=++revision;busy=true;ready=false;chapter=index;page=0;if(!keepMenu)menus();controls();status('Preparazione della pagina…');
  el('reader-paper').classList.add('is-loading');el('reader-paper').hidden=Boolean(pdf);el('reader-pdf').hidden=!pdf;
  try{
   if(pdf){
    renderTask?.cancel();const sheet=await pdf.getPage(index+1);if(run!==session||change!==revision)return;
    const initial=sheet.getViewport({scale:1}),width=Math.max(180,el('reader-scroll').clientWidth-32),height=Math.max(160,el('reader-scroll').clientHeight-32);
    const viewport=sheet.getViewport({scale:Math.min(width/initial.width,height/initial.height)*pdfZoom});
    const ratio=Math.min(window.devicePixelRatio||1,2,Math.sqrt(12000000/(viewport.width*viewport.height)));
    const canvas=el('reader-canvas');canvas.width=Math.floor(viewport.width*ratio);canvas.height=Math.floor(viewport.height*ratio);canvas.style.width=viewport.width+'px';canvas.style.height=viewport.height+'px';
    renderTask=sheet.render({canvasContext:canvas.getContext('2d'),viewport,transform:ratio===1?null:[ratio,0,0,ratio,0,0]});await renderTask.promise;renderTask=null;
    const text=await sheet.getTextContent();if(run!==session||change!==revision)return;
    el('reader-pdf-text').textContent=text.items.map(item=>item.str).join(' ');canvas.setAttribute('aria-label',window.KindleI18n.t('Pagina '+(index+1)+' di '+pdf.numPages+'. '+el('reader-pdf-text').textContent.slice(0,250)));
    el('reader-scroll').scrollTop=0;el('reader-scroll').scrollLeft=0;
   }else{
    const html=await getChapter(index);if(run!==session||change!==revision)return;
    const content=el('reader-content');content.lang=current.language||'und';content.innerHTML=html;const offset=normalizeParagraphs(content,position.version===2?position.offset:null);content.hidden=false;
    layoutWidth=el('reader-window').clientWidth;layoutHeight=el('reader-window').clientHeight;stride=layoutWidth+GAP;
    geometry(content,layoutWidth,layoutHeight);await settle(content);if(run!==session||change!==revision)return;
    counts[index]=pageCount(content,layoutWidth);
    let target=position.end?counts[index]-1:pageForOffset(position.version===2?offset:position.offset);
    if(target===null)target=Number.isFinite(position.page)?position.page:Math.floor((Number(position.scroll)||0)/Math.max(1,layoutHeight));
    if(fragment){const element=document.getElementById('book-'+fragment);if(element){const rect=element.getClientRects()[0];if(rect)target=Math.floor((rect.left-el('reader-window').getBoundingClientRect().left+1)/stride);}}
    setPage(target);
   }
   if(run!==session||change!==revision)return;
   ready=true;busy=false;el('reader-paper').classList.remove('is-loading');el('reader-status').hidden=true;controls();progress();indexPages();
  }catch(error){if(run!==session||change!==revision||error.name==='AbortError'||error.name==='RenderingCancelledException')return;
   busy=false;status(pdf?'Impossibile leggere questa pagina PDF. Il documento potrebbe essere danneggiato o richiedere una password.':error.message);controls();}
 }
 function turn(delta){
  if(!current||busy)return;progress();menus();
  if(pdf){const next=chapter+delta;if(next>=0&&next<pdf.numPages)showChapter(next);return;}
  const next=page+delta;
  if(ready&&next>=0&&next<(counts[chapter]||1)){setPage(next);return;}
  if(chapter+delta>=0&&chapter+delta<current.chapters.length)showChapter(chapter+delta,delta<0?{end:true}:{});
 }
 function jump(number){
  const total=totalPages();if(!total||busy)return;let index=Math.max(0,Math.min(total-1,number-1));progress();
  if(pdf){showChapter(index);return;}
  let section=0;while(index>=counts[section])index-=counts[section++];
  if(section===chapter){menus();setPage(index);}else showChapter(section,{page:index});
 }
 function reflow(){
  if(!current||busy)return;
  const position=lastLocation||{page};layout++;indexRun++;indexKey='';counts=Array(current.chapters.length).fill(null);showChapter(chapter,position,'',true);
 }
 function scheduleReflow(){clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{if(!current||busy||el('reader-view').hidden)return;if(pdf||el('reader-window').clientWidth!==layoutWidth||el('reader-window').clientHeight!==layoutHeight)reflow();},140);}
 window.closeBook=()=>{
  if(el('reader-view').hidden)return;progress();ready=false;busy=false;session++;revision++;indexRun++;clearTimeout(resizeTimer);controller?.abort();renderTask?.cancel();renderTask=null;
  if(pdfTask)pdfTask.destroy().catch(()=>{});pdfTask=null;pdf=null;current=null;cache.clear();requests.clear();lastLocation=null;indexKey='';menus();
  for(const id of ['reader-content','reader-measure'])el(id).replaceChildren();el('reader-measure').hidden=true;el('reader-pdf-text').textContent=window.KindleI18n.t('');
  el('reader-view').hidden=true;el('shell').hidden=false;document.body.classList.remove('reading');document.title=window.KindleI18n.t('Kindle · La tua libreria');
 };
 window.openBook=async (id,fromPage='files')=>{
  if(!/^[a-f0-9]{32}$/.test(id)){location.hash='files';return;}if(current?.id===id&&!el('reader-view').hidden)return;
  window.closeBook();returnPage=fromPage;closeLabel();el('reader-book-details').dataset.fileId=id;el('reader-book-details').hidden=!window.KindleShelves?.hasFile(id);const run=++session;controller=new AbortController();chapter=0;page=0;counts=[];layout++;pdfZoom=1;ready=false;busy=true;
  el('shell').hidden=true;el('reader-view').hidden=false;document.body.classList.add('reading');styles();menus();
  el('reader-paper').hidden=true;el('reader-pdf').hidden=true;el('reader-title').textContent=window.KindleI18n.t('Apertura libro…');el('reader-send').disabled=true;el('reader-toc').replaceChildren();controls();status('Apertura del libro… La prima conversione può richiedere un minuto.');el('reader-close').focus();
  try{
   const book=await getJSON('/api/files/'+id+'/reader');if(run!==session)return;current={...book,id};counts=Array(book.chapters.length).fill(null);el('reader-title').textContent=book.title;document.title=book.title+' · Kindle Sync';el('reader-send').disabled=false;el('reader-scroll').dataset.mode=book.kind;
   if(book.kind==='pdf'){
    const pdfjs=await import('/static/vendor/pdfjs/pdf.mjs');if(run!==session)return;pdfjs.GlobalWorkerOptions.workerSrc='/static/vendor/pdfjs/pdf.worker.mjs';
    pdfTask=pdfjs.getDocument({url:book.source_url,httpHeaders:window.KindleAccounts.headers(),isEvalSupported:false,disableFontFace:true,cMapUrl:'/static/vendor/pdfjs/cmaps/',cMapPacked:true,standardFontDataUrl:'/static/vendor/pdfjs/standard_fonts/',wasmUrl:'/static/vendor/pdfjs/wasm/',iccUrl:'/static/vendor/pdfjs/iccs/'});
    const doc=await pdfTask.promise;if(run!==session){doc.destroy();return;}pdf=doc;
   }
   const count=pdf?pdf.numPages:book.chapters.length,options=document.createDocumentFragment();for(let i=0;i<count;i++){const option=document.createElement('option');option.value=i;option.textContent=pdf?window.KindleI18n.t('Pagina {number}',{number:i+1}):sectionTitle(i);options.append(option);}el('reader-toc').replaceChildren(options);
   const saved=storage.get(readingKey(book.digest))||(window.KindleAccounts.user?.legacy?storage.get('kindle-reading-'+book.digest):null)||{};chapter=Math.max(0,Math.min(count-1,Number(saved.chapter)||0));await showChapter(chapter,saved);
  }catch(error){if(run!==session||error.name==='AbortError')return;busy=false;status(error.message||'Impossibile aprire il libro. Controlla che il file non sia danneggiato o protetto da DRM.');controls();}
 };
 el('reader-close').onclick=()=>{location.hash=returnPage;};el('reader-prev').onclick=()=>turn(-1);el('reader-next').onclick=()=>turn(1);
 el('reader-toc').onchange=event=>{progress();showChapter(Number(event.target.value));};el('reader-progress').onchange=event=>jump(Number(event.target.value));
 el('reader-jump').onsubmit=event=>{event.preventDefault();const value=Number(el('reader-page-number').value);if(Number.isInteger(value)&&value>0)jump(value);};
 el('reader-book-details').onclick=event=>window.KindleShelves?.showFile(event.currentTarget.dataset.fileId);
 el('reader-send').onclick=()=>{if(current)window.sendBook(current.id,el('reader-send'));};
 for(const name of ['contents','settings'])el('reader-'+name+'-toggle').onclick=()=>menus(el('reader-'+name).hidden?name:null);
 for(const button of document.querySelectorAll('[data-close-reader-panel]'))button.onclick=()=>menus();
 function changeSize(delta){if(busy||!current)return;progress();if(pdf){pdfZoom=Math.max(.6,Math.min(3,pdfZoom+delta/10));showChapter(chapter,{},'',true);}else{preferences.size=Math.max(16,Math.min(30,preferences.size+delta));styles();reflow();}}
 el('reader-smaller').onclick=()=>changeSize(-2);el('reader-larger').onclick=()=>changeSize(2);
 el('reader-font').onchange=event=>{progress();preferences.font=event.target.value;styles();reflow();};el('reader-leading').onchange=event=>{progress();preferences.line=Number(event.target.value);styles();reflow();};
 el('reader-theme').onclick=()=>{preferences.theme=(preferences.theme+1)%themes.length;styles();};
 el('reader-content').addEventListener('click',event=>{const link=event.target.closest('a');if(!link)return;event.preventDefault();event.stopPropagation();progress();if(link.dataset.chapter!==undefined)showChapter(Number(link.dataset.chapter),{},link.dataset.fragment);else if(link.hash)showChapter(chapter,{},decodeURIComponent(link.hash.slice(1)).replace(/^book-/,''));});
 el('reader-scroll').addEventListener('click',event=>{if(!current||busy||pdf||event.target.closest('a,button,input,select')||Date.now()-lastSwipe<500||!window.getSelection().isCollapsed)return;if(!el('reader-settings').hidden||!el('reader-contents').hidden){menus();return;}const rect=el('reader-window').getBoundingClientRect(),x=event.clientX-rect.left;if(x<rect.width*.18)turn(-1);else if(x>rect.width*.82)turn(1);});
 el('reader-scroll').addEventListener('touchstart',event=>{if(event.touches.length!==1||event.target.closest('a,button,input,select')){touch=null;return;}touch={x:event.touches[0].clientX,y:event.touches[0].clientY,time:Date.now()};},{passive:true});
 el('reader-scroll').addEventListener('touchend',event=>{if(!touch||event.changedTouches.length!==1)return;const end=event.changedTouches[0],dx=end.clientX-touch.x,dy=end.clientY-touch.y;if(Math.abs(dx)>55&&Math.abs(dx)>Math.abs(dy)*1.5&&Date.now()-touch.time<900&&window.getSelection().isCollapsed){lastSwipe=Date.now();turn(dx<0?1:-1);}touch=null;},{passive:true});
 document.addEventListener('keydown',event=>{if(el('reader-view').hidden||document.querySelector('dialog[open]')||event.target.matches('input,select,textarea'))return;if(event.key==='Escape'){if(!el('reader-settings').hidden||!el('reader-contents').hidden)menus();else el('reader-close').click();return;}if(['ArrowRight','PageDown','ArrowLeft','PageUp'].includes(event.key)){event.preventDefault();turn(['ArrowRight','PageDown'].includes(event.key)?1:-1);}else if(event.key===' '&&!event.target.matches('button')){event.preventDefault();turn(event.shiftKey?-1:1);}});
 window.addEventListener('kindle:language',()=>{styles();closeLabel();if(!el('reader-status').hidden)status(el('reader-status').textContent);if(!current)return;progress();const options=el('reader-toc').options;for(let i=0;i<options.length;i++)options[i].textContent=pdf?window.KindleI18n.t('Pagina {number}',{number:i+1}):sectionTitle(i);controls();document.title=current.title+' · Kindle Sync';if(!pdf)reflow();});
 document.addEventListener('visibilitychange',()=>{if(document.hidden)progress();else scheduleReflow();});window.addEventListener('pagehide',progress);window.addEventListener('resize',scheduleReflow);new ResizeObserver(scheduleReflow).observe(el('reader-window'));
})();
