'use strict';
(() => {
 const get=id=>document.getElementById(id);
 const dialog=get('spine-crop-dialog'), form=get('spine-crop-form'), input=get('spine-file');
 const work=get('spine-crop-work'), canvas=get('spine-canvas'), save=get('spine-save'), warning=get('spine-error');
 const context=canvas.getContext('2d');
 const controls=[...form.querySelectorAll('[data-crop]')];
 const translate=key=>window.KindleI18n.t(key);
 let image, selection, dragging, currentId, activeUrl='', opening=0;

 function error(message=''){
  warning.textContent=message;
  warning.hidden=!message;
 }
 function clear(){
  if(activeUrl)URL.revokeObjectURL(activeUrl);
  activeUrl='';
  image=selection=dragging=null;
  work.hidden=true;
  save.disabled=true;
 }
 function show(id){
  currentId=id;
  opening++;
  form.reset();
  clear();
  error();
  dialog.showModal();
  input.focus();
 }
 function bound(value,minimum,maximum){
  return Math.min(Math.max(value,minimum),maximum);
 }
 function adjust(rect){
  const w=bound(rect.w,.03,1),h=bound(rect.h,.05,1);
  return {w,h,x:bound(rect.x,0,1-w),y:bound(rect.y,0,1-h)};
 }
 function draw(){
  if(!image||!selection)return;
  const {width,height}=canvas;
  const {x,y,w,h}=selection;
  const left=x*width, top=y*height, boxWidth=w*width, boxHeight=h*height;
  context.drawImage(image,0,0,width,height);
  context.fillStyle='#132018b7';
  context.fillRect(0,0,width,top);
  context.fillRect(0,top,left,boxHeight);
  context.fillRect(left+boxWidth,top,width-left-boxWidth,boxHeight);
  context.fillRect(0,top+boxHeight,width,height-top-boxHeight);
  context.strokeStyle='#ffdfa7';
  context.lineWidth=3;
  context.strokeRect(left,top,boxWidth,boxHeight);
  context.fillStyle='#ffdfa7';
  for(const cornerX of [left,left+boxWidth])for(const cornerY of [top,top+boxHeight]){
   context.fillRect(cornerX-5,cornerY-5,10,10);
  }
  for(const control of controls)control.value=String(Math.round(selection[control.dataset.crop]*100));
 }
 function point(event){
  const area=canvas.getBoundingClientRect();
  return {x:bound((event.clientX-area.left)/area.width,0,1),y:bound((event.clientY-area.top)/area.height,0,1),
   distanceX:12/area.width,distanceY:12/area.height};
 }
 canvas.addEventListener('pointerdown',event=>{
  if(!selection||!event.isPrimary||event.button!==0)return;
  const p=point(event), {x,y,w,h}=selection;
  const nearX=Math.abs(p.x-x)<p.distanceX?-1:Math.abs(p.x-x-w)<p.distanceX?1:0;
  const nearY=Math.abs(p.y-y)<p.distanceY?-1:Math.abs(p.y-y-h)<p.distanceY?1:0;
  const inside=p.x>=x&&p.x<=x+w&&p.y>=y&&p.y<=y+h;
  if(!inside&&!(nearX&&nearY))return;
  dragging={p,target:{...selection},handleX:nearX&&nearY?nearX:0,handleY:nearX&&nearY?nearY:0};
  canvas.setPointerCapture(event.pointerId);
 });
 canvas.addEventListener('pointermove',event=>{
  if(!dragging)return;
  const here=point(event), {p,target,handleX,handleY}=dragging;
  const dx=here.x-p.x,dy=here.y-p.y;
  if(!handleX){
   selection=adjust({...target,x:target.x+dx,y:target.y+dy});
  }else{
   const left=handleX<0?bound(target.x+dx,0,target.x+target.w-.03):target.x;
   const top=handleY<0?bound(target.y+dy,0,target.y+target.h-.05):target.y;
   const right=handleX>0?bound(target.x+target.w+dx,target.x+.03,1):target.x+target.w;
   const bottom=handleY>0?bound(target.y+target.h+dy,target.y+.05,1):target.y+target.h;
   selection=adjust({x:left,y:top,w:right-left,h:bottom-top});
  }
  draw();
 });
 for(const eventName of ['pointerup','pointercancel','lostpointercapture']){
  canvas.addEventListener(eventName,()=>{dragging=null;});
 }
 canvas.addEventListener('keydown',event=>{
  if(!selection)return;
  const delta={ArrowLeft:[-.015,0],ArrowRight:[.015,0],ArrowUp:[0,-.015],ArrowDown:[0,.015]}[event.key];
  if(!delta)return;
  event.preventDefault();
  selection=event.shiftKey
   ?adjust({...selection,w:selection.w+delta[0],h:selection.h+delta[1]})
   :adjust({...selection,x:selection.x+delta[0],y:selection.y+delta[1]});
  draw();
 });
 for(const control of controls)control.addEventListener('input',()=>{
  if(!selection)return;
  selection=adjust({...selection,[control.dataset.crop]:Number(control.value)/100});
  draw();
 });

 input.addEventListener('change',async()=>{
  const file=input.files[0], generation=++opening;
  clear();
  error();
  if(!file)return;
  const url=URL.createObjectURL(file);
  activeUrl=url;
  try{
   const source=new Image();
   source.src=url;
   await source.decode();
   if(generation!==opening||!dialog.open)return;
   if(!source.naturalWidth||source.naturalWidth*source.naturalHeight>50_000_000)throw new Error();
   image=source;
   const scale=Math.min(660/source.naturalWidth,470/source.naturalHeight);
   canvas.width=Math.max(1,Math.round(source.naturalWidth*scale));
   canvas.height=Math.max(1,Math.round(source.naturalHeight*scale));
   const w=Math.min(.5,.26*canvas.height/canvas.width);
   selection=adjust({x:(1-w)/2,y:.08,w,h:.84});
   work.hidden=false;
   save.disabled=false;
   draw();
  }catch{
   if(generation===opening)error(translate('Impossibile aprire la foto.'));
  }
 });

 form.addEventListener('submit',async event=>{
  event.preventDefault();
  if(!selection||!image||save.disabled)return;
  const generation=opening, epoch=window.KindleAccounts.epoch;
  const id=currentId;
  const x=selection.x*image.naturalWidth,y=selection.y*image.naturalHeight;
  const w=selection.w*image.naturalWidth,h=selection.h*image.naturalHeight;
  if(w/h>.7){error(translate('Ritaglia solo il dorso verticale del libro.'));return;}
  const scale=Math.min(1,330/w,960/h);
  const output=document.createElement('canvas');
  output.width=Math.max(8,Math.round(w*scale));
  output.height=Math.max(30,Math.round(h*scale));
  const target=output.getContext('2d',{alpha:false});
  target.fillStyle='#fff';
  target.fillRect(0,0,output.width,output.height);
  target.drawImage(image,x,y,w,h,0,0,output.width,output.height);
  const photo=output.toDataURL('image/webp',.88).split(',')[1];
  save.disabled=true;
  error();
  try{
   await api('/api/reading-shelves/'+encodeURIComponent(id)+'/spine',{active:true,photo});
   if(epoch!==window.KindleAccounts.epoch||generation!==opening)return;
   dialog.close();
   if(refreshTask?.epoch===epoch)await refreshTask.promise;
   if(epoch===window.KindleAccounts.epoch)await refresh();
  }catch(cause){
   if(generation===opening&&cause.name!=='AbortError')error(cause.message);
  }finally{
   if(generation===opening&&dialog.open)save.disabled=false;
  }
 });
 dialog.addEventListener('close',()=>{opening++;clear();});
 window.addEventListener('kindle:account',()=>{if(dialog.open)dialog.close();});
 window.KindleSpineCrop={show};
})();
