'use strict';
const $=s=>document.querySelector(s);
const $$=s=>Array.from(document.querySelectorAll(s));
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels={pending:'Da inviare',queued:'In coda',sending:'Invio in corso',accepted:'Accettato da SMTP',failed:'Invio fallito',uncertain:'Esito incerto',skipped:'Duplicato evitato',reviewed:'Ricontrollato',legacy:'Registro precedente',cancelled:'Invio annullato'};
let csrf='',state=null,page='home',filter='all',fileLimit=30,homePendingLimit=20,checking=false,selected=new Set(),refreshTask=null,uploading=false,scheduleLoaded=false;
let toastTimer;const activeUploads=new Set();window.appIsUploading=()=>uploading;window.cancelUploads=()=>{for(const xhr of activeUploads)xhr.abort();};window.toast=toast;
function toast(message){$('#toast').textContent=window.KindleI18n.t(message);$('#toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').hidden=true,6500);}
function formatDate(value,full=false){if(!value)return 'Nessun invio';const d=new Date(typeof value==='number'?value*1000:value);return d.toLocaleString(window.KindleI18n.locale,full?{day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'}:{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});}
function size(bytes){return bytes>1048576?(bytes/1048576).toFixed(1)+' MB':Math.ceil(bytes/1024)+' KB';}
function badge(status){return `<span class="badge ${esc(status)}">${esc(labels[status]||status)}</span>`;}
async function api(path,body){return window.KindleAccounts.request(path,body);}

function go(next,scroll=true){if(next.startsWith('read=')){window.openBook(next.slice(5),page==='reading-list'?'reading-list':'files');return;}window.closeBook();if(!['home','files','reading-list','recommended','downloads','history','schedule','settings'].includes(next))next='home';page=next;$$('.page').forEach(p=>p.hidden=p.id!=='page-'+page);$$('.bottom-nav button').forEach(b=>{b.classList.toggle('active',b.dataset.go===page);if(b.dataset.go===page)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});history.replaceState(null,'','#'+page);if(scroll)window.scrollTo({top:0,behavior:'instant'});window.dispatchEvent(new CustomEvent('kindle:page',{detail:{page}}));}
function confirmAction(title,message,button='Conferma'){return new Promise(resolve=>{const dialog=$('#confirm-dialog');$('#confirm-title').textContent=window.KindleI18n.t(title);$('#confirm-message').textContent=window.KindleI18n.t(message);$('#confirm-ok').textContent=window.KindleI18n.t(button);dialog.returnValue='cancel';dialog.addEventListener('close',()=>resolve(dialog.returnValue==='ok'),{once:true});dialog.showModal();});}
function normalized(value){return String(value||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase(window.KindleI18n.locale);}
function visibleFiles(){if(!state)return [];const words=normalized($('#search').value).trim().split(/\s+/).filter(Boolean),folder=$('#folder-filter').value,format=$('#format-filter').value;return state.files.filter(f=>words.every(word=>normalized(f.name+' '+f.folder).includes(word))&&(!folder||f.folder===folder||f.folder.startsWith(folder+'/'))&&(!format||f.format===format)&&(filter==='all'||filter==='pending'&&!f.sent_at&&f.available||filter==='problems'&&(['failed','uncertain'].includes(f.status)||!f.available)));}
function renderLibraryFilters(){for(const [selector,key,label] of [['#folder-filter','folder','Tutte le cartelle'],['#format-filter','format','Tutti i formati']]){const el=$(selector),values=[...new Set(state.files.map(f=>f[key]))].sort((a,b)=>a.localeCompare(b,window.KindleI18n.locale)),signature=JSON.stringify([values,window.KindleI18n.language]);if(el.dataset.signature!==signature){const old=el.value;el.innerHTML=window.KindleI18n.html('<option value="">'+label+'</option>'+values.map(v=>`<option value="${esc(v)}" data-untranslated>${esc(v==='.'?window.KindleI18n.t('Cartella principale'):v)}</option>`).join(''));el.value=values.includes(old)?old:'';el.dataset.signature=signature;}}}
function bookCover(f){return `<img class="book-cover" src="${esc(f.cover_url||'/static/cover-placeholder.svg')}" alt="Copertina di ${esc(f.name)}" width="48" height="72" loading="lazy" decoding="async">`;}
function bookActions(f,sendLabel='Invia ora ↗'){
 const busy=['queued','sending'].includes(f.status);
 return `<button class="secondary" data-read="${f.id}" ${!f.available?'disabled':''}>Leggi</button><button class="primary" ${!state.mail_ready?'data-configure-mail':f.status==='uncertain'?'data-go="history"':`data-send="${f.id}"`} ${!f.available||f.sent_at||busy?'disabled':''}>${f.sent_at?'Già inviato':!state.mail_ready?'Configura invio':busy?(f.status==='sending'?'Invio…':'In coda'):f.status==='uncertain'?'Verifica invio':sendLabel}</button>`;
}
function fileCard(f,selectable=true){const saved=state.reading_list_files[f.id];return `<article class="card file-card ${selected.has(f.id)?'selected':''}">${bookCover(f)}<div class="file-info"><h3 class="file-title">${esc(f.name)}</h3><p class="file-meta">${esc(f.format)} · ${size(f.size)} · ${esc(f.origin==='upload'?window.KindleI18n.t('Caricato dall’app'):f.folder==='.'?window.KindleI18n.t('Libreria del server'):f.folder)}</p>${badge(f.available?f.status:'failed')}<div class="book-actions">${bookActions(f)}${f.available?readingListButton('file',f.id,saved):''}</div><details><summary>Dettagli e invio automatico</summary><p>${esc(f.available?(f.detail||(f.sent_at?'Presente nel registro degli invii. La consegna va verificata sul Kindle.':'Puoi leggere il libro o inviarlo subito.')):'File non più disponibile nella libreria.')}</p>${f.sent_at?`<p>Registro SMTP: ${formatDate(f.sent_at,true)}</p>`:''}<label class="check-line"><input type="checkbox" data-automatic="${f.id}" ${f.auto_send?'checked':''}><span>Invia anche in automatico</span></label><p>Disattiva per scegliere tu quando inviarlo. La lettura non avvia invii.</p></details></div>${selectable?`<label class="select-box"><span class="sr-only">Seleziona ${esc(f.name)}</span><input type="checkbox" data-select="${f.id}" ${selected.has(f.id)?'checked':''} ${!f.available?'disabled':''}></label>`:''}</article>`;}
function readingListButton(source,key,saved){
 return `<button class="secondary" type="button" data-reading-list-source="${source}" data-reading-list-key="${esc(key)}" data-reading-list-id="${esc(saved||'')}">${saved?'Rimuovi da Da leggere':'Salva in Da leggere'}</button>`;
}
function savedRecommendation(book,requireSource=false){
 return state?.reading_list.find(entry=>(!requireSource||entry.url)&&(entry.url===book.url||
  (book.reading_isbn&&entry.reading_isbn===book.reading_isbn)||
  (book.reading_key&&entry.reading_key===book.reading_key)))?.id;
}
function renderReadingList(){
 const files=new Map(state.files.map(file=>[file.id,file]));
 const saved=state.reading_list;
 const signature=JSON.stringify([saved,saved.map(entry=>files.get(entry.file_id)),state.mail_ready,window.KindleI18n.language]);
 if(signature!==renderReadingList.signature){
  const focused=document.activeElement;
  const key=focused?.dataset.readingListKey;
  const hadFocus=$('#reading-list').contains(focused);
  $('#reading-list-loading').hidden=true;
  $('#reading-list').innerHTML=window.KindleI18n.html(saved.map(entry=>{
   const file=files.get(entry.file_id);
   const cover=file?.cover_url||entry.cover_url||'/static/cover-placeholder.svg';
   return `<article class="card file-card"><img class="book-cover" src="${esc(cover)}" alt="" width="48" height="72" loading="lazy" decoding="async"><div class="file-info"><h2 class="file-title" data-untranslated>${esc(entry.title)}</h2>${entry.author?`<p class="small muted" data-untranslated>${esc(entry.author)}</p>`:''}${file?'':`<p class="small muted">${entry.url?'Da scaricare o caricare':'File non più disponibile nella libreria.'}</p>`}<div class="book-actions">${file?bookActions(file,'Invia al Kindle ↗'):entry.url?`<button class="primary" type="button" data-saved-download="${entry.id}">Scarica</button><a class="recommendation-link" href="${esc(entry.url)}" target="_blank" rel="noopener noreferrer">Vedi il libro ↗</a>`:''}${readingListButton('remove',entry.id,entry.id)}</div></div></article>`;
  }).join('')||'<div class="empty"><strong>La lista è vuota.</strong>Salva un titolo dalla Libreria o da Consigliati.</div>');
  if(hadFocus&&key)($(`#reading-list [data-reading-list-key="${CSS.escape(key)}"]`)||$('#reading-list-heading')).focus();
  renderReadingList.signature=signature;
 }
 const savedSignature=JSON.stringify(saved.map(entry=>[entry.id,entry.url,entry.reading_key,entry.reading_isbn]));
 if(savedSignature!==renderReadingList.savedSignature){
  renderReadingList.savedSignature=savedSignature;
  window.dispatchEvent(new Event('kindle:reading-list'));
 }
}
let readingListBusy=false;
document.addEventListener('click',async event=>{
 const button=event.target.closest('[data-reading-list-source]');
 if(!button||readingListBusy)return;
 const {readingListSource:source,readingListKey:key,readingListId:id}=button.dataset;
 const body=id?{remove_id:id}:source==='file'?{file_id:key}:{recommendation_url:key};
 const epoch=window.KindleAccounts.epoch;
 readingListBusy=true;
 button.disabled=true;
 try{
  await api('/api/reading-list',body);
  if(refreshTask?.epoch===epoch)await refreshTask.promise;
  if(epoch!==window.KindleAccounts.epoch)return;
  await refresh();
  if(epoch===window.KindleAccounts.epoch)toast(id?'Rimosso da Da leggere.':'Salvato in Da leggere.');
 }catch(error){if(error.name!=='AbortError')toast(error.message);}
 finally{readingListBusy=false;button.disabled=false;}
});
document.addEventListener('click',event=>{
 const button=event.target.closest('[data-saved-download]');
 if(!button)return;
 const entry=state?.reading_list.find(book=>book.id===button.dataset.savedDownload);
 if(entry)window.dispatchEvent(new CustomEvent('kindle:saved-download',{detail:entry}));
});
function renderFiles(){if(!state)return;renderLibraryFilters();const all=visibleFiles();const files=all.slice(0,fileLimit);$('#file-count').textContent=files.length<all.length?window.KindleI18n.t('{shown} di {count} documenti',{shown:files.length,count:all.length}):window.KindleI18n.t(all.length===1?'{count} documento':'{count} documenti',{count:all.length});$('#load-more').hidden=files.length>=all.length;$('#file-list').innerHTML=window.KindleI18n.html(files.map(f=>fileCard(f)).join('')||'<div class="empty"><strong>Nessun documento qui.</strong>Carica un file o cambia ricerca e filtro.</div>');updateSelection();}
function updateSelection(){$('#shell').classList.toggle('has-selection',selected.size>0);$('#selection-bar').hidden=selected.size===0;$('#selection-count').textContent=window.KindleI18n.t(selected.size+' selezionat'+(selected.size===1?'o':'i'));$('#send-selected').textContent=window.KindleI18n.t('Invia ora'+(selected.size?' ('+selected.size+')':'')+' ↗');$$('[data-select]').forEach(x=>{x.checked=selected.has(x.dataset.select);x.closest('.file-card').classList.toggle('selected',x.checked);});}
function render(){if(!state)return;$$('.upload-result[data-file-id]').forEach(row=>{if(row.dataset.sendNow!=='true')return;const f=state.files.find(f=>f.id===row.dataset.fileId);if(f){const note=row.querySelector('.upload-status');if(note){note.textContent=window.KindleI18n.t(f.status==='accepted'?'Accettato da SMTP. Verifica la consegna sul Kindle.':(labels[f.status]||f.status)+(f.detail?' · '+f.detail:''));note.classList.toggle('error-text',['failed','uncertain'].includes(f.status));}}});const d=state.diagnostics,s=state.schedule;selected=new Set([...selected].filter(id=>state.files.some(f=>f.id===id)));const pending=state.files.filter(f=>f.available&&!f.sent_at&&!['queued','sending'].includes(f.status));const errors=state.files.filter(f=>['failed','uncertain'].includes(f.status));const queued=state.jobs.filter(j=>['queued','sending'].includes(j.status));$('#top-status').textContent=window.KindleI18n.t(d.worker_ok?'● Servizio attivo':'● Da controllare');$('#top-status').classList.toggle('bad',!d.worker_ok);$('#hero-title').textContent=window.KindleI18n.t(pending.length?pending.length+(pending.length===1?' documento pronto.':' documenti pronti.'):queued.length?'I tuoi libri stanno partendo.':'La libreria è aggiornata.');$('#hero-note').textContent=window.KindleI18n.t(queued.length?queued.length+' invii in coda o in corso. Seguili in Attività.':pending.length?'Scegli i titoli e inviali al tuo Kindle.':'Aggiungi un nuovo documento per la tua prossima lettura.');$('#home-send').textContent=window.KindleI18n.t('Controlla e invia ora ↗');$('#next-send').textContent=window.KindleI18n.t(s.paused?'In pausa':formatDate(s.next_run));$('#last-send').textContent=window.KindleI18n.t(formatDate(state.last_sent));$('#schedule-summary').textContent=window.KindleI18n.t('Ogni '+s.interval_minutes+' minuti');$('#schedule-next').textContent=window.KindleI18n.t(s.paused?'Programmazione sospesa. Puoi continuare a inviare manualmente.':'Prossimo controllo: '+formatDate(s.next_run,true));$('#schedule-badge').textContent=window.KindleI18n.t(s.paused?'In pausa':'Attivi');if(!scheduleLoaded){$('#interval').value=s.interval_minutes;$('#paused').checked=s.paused;scheduleLoaded=true;}
let warning=[];if(!d.worker_ok)warning.push('Il worker non risponde: gli invii restano conservati in coda.');if(!d.config_ok)warning.push('Completa la configurazione Kindle nelle impostazioni.');if(!d.library_ok)warning.push('La libreria del server non è disponibile.');if(errors.length)warning.push(errors.length+' tentativi richiedono un controllo in Attività.');$('#health-warning').hidden=!warning.length;$('#health-warning').textContent=window.KindleI18n.t(warning.join(' '));
renderHomeBooks();renderLibraryCheck();renderReadingList();
// Keep expanded details and focus stable when a poll returns unchanged lists.
const fileSignature=JSON.stringify([state.files,state.reading_list_files,state.mail_ready,window.KindleI18n.language]);if(render.fileSignature!==fileSignature){renderFiles();render.fileSignature=fileSignature;}else updateSelection();
const jobSignature=JSON.stringify([state.jobs,window.KindleI18n.language]);if(render.jobSignature!==jobSignature){$('#job-list').innerHTML=window.KindleI18n.html(state.jobs.map(j=>`<article class="card job-card"><div class="job-head">${badge(j.status)}<time class="small muted">${formatDate(j.created)}</time></div><h3 class="file-title">${esc(j.name)}</h3><p class="file-meta">${esc(window.KindleI18n.t(({manual:'Invio manuale',automatic:'Invio automatico',retry:'Nuovo tentativo'})[j.source]||j.source))}</p><p class="job-detail">${esc(j.detail||'Il worker prenderà in carico il documento.')}</p>${j.status==='sending'?'<div class="job-progress" role="progressbar" aria-label="Trasmissione SMTP in corso"></div>':''}${['failed','uncertain'].includes(j.status)?`<button class="secondary" data-retry="${j.id}">${j.status==='uncertain'?'Controlla e riprova':'Riprova invio'}</button>`:''}</article>`).join('')||'<div class="empty"><strong>La tua attività inizia qui.</strong>Gli invii compariranno appena aggiunti alla coda.</div>');render.jobSignature=jobSignature;}
$('#legacy-list').innerHTML=window.KindleI18n.html(state.legacy.map(j=>`<div class="legacy-item"><span data-untranslated>${esc(j.name)}</span><time>${formatDate(j.sent_at,true)} · Registrato dallo script originale</time></div>`).join(''));$('#config-summary').textContent=window.KindleI18n.t(d.config_ok?'Configurazione completa. Credenziali conservate sul server.':'Configurazione da completare. Campi mancanti o non validi: '+d.missing.map(k=>window.KindleI18n.t(({smtp_user:'utente SMTP',smtp_password:'password SMTP',smtp_host:'server SMTP',smtp_port:'porta SMTP',kindle_address:'indirizzo Kindle',smtp_ssl:'sicurezza TLS'})[k]||k)).join(', '));$('#diagnostics').innerHTML=window.KindleI18n.html([['App web',true],['Worker invii',d.worker_ok],['Database persistente',d.database_ok],['Libreria del server',d.library_ok],['Configurazione',d.config_ok]].map(([label,ok])=>`<div class="diagnostic"><span>${label}</span><span class="pill ${ok?'':'bad'}">${ok?'Pronto':'Da controllare'}</span></div>`).join('')+`<p class="small muted">Ultimo controllo file: ${formatDate(d.last_scan,true)}</p>`);$('#logs').innerHTML=window.KindleI18n.html(state.logs.map(l=>`<div class="log-item"><time>${formatDate(l.created,true)}</time>${esc(l.message)}</div>`).join('')||'<p class="small muted">Nessun evento.</p>');}
function renderHomeBooks(){
 const waiting=state.files.filter(f=>f.available&&!f.sent_at);
 $('#pending-count').textContent=window.KindleI18n.t(waiting.length);
 const signature=JSON.stringify([waiting,state.reading_list_files,state.mail_ready,homePendingLimit,window.KindleI18n.language]);
 if(renderHomeBooks.waiting!==signature){
  $('#home-queue').innerHTML=window.KindleI18n.html(waiting.slice(0,homePendingLimit).map(f=>fileCard(f,false)).join('')||'<div class="empty"><strong>Nessun libro in attesa.</strong>Usa Controlla cartella ora per cercare nuovi documenti.</div>');
  $('#pending-more').hidden=waiting.length<=homePendingLimit;
  renderHomeBooks.waiting=signature;
 }
 const recentSignature=JSON.stringify([state.recent_sent||[],window.KindleI18n.language]);
 if(renderHomeBooks.recent!==recentSignature){
  $('#recent-sent').innerHTML=window.KindleI18n.html((state.recent_sent||[]).map(f=>`<article class="card recent-card">${bookCover(f)}<div class="file-info"><h3 class="file-title">${esc(f.name)}</h3><time class="file-meta">${formatDate(f.sent_at,true)}</time>${badge(f.status)}${f.file_id?`<div class="book-actions"><button class="secondary" data-read="${f.file_id}">Leggi</button></div>`:''}</div></article>`).join('')||'<div class="empty">Non ci sono ancora invii nel registro.</div>');
  renderHomeBooks.recent=recentSignature;
 }
}
function renderLibraryCheck(){
 const check=state.library_check;
 const active=checking||Boolean(check&&['queued','running'].includes(check.status));
 $('#home-send').disabled=active;$('#check-library').disabled=active;
 $('#home-send').textContent=window.KindleI18n.t(active?'Controllo in corso…':!state.mail_ready?'Configura invio':'Controlla e invia ora ↗');
 $('#library-check-status').hidden=!check;
 if(check){
  $('#library-check-status').textContent=window.KindleI18n.t(check.detail+(check.status==='completed'?' '+window.KindleI18n.t('{checked} file controllati, {new} nuovi.',{checked:check.checked,new:check.new_files}):''));
  $('#library-check-status').classList.toggle('warning',check.status==='failed');
 }
}
async function requestLibraryCheck(sendNow){
 if(sendNow&&!state.mail_ready){$('#configure-mail').click();return;}
 if(checking||state?.library_check&&['queued','running'].includes(state.library_check.status))return;
 if(sendNow&&!await confirmAction('Controllare e inviare adesso?','La cartella verrà riletta subito e tutti i libri non ancora inviati saranno accodati, anche se gli invii automatici sono in pausa. I libri già registrati non saranno reinviati. Gli esiti incerti richiedono un controllo in Attività.','Controlla e invia ora'))return;
 checking=true;renderLibraryCheck();
 try{const result=await api('/api/library/check',{send_now:sendNow});state.library_check=result.check;toast('Controllo richiesto. Puoi seguirlo nella Home.');}
 catch(e){toast(e.message);}
 finally{checking=false;renderLibraryCheck();await refresh();}
}
function refresh(){
 if(!window.KindleAccounts.active)return Promise.resolve();
 const epoch=window.KindleAccounts.epoch;
 if(refreshTask?.epoch===epoch)return refreshTask.promise;
 const task={epoch};
 task.promise=(async()=>{
  try{state=await api('/api/state');render();}
  catch(error){if(error.name!=='AbortError'&&epoch===window.KindleAccounts.epoch){$('#top-status').textContent=window.KindleI18n.t('● Connessione assente');$('#top-status').classList.add('bad');}}
  finally{if(refreshTask===task)refreshTask=null;}
 })();
 refreshTask=task;
 return task.promise;
}
async function sendSelected(){if(!selected.size)return;if(!state.mail_ready){$('#configure-mail').click();return;}if(!await confirmAction('Inviare adesso?',`${selected.size} documenti saranno aggiunti alla coda immediata. I contenuti già registrati non verranno reinviati.`,'Invia ora'))return;const button=$('#send-selected');button.disabled=true;try{const data=await api('/api/send',{ids:[...selected]});selected.clear();toast(data.results.every(r=>r.status==='accepted')?'Documenti già registrati: nessun nuovo invio.':'Invii aggiunti alla coda. Puoi seguirli qui.');await refresh();go('history');}catch(e){toast(e.message);}finally{button.disabled=false;}}
async function upload(sendNow){const files=[...$('#file-input').files],uploadEpoch=window.KindleAccounts.epoch;if(!files.length||uploading)return;if(sendNow&&!await confirmAction('Caricare e inviare?',`${files.length} documenti saranno caricati e aggiunti subito alla coda di invio.`,'Carica e invia ora'))return;uploading=true;$('#upload-only').disabled=true;$('#upload-send').disabled=true;$('#file-input').disabled=true;$('#upload-progress').hidden=false;$('#upload-progress').replaceChildren();for(const file of files){if(uploadEpoch!==window.KindleAccounts.epoch)break;const row=document.createElement('div');row.className='upload-result';const name=document.createElement('strong');name.textContent=file.name;const note=document.createElement('p');const progress=document.createElement('progress');progress.max=100;progress.value=0;row.append(name,note,progress);$('#upload-progress').append(row);if(file.size>45*1024*1024||file.size===0){note.textContent=window.KindleI18n.t('Non caricato: il file deve avere contenuto e non superare 45 MB.');progress.remove();continue;}try{const session=await api('/api/session');csrf=session.csrf;note.textContent=window.KindleI18n.t('Caricamento sul server…');const data=await new Promise((resolve,reject)=>{const xhr=new XMLHttpRequest();xhr.open('POST','/api/upload');xhr.timeout=180000;xhr.setRequestHeader('X-CSRF-Token',csrf);xhr.setRequestHeader('Accept-Language',window.KindleI18n.language);xhr.setRequestHeader('X-Account-ID',window.KindleAccounts.user.id);activeUploads.add(xhr);xhr.onabort=()=>reject(new DOMException('Account cambiato.','AbortError'));xhr.onloadend=()=>activeUploads.delete(xhr);xhr.upload.onprogress=e=>{if(e.lengthComputable){progress.value=Math.round(e.loaded/e.total*100);note.textContent=window.KindleI18n.t('Caricamento '+progress.value+'%');}};xhr.upload.onload=()=>note.textContent=window.KindleI18n.t('Caricato. Validazione del documento…');xhr.onerror=()=>reject(new Error('Connessione interrotta. Controlla File e Attività prima di riprovare.'));xhr.ontimeout=()=>reject(new Error('Risposta scaduta. Controlla File e Attività prima di riprovare.'));xhr.onload=()=>{if(uploadEpoch!==window.KindleAccounts.epoch){reject(new DOMException('Account cambiato.','AbortError'));return;}if([401,409].includes(xhr.status))window.KindleAccounts.showPicker(true);let data;try{data=JSON.parse(xhr.responseText);}catch{reject(new Error('Risposta non disponibile. Controlla File e Attività.'));return;}if(xhr.status>=200&&xhr.status<300)resolve(data);else reject(new Error(data.error||'Caricamento non riuscito.'));};const body=new FormData();body.append('file',file);body.append('send_now',String(sendNow));xhr.send(body);});progress.remove();row.dataset.fileId=data.file_id;row.dataset.sendNow=String(sendNow);note.classList.add('upload-status');note.textContent=window.KindleI18n.t(sendNow?(data.results[0]?.status==='accepted'?'Già registrato: invio duplicato evitato.':'Caricato e in coda. Esito SMTP disponibile in Attività.'):'Caricato nella libreria.');}catch(e){note.className='error-text';note.textContent=window.KindleI18n.t(e.message);progress.remove();}}if(uploadEpoch!==window.KindleAccounts.epoch){uploading=false;return;}uploading=false;$('#file-input').disabled=false;$('#file-input').value='';$('#picked-names').textContent=window.KindleI18n.t('Puoi selezionare più documenti');await refresh();toast(sendNow?'Caricamento terminato. Controlla qui gli esiti e in Attività gli invii.':'Caricamento terminato. Controlla gli esiti.');}
document.addEventListener('click',async e=>{if(e.target.closest('[data-configure-mail]')){$('#configure-mail').click();return;}const read=e.target.closest('[data-read]');if(read){location.hash='read='+read.dataset.read;return;}const send=e.target.closest('[data-send]');if(send){sendBook(send.dataset.send,send);return;}const goButton=e.target.closest('[data-go]');if(goButton)go(goButton.dataset.go);if(e.target.closest('[data-upload]')){go('files');$('#upload-panel').scrollIntoView({block:'start'});$('#file-input').click();}const f=e.target.closest('[data-filter]');if(f){filter=f.dataset.filter;fileLimit=30;$$('[data-filter]').forEach(b=>{b.classList.toggle('active',b===f);b.setAttribute('aria-pressed',String(b===f));});renderFiles();}const retry=e.target.closest('[data-retry]');if(retry){const job=state.jobs.find(j=>j.id===Number(retry.dataset.retry));if(!await confirmAction('Riprovare l’invio?',job.status==='uncertain'?'Controlla prima il Kindle e la posta: il precedente invio potrebbe essere stato accettato. Confermando autorizzi un nuovo tentativo, con possibile duplicato.':'Il documento sarà aggiunto nuovamente alla coda immediata.','Riprova invio'))return;retry.disabled=true;try{await api('/api/retry/'+job.id,{confirmed:job.status==='uncertain'});await refresh();toast('Nuovo tentativo aggiunto alla coda.');}catch(err){toast(err.message);}finally{retry.disabled=false;}}});
document.addEventListener('change',e=>{if(e.target.matches('[data-select]')){const id=e.target.dataset.select;if(e.target.checked){if(selected.size>=100){e.target.checked=false;toast('Puoi selezionare fino a 100 file per volta.');return;}selected.add(id);}else selected.delete(id);updateSelection();}});
$('#search').addEventListener('input',()=>{fileLimit=30;renderFiles();});$('#load-more').onclick=()=>{fileLimit+=30;renderFiles();};$('#clear-selection').onclick=()=>{selected.clear();updateSelection();};$('#select-visible').onclick=()=>{visibleFiles().slice(0,fileLimit).filter(f=>f.available).forEach(f=>{if(selected.size<100)selected.add(f.id);});updateSelection();};$('#send-selected').onclick=sendSelected;
$('#home-send').onclick=()=>requestLibraryCheck(true);
$('#check-library').onclick=()=>requestLibraryCheck(false);
$('#pending-more').onclick=()=>{homePendingLimit+=20;renderHomeBooks();};
$('#open-pending').onclick=()=>{go('files');filter='pending';fileLimit=30;$$('[data-filter]').forEach(b=>{b.classList.toggle('active',b.dataset.filter===filter);b.setAttribute('aria-pressed',String(b.dataset.filter===filter));});renderFiles();};
$('#file-input').onchange=()=>{const files=[...$('#file-input').files];$('#picked-names').textContent=files.map(f=>f.name).join(' · ')||window.KindleI18n.t('Puoi selezionare più documenti');$('#upload-only').disabled=!files.length;$('#upload-send').disabled=!files.length;};$('#upload-only').onclick=()=>upload(false);$('#upload-send').onclick=()=>upload(true);
$('#delete-selected').onclick=async()=>{if(!await confirmAction('Eliminare i file selezionati?','I caricamenti verranno eliminati definitivamente. I file della libreria del server saranno rimossi solo dal catalogo: gli originali rimangono al loro posto. La cronologia viene conservata.','Elimina selezionati'))return;try{await api('/api/delete',{ids:[...selected]});selected.clear();await refresh();toast('File rimossi.');}catch(e){toast(e.message);}};
$('#schedule-form').onsubmit=async e=>{e.preventDefault();const paused=$('#paused').checked;if(!await confirmAction('Aggiornare la programmazione?',paused?'I nuovi controlli automatici saranno sospesi. Gli invii già in coda continuano.':'Il prossimo controllo avverrà fra '+$('#interval').value+' minuti.','Salva programmazione'))return;try{await api('/api/schedule',{interval_minutes:Number($('#interval').value),paused});await refresh();toast('Programmazione aggiornata.');}catch(err){toast(err.message);}};
$('#config-form').onsubmit=async e=>{e.preventDefault();if(!await confirmAction('Salvare il collegamento?','I campi compilati sostituiranno i valori salvati. Gli altri restano invariati.','Salva impostazioni'))return;const button=e.target.querySelector('button');button.disabled=true;try{await api('/api/config',Object.fromEntries(new FormData(e.target)));e.target.reset();$('#check-result').textContent=window.KindleI18n.t('');await refresh();toast('Impostazioni salvate. Verifica la connessione SMTP.');}catch(err){toast(err.message);}finally{button.disabled=false;}};
$('#check-config').onclick=async()=>{const button=$('#check-config');button.disabled=true;$('#check-result').textContent=window.KindleI18n.t('Verifica TLS e autenticazione in corso…');try{const r=await api('/api/config/check',{});$('#check-result').textContent=window.KindleI18n.t(r.message);await refresh();}catch(e){$('#check-result').textContent=window.KindleI18n.t(e.message);}finally{button.disabled=false;}};
window.addEventListener('hashchange',()=>{if(appStarted)go(location.hash.slice(1));});
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&appStarted)refresh();});
let appStarted=false;
window.KindleI18n.ready.then(()=>window.KindleAccounts.start());
setInterval(()=>{if(!document.hidden&&appStarted&&window.KindleAccounts.active)refresh();},4000);
window.addEventListener('kindle:account',event=>{appStarted=Boolean(event.detail.user);$('#toast').hidden=true;state=null;selected.clear();scheduleLoaded=false;csrf='';render.fileSignature=null;render.jobSignature=null;renderReadingList.signature=null;renderReadingList.savedSignature=null;$('#reading-list').replaceChildren();$('#reading-list-loading').hidden=false;renderHomeBooks.waiting=null;renderHomeBooks.recent=null;$('#upload-progress').replaceChildren();$('#upload-progress').hidden=true;$('#config-form').reset();$('#schedule-form').reset();for(const id of ['legacy-list','logs','diagnostics','config-summary','check-result','last-send','next-send','file-count','health-warning'])$('#'+id).replaceChildren();$('#file-input').value='';$('#search').value='';filter='all';fileLimit=30;homePendingLimit=20;$('#job-list').replaceChildren();$('#file-list').replaceChildren();$('#home-queue').replaceChildren();$('#recent-sent').replaceChildren();if(appStarted){go(event.detail.restoreRoute?(location.hash.slice(1)||'home'):'home',false);refresh();}});
window.addEventListener('kindle:language',()=>{if(appStarted){render();refresh();}});

document.addEventListener('error',e=>{if(e.target.matches?.('img.book-cover')&&!e.target.dataset.fallback){e.target.dataset.fallback='true';e.target.src='/static/cover-placeholder.svg';}},true);

async function sendBook(id,button){button.disabled=true;try{const result=await api('/api/send',{ids:[id]});toast(result.results[0]?.status==='accepted'?'Libro già registrato: nessun nuovo invio.':'Invio richiesto subito. Puoi seguirlo in Attività.');await refresh();}catch(e){toast(e.message);}finally{if(button.isConnected)button.disabled=false;}}
window.sendBook=sendBook;
document.addEventListener('change',async e=>{if(!e.target.matches('[data-automatic]'))return;const input=e.target,enabled=input.checked;input.disabled=true;try{await api('/api/files/'+input.dataset.automatic+'/automatic',{enabled});await refresh();toast(enabled?'Invio automatico abilitato per questo libro.':'Invio automatico disattivato. Puoi continuare a leggere e inviare manualmente.');}catch(err){input.checked=!enabled;toast(err.message);}finally{input.disabled=false;}});
for(const selector of ['#folder-filter','#format-filter'])$(selector).onchange=()=>{fileLimit=30;renderFiles();};
$('#files-refresh').onclick=()=>requestLibraryCheck(false);
