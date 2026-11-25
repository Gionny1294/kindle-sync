'use strict';
(() => {
 const picker=document.getElementById('downloads-isbn-photo');
 const scan=document.getElementById('downloads-scan-isbn');
 const query=document.getElementById('downloads-query');
 const submit=document.getElementById('downloads-search-submit');
 const status=document.getElementById('downloads-isbn-status');
 const scriptUrl=new URL(document.currentScript.src);
 const decoderUrl=new URL('vendor/zxing/zxing.min.js',scriptUrl);
 decoderUrl.search=scriptUrl.search;
 let generation=0,statusMessage='';

 function showStatus(message){
  statusMessage=message;
  status.textContent=window.KindleI18n.t(message);
  status.hidden=!message;
 }

 async function readISBN(file){
  const url=URL.createObjectURL(file);
  try{
   const photo=new Image();
   photo.src=url;
   await photo.decode();
   const scale=Math.min(1,2048/Math.max(photo.naturalWidth,photo.naturalHeight));
   const width=Math.max(1,Math.round(photo.naturalWidth*scale));
   const height=Math.max(1,Math.round(photo.naturalHeight*scale));
   const canvas=document.createElement('canvas');
   await import(decoderUrl.href);
   const zxing=globalThis.ZXing;
   const hints=new Map([[zxing.DecodeHintType.TRY_HARDER,true]]);
   // One-dimensional readers already handle reversed bars. Try both axes for sideways photos.
   for(const rotated of [false,true]){
    canvas.width=rotated?height:width;
    canvas.height=rotated?width:height;
    const context=canvas.getContext('2d',{willReadFrequently:true});
    if(rotated){context.translate(height,0);context.rotate(Math.PI/2);}
    context.drawImage(photo,0,0,width,height);
    const pixels=zxing.HTMLCanvasElementLuminanceSource.makeBufferFromCanvasImageData(canvas);
    const source=new zxing.RGBLuminanceSource(pixels,canvas.width,canvas.height);
    const bitmap=new zxing.BinaryBitmap(new zxing.HybridBinarizer(source));
    try{
     const value=new zxing.EAN13Reader().decode(bitmap,hints).getText();
     if(/^97[89][0-9]{10}$/.test(value))return value;
    }catch(error){
     if(!(error instanceof zxing.NotFoundException||error instanceof zxing.ChecksumException||error instanceof zxing.FormatException))throw error;
    }
   }
   return '';
  }finally{URL.revokeObjectURL(url);}
 }

 scan.addEventListener('click',()=>picker.click());
 picker.addEventListener('change',async()=>{
  const file=picker.files[0];
  picker.value='';
  if(!file)return;
  const current=++generation;
  scan.disabled=true;
  showStatus('Lettura dell’ISBN…');
  try{
   const isbn=await readISBN(file);
   if(current!==generation)return;
   if(!isbn){showStatus('ISBN non trovato. Fotografa da vicino il codice a barre del libro e riprova.');return;}
   query.value=isbn;
   showStatus('ISBN inserito. Premi Scarica per cercare il libro.');
   if(!submit.disabled)submit.focus();
  }catch{
   if(current===generation)showStatus('Impossibile leggere la foto. Riprova con un’altra immagine.');
  }finally{if(current===generation)scan.disabled=false;}
 });

 query.addEventListener('input',()=>{
  generation++;
  scan.disabled=false;
  showStatus('');
 });
 window.addEventListener('kindle:account',()=>{
  generation++;
  picker.value='';
  scan.disabled=false;
  showStatus('');
 });
 window.addEventListener('kindle:language',()=>showStatus(statusMessage));
})();
