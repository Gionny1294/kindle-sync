'use strict';
(() => {
 const get=id=>document.getElementById(id);
 const translate=(key,values)=>window.KindleI18n.t(key,values);
 const pageSize=48;
 const select=get('shelves-select'), search=get('shelves-search');
 const rack=get('reading-list'), caseTemplate=get('bookcase-template'), volumeTemplate=get('shelf-volume-template');
 const detailDialog=get('shelf-title-dialog'), detailContent=get('shelf-title-content');
 const arrangeToggle=get('shelves-arrange'), arrangeHelp=get('shelves-arrange-help');
 const editDialog=get('shelves-edit-dialog'), editForm=get('shelves-edit-form');
 const bookDialog=get('shelves-book-dialog'), bookForm=get('shelves-book-form');
 const importDialog=get('shelves-import-dialog'), importForm=get('shelves-import-form');
 const csvInput=get('shelves-csv'), summary=get('shelves-import-summary');
 const confirmImport=get('shelves-import-confirm');
 let data, status='all', limit=pageSize, selection='', editId='', bookId='', pendingCsv='';
 let lastOptions='', lastBookcases='', detailId='', lastDetails='';
 let arranging=false,reordering=false,dragging=null,dropTarget=null,scrollFrame=0,heldClick=null,openedShelfId='';

 function error(form,message=''){
  const line=form.querySelector('.error-text');
  line.textContent=message;
  line.hidden=!message;
 }

 function applyStatus(next) {
  status=next;
  limit=pageSize;
  document.querySelectorAll('[data-shelf-status]').forEach(button=>{
   const active=button.dataset.shelfStatus===status;
   button.classList.toggle('active',active);
   button.setAttribute('aria-pressed',String(active));
  });
 }

 function render() {
  if(data)renderReadingList();
 }

 function str(value) {
  return value.toLocaleLowerCase(window.KindleI18n.locale).normalize('NFD').replace(/\p{M}/gu,'');
 }

 function updatePicker(shelves) {
  const signature=JSON.stringify([shelves.map(({id,name})=>[id,name]),window.KindleI18n.language]);
  if(signature===lastOptions)return;
  const all=new Option(translate('Tutte le librerie'),'');
  const options=shelves.map(shelf=>new Option(shelf.name,shelf.id));
  for(const option of options)option.dataset.untranslated='';
  select.replaceChildren(all,...options);
  lastOptions=signature;
 }

 function groupVolumes(model,shelves,books,chosen,filtering){
  const indexed=new Map(books.map(book=>['book:'+book.id,{book}]));
  const members=(caseKey,name)=>{
   const objects=filtering?[]:model.library_objects[caseKey]||[];
   const objectIndex=new Map(objects.map(item=>['object:'+item.id,{object:item}]));
   const matches=(model.library_layouts[caseKey]||[]).flatMap(key=>{
    const item=indexed.get(key)||objectIndex.get(key);
    return item?[item]:[];
   });
   return {key:caseKey,name,count:matches.filter(item=>item.book).length,
    more:matches.length>limit,items:matches.slice(0,limit)};
  };
  const named=(chosen?[chosen]:shelves).map(shelf=>members(shelf.id,shelf.name));
  if(chosen)return named;
  const unsorted=members('',translate('Senza libreria'));
  if(unsorted.items.length||!shelves.length)named.push(unsorted);
  return named.filter(group=>group.items.length||!filtering);
 }

 function makeVolume(book, caseKey) {
  const tile=volumeTemplate.content.firstElementChild.cloneNode(true);
  const button=tile.querySelector('button');
  button.dataset.shelfVolume=book.id;
  button.dataset.shelfItem='book:'+book.id;
  button.dataset.caseKey=caseKey;
  const author=book.author?' · '+book.author:'';
  const marking=book.is_read&&Number.isFinite(book.personal_rating)
   ?translate('Voto personale {value} su 5',{value:book.personal_rating.toLocaleString(window.KindleI18n.locale)})
   :translate(book.is_read?'Letto':'Da leggere');
  button.title=book.title+author+' · '+marking;
  button.querySelector('.sr-only').textContent=book.title+author+' · '+marking;
  const cover=tile.querySelector('.shelf-volume-cover');
  const color=[...book.title].reduce((hash,letter)=>Math.imul(hash,31)+letter.codePointAt(0),0);
  cover.dataset.binding=String((color>>>0)%6);
  cover.querySelector('.shelf-volume-title').textContent=book.title;
  const displayed=book.spine?.active?{src:book.spine.url}:book.cover;
  if(book.spine?.active){
   tile.dataset.facing='spine';
   tile.dataset.height=String(book.spine.height);
   tile.dataset.width=String(book.spine.width);
  }
  const mark=tile.querySelector('.shelf-volume-mark');
  mark.hidden=!book.is_read;
  mark.dataset.shelfUnread=book.id;
  mark.dataset.caseKey=caseKey;
  mark.title=translate('Segna come da leggere');
  mark.setAttribute('aria-label',mark.title+' · '+book.title);
  if(displayed.src){
   const image=cover.querySelector('img');
   if(displayed.fallback)image.dataset.coverFallback=displayed.fallback;
   image.src=displayed.src;
   image.hidden=false;
  }
  return tile;
 }

 function makeCase(group, filtering) {
  const node=caseTemplate.content.firstElementChild.cloneNode(true);
  node.dataset.bookcase=group.key;
  const heading=node.querySelector('h2');
  heading.id='bookcase-'+(group.key||'unassigned');
  heading.textContent=group.name;
  node.setAttribute('aria-labelledby',heading.id);
  node.querySelector('.bookcase-count').textContent=group.count===1
   ?translate('1 libro'):translate('{count} libri',{count:group.count});
  const add=node.querySelector('.bookcase-add');
  add.dataset.addToBookcase=group.key;
  add.setAttribute('aria-label',translate('Aggiungi libro')+' · '+group.name);
  add.title=translate('Aggiungi libro');
  const shelves=node.querySelector('ul');
  const objects=node.querySelector('.bookcase-object');
  objects.dataset.objectsFor=group.key;
  objects.setAttribute('aria-label',translate('Oggetti')+' · '+group.name);
  if(group.items.length){
   shelves.append(...group.items.map(item=>item.book?makeVolume(item.book,group.key):
    window.KindleShelfObjects.make(item.object,group.key)));
  }else{
   const message=document.createElement('li');
   message.className='bookcase-empty';
   message.textContent=translate(filtering?'Nessun libro trovato':'Questa libreria è vuota.');
   shelves.append(message);
  }
  return node;
 }

 function renderDetails(model) {
  if(!detailId)return;
  const entry=model.library_books.find(book=>book.id===detailId);
  if(!entry){detailDialog.close();get('reading-list-heading').focus({preventScroll:true});return;}
  const file=model.files.find(file=>file.id===entry.file_id);
  const details=entry;
  const spine=model.library_spines[entry.id], inQueue=model.reading_list.some(book=>book.id===entry.id);
  const signature=JSON.stringify([details,file,spine,inQueue,model.mail_ready,window.KindleI18n.language]);
  if(signature!==lastDetails){
   const previous=detailContent.contains(document.activeElement)?document.activeElement:null;
   const key=previous?.dataset.readingListRead;
   get('shelf-title-heading').textContent=entry.title;
   detailContent.innerHTML=window.KindleI18n.html(savedBookDetails(entry,file,spine,inQueue));
   lastDetails=signature;
   if(key)detailContent.querySelector('[data-reading-list-read]')?.focus({preventScroll:true});
  }
  syncReadingMarks(detailContent);
 }

 function renderBookcases(model) {
  data=model;
  const shelves=model.reading_shelves||[];
  updatePicker(shelves);
  const chosen=shelves.find(item=>item.id===selection);
  selection=chosen?.id||'';
  select.value=selection;
  get('shelves-edit').hidden=!chosen;

  const words=str(search.value.trim()).split(/\s+/).filter(Boolean);
  const filtering=words.length>0||status!=='all';
  const files=new Map(model.files.map(file=>[file.id,file]));
  const activeIds=chosen?new Set(chosen.books):null;
  const volumes=model.library_books.filter(book=>{
   if(activeIds&&!activeIds.has(book.id))return false;
   if(status!=='all'&&book.is_read!==(status==='read'))return false;
   if(!words.length)return true;
   const haystack=str(book.title+' '+book.author);
   return words.every(word=>haystack.includes(word));
  }).map(book=>({id:book.id,title:book.title,author:book.author,is_read:book.is_read,
   personal_rating:book.personal_rating, cover:savedBookCover(book,files.get(book.file_id)),spine:model.library_spines[book.id]}));
  const groups=groupVolumes(model,shelves,volumes,chosen,filtering);
  get('shelves-more').hidden=!groups.some(group=>group.more);
  const signature=JSON.stringify([groups,filtering,window.KindleI18n.language]);
  if(signature!==lastBookcases){
   const previous=rack.contains(document.activeElement)?document.activeElement:null;
   const nodes=groups.map(group=>makeCase(group,filtering));
   if(!nodes.length){
    const empty=document.createElement('p');
    empty.className='empty';
    empty.textContent=translate('Nessun libro trovato');
    nodes.push(empty);
   }
   rack.replaceChildren(...nodes);
   lastBookcases=signature;
   if(previous?.dataset.shelfItem){
    const {shelfItem:id,caseKey}=previous.dataset;
    const replacement=[...rack.querySelectorAll('[data-shelf-item]')].find(button=>
     button.dataset.shelfItem===id&&button.dataset.caseKey===caseKey);
    (replacement||document.querySelector('[data-shelf-status][aria-pressed="true"]'))?.focus({preventScroll:true});
   }else if(previous?.hasAttribute('data-add-to-bookcase')){
    [...rack.querySelectorAll('[data-add-to-bookcase]')].find(button=>
     button.dataset.addToBookcase===previous.dataset.addToBookcase)?.focus({preventScroll:true});
   }
  }
  renderDetails(model);
 }

 function showDetails(id) {
  if(!data?.library_books.some(book=>book.id===id))return;
  detailId=id;
  lastDetails='';
  renderDetails(data);
  detailDialog.showModal();
 }

 function openVolume(id){
  const entry=data?.library_books.find(item=>item.id===id);
  const linked=entry?.file_id&&data.files.find(file=>file.id===entry.file_id&&file.available);
  if(linked){openedShelfId=id;location.hash='read='+linked.id;}
  else showDetails(id);
 }
 function hasFile(fileId){return Boolean(data?.library_files[fileId]);}
 function showFile(fileId){
  const entry=data?.library_books.find(item=>item.id===openedShelfId&&item.file_id===fileId);
  const identifier=entry?.id||data?.library_files[fileId];
  if(identifier)showDetails(identifier);
 }

 function showShelf(shelf) {
  editId=shelf?.id||'';
  const input=editForm.elements.name;
  input.value=shelf?.name||'';
  error(editForm);
  get('shelves-edit-title').textContent=translate(shelf?'Modifica libreria':'Nuova libreria');
  get('shelves-remove').hidden=!shelf;
  editDialog.showModal();
 }

 function showBook(id, caseKey=selection) {
  const book=id&&(data?.library_books.find(item=>item.id===id)||data?.reading_list.find(item=>item.id===id));
  if(id&&!book)return;
  bookId=id||'';
  bookForm.reset();
  error(bookForm);
  get('shelves-book-title').textContent=translate(book?'Scegli librerie':'Aggiungi libro');
  get('shelves-book-fields').hidden=Boolean(book);
  bookForm.elements.title.required=!book;
  for(const field of bookForm.querySelectorAll('#shelves-book-fields input,#shelves-book-fields select'))field.disabled=Boolean(book);
  const name=get('shelves-book-name');
  name.hidden=!book;
  name.textContent=book?.title||'';
  const options=get('shelves-book-options');
  options.replaceChildren();
  for(const shelf of data?.reading_shelves||[]){
   const label=document.createElement('label');
   label.className='check-line';
   const input=document.createElement('input');
   input.type='checkbox';
   input.name='shelves';
   input.value=shelf.id;
   input.checked=book?shelf.books.includes(book.id):shelf.id===caseKey;
   const text=document.createElement('span');
   text.dataset.untranslated='';
   text.textContent=shelf.name;
   label.append(input,text);
   options.append(label);
  }
  if(!options.childElementCount)options.textContent=translate('Crea una libreria per assegnarle i libri.');
  bookDialog.showModal();
  if(!book)bookForm.elements.title.focus();
 }

 async function submit(form,dialog,action) {
  const button=form.querySelector('[type="submit"]');
  if(button.disabled)return;
  button.disabled=true;
  error(form);
  const epoch=window.KindleAccounts.epoch;
  try{
   if(refreshTask?.epoch===epoch)await refreshTask.promise;
   if(epoch!==window.KindleAccounts.epoch)return;
   if(await action()===false)return;
   if(epoch!==window.KindleAccounts.epoch)return;
   dialog.close();
   await refresh();
  }catch(cause){
   if(epoch===window.KindleAccounts.epoch&&cause.name!=='AbortError')error(form,cause.message);
  }finally{button.disabled=false;}
 }

 async function editLibrary(target,path,body,done){
  if(target.disabled)return;
  const epoch=window.KindleAccounts.epoch;
  target.disabled=true;
  try{
   await api(path,body);
   if(refreshTask?.epoch===epoch)await refreshTask.promise;
   if(epoch!==window.KindleAccounts.epoch)return;
   await refresh();
   if(epoch===window.KindleAccounts.epoch)done?.();
  }catch(cause){
   if(cause.name!=='AbortError'&&epoch===window.KindleAccounts.epoch)toast(cause.message);
  }finally{target.disabled=false;}
 }

 function setArrange(active){
  arranging=active;
  arrangeToggle.setAttribute('aria-pressed',String(active));
  arrangeToggle.textContent=translate(active?'Fine spostamento':'Sposta libri');
  arrangeHelp.hidden=!active;
  rack.classList.toggle('bookcases-arranging',active);
  if(!active)resetDrag();
 }

 function resetDrag(){
  if(scrollFrame)cancelAnimationFrame(scrollFrame);
  scrollFrame=0;
  if(dragging?.timer)clearTimeout(dragging.timer);
  if(dragging?.slot)delete dragging.slot.dataset.dragging;
  if(dropTarget?.slot)delete dropTarget.slot.dataset.drop;
  if(dropTarget?.section)delete dropTarget.section.dataset.emptyDrop;
  dragging=dropTarget=null;
 }

 function aimDrop(){
  const live=dragging;
  if(!live?.active)return;
  const section=document.elementFromPoint(live.x,live.y)?.closest('.bookcase');
  const nearest=section?[...section.querySelectorAll('.shelf-volume')].filter(item=>item!==live.slot)
   .reduce((best,item)=>{
    const rect=item.getBoundingClientRect();
    const distance=Math.hypot(live.x-rect.x-rect.width/2,live.y-rect.y-rect.height/2);
    return !best||distance<best.distance?{slot:item,rect,distance}:best;
   },null):null;
  if(dropTarget?.slot)delete dropTarget.slot.dataset.drop;
  if(dropTarget?.section)delete dropTarget.section.dataset.emptyDrop;
  if(nearest){
   const after=live.x>nearest.rect.x+nearest.rect.width/2;
   nearest.slot.dataset.drop=after?'after':'before';
   dropTarget={section,slot:nearest.slot,after};
  }else if(section&&section!==live.section){
   section.dataset.emptyDrop='true';
   dropTarget={section,slot:null,after:true};
  }else dropTarget=null;
 }

 function dragScroll(){
  if(!dragging?.active){scrollFrame=0;return;}
  const y=dragging.y, edge=75;
  const offset=y<edge?-.18*(edge-y):y>innerHeight-edge?.18*(y-innerHeight+edge):0;
  if(offset){window.scrollBy(0,offset);aimDrop();}
  scrollFrame=requestAnimationFrame(dragScroll);
 }

 async function moveItem(button,adjacent,after,target=button.dataset.caseKey){
  if(reordering||button===adjacent)return;
  reordering=true;
  await editLibrary(button,'/api/reading-shelves',{
   operation:'move-slot',case_id:button.dataset.caseKey,item:button.dataset.shelfItem,
   target_case:target,adjacent:adjacent?.dataset.shelfItem||null,after
  },()=>{
   const fresh=[...rack.querySelectorAll('[data-shelf-item]')].find(item=>
    item.dataset.shelfItem===button.dataset.shelfItem&&item.dataset.caseKey===target);
   fresh?.focus({preventScroll:true});
  });
  reordering=false;
 }

 function armHold(button,pointer,x,y,capture=false){
  if(reordering||dragging||!button)return;
  const slot=button.closest('.shelf-volume');
  dragging={button,slot,section:slot.closest('.bookcase'),pointer,startX:x,startY:y,x,y,active:false};
  const activate=()=>{
   if(!dragging)return;
   dragging.active=true;
   dragging.slot.dataset.dragging='';
   if(capture)rack.setPointerCapture(pointer);
   scrollFrame=requestAnimationFrame(dragScroll);
  };
  if(arranging)activate();
  else dragging.timer=setTimeout(activate,390);
 }

 function moveHold(x,y){
  if(!dragging)return false;
  dragging.x=x;
  dragging.y=y;
  if(!dragging.active&&Math.hypot(x-dragging.startX,y-dragging.startY)>9)resetDrag();
  if(dragging?.active)aimDrop();
  return Boolean(dragging?.active);
 }

 function releaseHold(){
  if(!dragging)return false;
  const {button,active,startX,startY,x,y}=dragging;
  const target=dropTarget;
  resetDrag();
  if(!active)return false;
  heldClick=button;
  if(target&&Math.hypot(x-startX,y-startY)>6){
   moveItem(button,target.slot?.querySelector('[data-shelf-item]'),target.after,target.section.dataset.bookcase);
  }
  return true;
 }

 arrangeToggle.addEventListener('click',()=>setArrange(!arranging));
 rack.addEventListener('pointerdown',event=>{
  if(event.pointerType==='touch'||!event.isPrimary||event.button!==0)return;
  heldClick=null;
  armHold(event.target.closest('[data-shelf-item]'),event.pointerId,event.clientX,event.clientY,true);
 });
 window.addEventListener('pointermove',event=>{
  if(event.pointerType!=='touch'&&dragging?.pointer===event.pointerId&&moveHold(event.clientX,event.clientY))
   event.preventDefault();
 });
 window.addEventListener('pointerup',event=>{
  if(event.pointerType!=='touch'&&dragging?.pointer===event.pointerId)releaseHold();
 });
 rack.addEventListener('touchstart',event=>{
  heldClick=null;
  if(event.touches.length!==1){resetDrag();return;}
  const touch=event.touches[0];
  armHold(event.target.closest('[data-shelf-item]'),'touch:'+touch.identifier,touch.clientX,touch.clientY);
 },{passive:true});
 rack.addEventListener('touchmove',event=>{
  const touch=[...event.changedTouches].find(item=>dragging?.pointer==='touch:'+item.identifier);
  if(touch&&moveHold(touch.clientX,touch.clientY)&&event.cancelable)event.preventDefault();
 },{passive:false});
 rack.addEventListener('touchend',event=>{
  if([...event.changedTouches].some(item=>dragging?.pointer==='touch:'+item.identifier)&&releaseHold()&&event.cancelable)
   event.preventDefault();
 },{passive:false});
 for(const name of ['pointercancel','lostpointercapture','touchcancel']){
  rack.addEventListener(name,event=>{
   if(event.type==='touchcancel'||event.pointerType!=='touch')resetDrag();
  });
 }
 rack.addEventListener('contextmenu',event=>{if(event.target.closest('[data-shelf-item]'))event.preventDefault();});
 rack.addEventListener('keydown',event=>{
  const current=event.target.closest('[data-shelf-item]');
  const key=event.key;
  if(!arranging&&current?.dataset.shelfVolume&&key.toLowerCase()==='i'){showDetails(current.dataset.shelfVolume);return;}
  if(!arranging||!current||!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(key))return;
  event.preventDefault();
  const peers=[...current.closest('.bookcase').querySelectorAll('[data-shelf-item]')];
  const after=key==='ArrowRight'||key==='ArrowDown';
  let adjacent;
  if(key==='ArrowLeft'||key==='ArrowRight'){
   adjacent=peers[peers.indexOf(current)+(after?1:-1)];
  }else{
   const here=current.closest('.shelf-volume').getBoundingClientRect();
   const available=peers.map(button=>({button,rect:button.closest('.shelf-volume').getBoundingClientRect()}))
     .filter(({rect})=>after?rect.top-here.top>1:here.top-rect.top>1);
   available.sort((a,b)=>Math.abs(a.rect.top-here.top)-Math.abs(b.rect.top-here.top)||
                         Math.abs(a.rect.left-here.left)-Math.abs(b.rect.left-here.left));
   adjacent=available[0]?.button;
  }
  if(adjacent)moveItem(current,adjacent,after);
 });

 document.querySelectorAll('.shelves-close').forEach(button=>button.addEventListener('click',()=>button.closest('dialog').close()));
 document.querySelectorAll('[data-shelf-status]').forEach(button=>button.addEventListener('click',()=>{
  applyStatus(button.dataset.shelfStatus);
  render();
 }));
 select.addEventListener('change',()=>{selection=select.value; limit=pageSize; render();});
 search.addEventListener('input',()=>{limit=pageSize; render();});
 get('shelves-more').addEventListener('click',()=>{
  const known=new Set([...rack.querySelectorAll('[data-shelf-volume]')].map(button=>button.dataset.shelfVolume));
  limit+=pageSize;
  render();
  [...rack.querySelectorAll('[data-shelf-volume]')].find(button=>!known.has(button.dataset.shelfVolume))?.focus();
 });
 get('shelves-add').addEventListener('click',()=>showShelf());
 get('shelves-edit').addEventListener('click',()=>showShelf(data?.reading_shelves.find(shelf=>shelf.id===selection)));
 get('shelves-add-book').addEventListener('click',()=>showBook());
 rack.addEventListener('click',event=>{
  const volume=event.target.closest('[data-shelf-volume]');
  const add=event.target.closest('[data-add-to-bookcase]');
  const objects=event.target.closest('[data-objects-for]');
  const item=event.target.closest('[data-shelf-item]');
  if(item&&item===heldClick&&event.detail){heldClick=null;return;}
  const mark=event.target.closest('[data-shelf-unread]');
  if(volume&&!arranging)openVolume(volume.dataset.shelfVolume);
  if(add)showBook('',add.dataset.addToBookcase);
  if(objects)window.KindleShelfObjects.show(objects.dataset.objectsFor);
  if(item?.dataset.shelfItem.startsWith('object:')&&!arranging){
   const id=item.dataset.shelfItem.slice(7);
   window.KindleShelfObjects.showDetails(data.library_objects[item.dataset.caseKey]?.find(object=>object.id===id));
  }
  if(mark&&!arranging){
   const id=mark.dataset.shelfUnread;
   editLibrary(mark,'/api/reading-list/'+encodeURIComponent(id)+'/read',{is_read:false},()=>{
    const replacement=[...rack.querySelectorAll('[data-shelf-volume]')].find(button=>button.dataset.shelfVolume===id&&button.dataset.caseKey===mark.dataset.caseKey);
    (replacement||document.querySelector('[data-shelf-status][aria-pressed="true"]'))?.focus({preventScroll:true});
   });
  }
 });
 detailDialog.addEventListener('click',async event=>{
  if(event.target.closest('[data-place-book],[data-read],[data-saved-download],[data-go],[data-configure-mail]'))detailDialog.close();
  const style=event.target.closest('[data-spine-display]');
  const photo=event.target.closest('[data-spine-photo]');
  const add=event.target.closest('[data-library-queue]');
  const remove=event.target.closest('[data-library-remove]');
  const rate=event.target.closest('[data-edit-personal-rating]');
  if(rate){
   const entry=data.library_books.find(book=>book.id===rate.dataset.editPersonalRating);
   const answer=await askBookRating(entry);
   if(answer?.value!=null&&entry)editLibrary(rate,'/api/reading-list/'+encodeURIComponent(entry.id)+'/read',
    {is_read:true,personal_rating:answer.value});
  }
  if(style||photo){
   const id=style?.dataset.spineDisplay||photo.dataset.spinePhoto;
   const spine=data.library_spines[id];
   if(photo||!spine){detailDialog.close();window.KindleSpineCrop.show(id);}
   else editLibrary(style,'/api/reading-shelves/'+encodeURIComponent(id)+'/spine',{active:!spine.active});
  }
  if(add)editLibrary(add,'/api/reading-shelves',{operation:'queue-book',id:add.dataset.libraryQueue});
  if(remove&&await confirmAction('Rimuovere il libro dalla libreria?',
    'Il libro verrà tolto dai ripiani della libreria.','Rimuovi dalla libreria')){
   editLibrary(remove,'/api/reading-shelves',{operation:'remove-book',id:remove.dataset.libraryRemove});
  }
 });
 detailDialog.addEventListener('close',()=>{
  if(!detailDialog.open){detailId='';lastDetails='';}
 });
 window.addEventListener('kindle:page',event=>{
  if(event.detail.page==='bookcases')return;
  if(detailDialog.open)detailDialog.close();
  setArrange(false);
 });
 document.addEventListener('click',event=>{
  const control=event.target.closest('[data-place-book]');
  const fromFile=event.target.closest('[data-library-file]');
  if(control)showBook(control.dataset.placeBook);
  if(fromFile)editLibrary(fromFile,'/api/reading-shelves',{operation:'file-book',id:fromFile.dataset.libraryFile},
    ()=>toast('Salvato nella libreria.'));
 });
 editForm.addEventListener('submit',event=>{
  event.preventDefault();
  const body={operation:editId?'rename':'create',id:editId,name:editForm.elements.name.value};
  submit(editForm,editDialog,async()=>{
   const result=await api('/api/reading-shelves',body);
   selection=result.id;
   limit=pageSize;
  });
 });
 get('shelves-remove').addEventListener('click',async()=>{
  if(!await confirmAction('Eliminare questa libreria?','I libri restano tra i tuoi titoli e nelle altre librerie.','Elimina libreria'))return;
  submit(editForm,editDialog,async()=>{
   await api('/api/reading-shelves',{operation:'delete',id:editId});
   selection='';
  });
 });
 bookForm.addEventListener('submit',event=>{
  event.preventDefault();
  const values=new FormData(bookForm);
  const body={operation:bookId?'place-book':'add-book',id:bookId,shelves:values.getAll('shelves')};
  if(!bookId)Object.assign(body,{title:values.get('title'),author:values.get('author'),is_read:values.get('status')==='read'});
  submit(bookForm,bookDialog,async()=>{
   if(body.is_read){
    const answer=await askBookRating({title:body.title});
    if(!answer)return false;
    body.personal_rating=answer.value;
   }
   await api('/api/reading-shelves',body);
   if(!bookId){search.value='';applyStatus('all');}
  });
 });
 get('shelves-import').addEventListener('click',()=>{
  importForm.reset();
  pendingCsv='';
  error(importForm);
  summary.hidden=true;
  confirmImport.disabled=true;
  importDialog.showModal();
 });

 function importText(result) {
  return translate('{added} nuovi · {updated} già presenti · {shelves} nuovi scaffali',result)+
   (result.skipped?' · '+(result.skipped===1?translate('1 riga senza titolo ignorata'):
    translate('{count} righe senza titolo ignorate',{count:result.skipped})):'');
 }

 csvInput.addEventListener('change',async()=>{
  const file=csvInput.files[0];
  const epoch=window.KindleAccounts.epoch;
  summary.hidden=true;
  pendingCsv='';
  confirmImport.disabled=true;
  error(importForm);
  if(!file)return;
  try{
   if(file.size>8*1024*1024)throw new Error(translate('Scegli un CSV Goodreads di massimo 8 MB.'));
   const csv=await file.text();
   const result=await api('/api/reading-shelves/goodreads',{csv,preview:true});
   if(epoch!==window.KindleAccounts.epoch||csvInput.files[0]!==file||!importDialog.open)return;
   pendingCsv=csv;
   summary.textContent=importText(result);
   summary.hidden=false;
   confirmImport.disabled=false;
  }catch(cause){
   if(cause.name!=='AbortError'&&csvInput.files[0]===file&&importDialog.open)error(importForm,cause.message);
  }
 });
 importForm.addEventListener('submit',event=>{
  event.preventDefault();
  const csv=pendingCsv;
  if(!csv)return;
  submit(importForm,importDialog,async()=>{
   const result=await api('/api/reading-shelves/goodreads',{csv});
   selection='';
   search.value='';
   applyStatus('all');
   toast(importText(result));
  });
 });
 window.addEventListener('kindle:account',()=>{
  data=undefined;
  openedShelfId='';
  selection='';
  search.value='';
  pendingCsv='';
  applyStatus('all');
  lastBookcases='';
  setArrange(false);
  detailId='';
  lastDetails='';
  for(const dialog of [editDialog,bookDialog,importDialog,detailDialog])if(dialog.open)dialog.close();
 });
 window.KindleShelves={render:renderBookcases,hasFile,showFile};
})();
