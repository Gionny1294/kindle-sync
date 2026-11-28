'use strict';
(() => {
 const get=id=>document.getElementById(id);
 const t=key=>window.KindleI18n.t(key);
 const names={plant:'Pianta',bricks:'Costruzione in mattoncini',bookend:'Fermalibri',photo:'Foto su stand'};
 const picker=get('shelf-object-dialog'), form=get('shelf-object-form'), file=get('shelf-photo-file');
 const detail=get('shelf-object-details'), remove=get('object-details-remove');
 const template=get('shelf-object-template');
 let activeCase='', activeObject, busy=false;

 function label(object){return object.name||t(names[object.kind]);}
 function error(message=''){
  const box=form.querySelector('.error-text');
  box.textContent=message;
  box.hidden=!message;
 }
 function controls(){
  form.querySelectorAll('[data-object-kind]').forEach(button=>button.disabled=busy);
  form.querySelector('[type="submit"]').disabled=busy||!file.files.length;
  file.disabled=busy;
 }
 function show(caseId){
  if(busy)return;
  activeCase=caseId;
  form.reset();
  controls();
  error();
  picker.showModal();
 }
 function make(object,caseId){
  const tile=template.content.firstElementChild.cloneNode(true);
  tile.dataset.objectKind=object.kind;
  const button=tile.querySelector('button');
  button.dataset.shelfItem='object:'+object.id;
  button.dataset.caseKey=caseId;
  button.title=label(object);
  button.querySelector('.sr-only').textContent=label(object);
  tile.querySelector('img').src=object.url;
  return tile;
 }
 function showDetails(object){
  if(!object)return;
  activeObject=object;
  get('object-details-heading').textContent=label(object);
  const photo=get('object-details-image');
  photo.src=object.url;
  photo.dataset.kind=object.kind;
  photo.alt=label(object);
  detail.showModal();
 }
 async function reload(epoch){
  if(refreshTask?.epoch===epoch)await refreshTask.promise;
  if(epoch===window.KindleAccounts.epoch)await refresh();
 }
 async function add(kind,chosenFile){
  if(busy)return;
  const epoch=window.KindleAccounts.epoch;
  const caseId=activeCase;
  busy=true;
  error();
  controls();
  try{
   const body={operation:'add',kind,case_id:caseId};
   if(chosenFile){
    if(chosenFile.size>22*1024*1024)throw new Error(t('Scegli una foto più piccola.'));
    const url=URL.createObjectURL(chosenFile);
    try{
     const image=new Image();
     image.src=url;
     await image.decode();
     const scale=Math.min(1,760/image.naturalWidth,760/image.naturalHeight);
     const canvas=document.createElement('canvas');
     canvas.width=Math.max(1,Math.round(image.naturalWidth*scale));
     canvas.height=Math.max(1,Math.round(image.naturalHeight*scale));
     const ctx=canvas.getContext('2d',{alpha:false});
     ctx.fillStyle='#fff';
     ctx.fillRect(0,0,canvas.width,canvas.height);
     ctx.drawImage(image,0,0,canvas.width,canvas.height);
     body.photo=canvas.toDataURL('image/webp',.88).split(',')[1];
     body.name=chosenFile.name.replace(/\.[^.]+$/,'').slice(0,100);
    }catch{throw new Error(t('Impossibile aprire la foto.'));}
    finally{URL.revokeObjectURL(url);}
   }
   if(epoch!==window.KindleAccounts.epoch||!picker.open)return;
   await api('/api/shelf-objects',body);
   if(epoch!==window.KindleAccounts.epoch)return;
   picker.close();
   await reload(epoch);
  }catch(cause){
   if(cause.name!=='AbortError'&&epoch===window.KindleAccounts.epoch)error(cause.message);
  }finally{busy=false;controls();}
 }
 file.addEventListener('change',controls);
 form.addEventListener('click',event=>{
  const button=event.target.closest('[data-object-kind]');
  if(button)add(button.dataset.objectKind);
 });
 form.addEventListener('submit',event=>{
  event.preventDefault();
  if(file.files[0])add('photo',file.files[0]);
 });
 remove.addEventListener('click',async()=>{
  if(!activeObject||remove.disabled)return;
  const epoch=window.KindleAccounts.epoch;
  remove.disabled=true;
  try{
   await api('/api/shelf-objects',{operation:'delete',id:activeObject.id});
   if(epoch!==window.KindleAccounts.epoch)return;
   detail.close();
   await reload(epoch);
  }catch(cause){
   if(cause.name!=='AbortError')toast(cause.message);
  }finally{remove.disabled=false;}
 });
 window.addEventListener('kindle:account',()=>{
  for(const dialog of [picker,detail])if(dialog.open)dialog.close();
  activeObject=null;
 });
 window.KindleShelfObjects={show,make,showDetails};
})();
