'use strict';
(() => {
 const el=id=>document.getElementById(id),T=(text,values)=>window.KindleI18n.t(text,values);
 let user=null,sessionInfo=null,csrf='',phase='closed',epoch=0,selected=null,busy=false;
 const incomingActivation=new URLSearchParams(location.hash.slice(1)).get('activate')?.trim()||'';
 let activation=incomingActivation;
 try{activation=activation||sessionStorage.getItem('kindle-owner-activation')||'';if(incomingActivation)sessionStorage.setItem('kindle-owner-activation',incomingActivation);}catch{}
 if(incomingActivation)history.replaceState(null,'',location.pathname+location.search+'#home');
 function forgetActivation(){activation='';try{sessionStorage.removeItem('kindle-owner-activation');}catch{}}

 const helpLinks={gmail:'https://support.google.com/accounts/answer/185833',icloud:'https://support.apple.com/en-ie/102525',yahoo:'https://help.yahoo.com/kb/sln4724.html'};
 function providerHelp(){const url=helpLinks[el('setup-provider').value];el('setup-help').hidden=!url;if(url)el('setup-help').href=url;}
 const providers={gmail:['smtp.gmail.com','465','true'],icloud:['smtp.mail.me.com','587','false'],yahoo:['smtp.mail.yahoo.com','465','true']};
 function headers(){return {'Accept-Language':window.KindleI18n.language,...(user?{'X-Account-ID':user.id}:{})};}
 async function request(path,body){
  const generation=epoch,authentication=path.startsWith('/api/auth/')||path==='/api/session';
  const response=await fetch(path,{method:body===undefined?'GET':'POST',credentials:'same-origin',headers:{...(authentication?{'Accept-Language':window.KindleI18n.language}:headers()),...(body===undefined?{}:{'Content-Type':'application/json','X-CSRF-Token':csrf})},body:body===undefined?undefined:JSON.stringify(body)});
  const data=await response.json();
  if(!authentication&&generation!==epoch)throw new DOMException('Account cambiato.','AbortError');
  if(!response.ok){if(!authentication&&[401,409].includes(response.status))showPicker(true);throw Object.assign(new Error(data.error||T('Operazione non riuscita.')),{code:data.code});}
  if(path.startsWith('/api/auth/')){try{localStorage.setItem('kindle-auth-change',Date.now()+':'+Math.random());}catch{}}
  return data;
 }
 function notice(id,message){el(id).textContent=T(message);el(id).hidden=!message;}
 function forms(mode=null){el('account-login').hidden=mode!=='login';el('account-register').hidden=mode!=='register';el('account-list').hidden=Boolean(mode);el('account-create').hidden=Boolean(mode);notice('account-error','');}
 function render(){
  if(!sessionInfo)return;
  el('account-list').replaceChildren();
  for(const account of sessionInfo.accounts){
   const button=document.createElement('button');button.className='account-choice';button.type='button';button.dataset.accountId=account.id;
   const avatar=document.createElement('span');avatar.className='account-avatar';avatar.textContent=account.name.slice(0,1).toLocaleUpperCase();avatar.setAttribute('aria-hidden','true');
   const label=document.createElement('span');label.textContent=account.name;const action=document.createElement('span');action.className='small muted';action.textContent=T(sessionInfo.user?.id===account.id?'Continua':account.password_required?'Accedi':'Apri');
   button.append(avatar,label,action);button.onclick=()=>choose(account);el('account-list').append(button);
  }
  el('account-owner-code-label').hidden=!sessionInfo.owner_code_required;el('account-owner-code').required=Boolean(sessionInfo.owner_code_required);
  if(!sessionInfo.owner_code_required){forgetActivation();el('account-owner-code').value='';}
  el('account-owner-code').readOnly=Boolean(activation);
  if(activation)el('account-owner-code').value=activation;
  if(selected)el('account-login-title').textContent=T('Accedi a {name}',{name:selected.name});
 }
 async function start(){
  document.body.classList.add('account-pending');el('account-screen').hidden=false;phase='picker';forms();
  try{sessionInfo=await request('/api/session');csrf=sessionInfo.csrf;render();if(!sessionInfo.accounts.length)forms('register');}
  catch(error){notice('account-error',error.message);}
 }
 async function showPicker(force=false){
  if(!force&&window.appIsUploading?.()){window.toast?.(T('Attendi la fine del caricamento prima di cambiare account.'));return;}
  window.closeBook?.();window.cancelUploads?.();epoch++;phase='picker';user=null;
  if(el('confirm-dialog').open)el('confirm-dialog').close('cancel');
  el('account-login').reset();el('account-register').reset();el('setup-form').reset();el('account-password-form').reset();notice('account-password-error','');el('setup-screen').hidden=true;
  window.dispatchEvent(new CustomEvent('kindle:account',{detail:{user:null}}));await start();
 }
 async function choose(account){
  if(busy)return;selected=account;notice('account-error','');
  if(sessionInfo.user?.id===account.id){user=sessionInfo.user;await openUser();return;}
  if(account.password_required===false){
   busy=true;
   try{const result=await request('/api/auth/login',{name:account.name,password:'',remember:true});csrf=result.csrf;user=result.user;sessionInfo.user=user;await openUser();}
   catch(error){notice('account-error',error.message);}finally{busy=false;}
   return;
  }
  forms('login');render();el('account-login-name').value=account.name;el('account-login-password').value='';el('account-login-password').focus();
 }
 async function openUser(){
  epoch++;if(user.language&&user.language!==window.KindleI18n.language)await window.KindleI18n.setLanguage(user.language);el('account-login').reset();el('account-register').reset();el('setup-form').reset();notice('setup-error','');
  try{
   const state=await request('/api/state');
   if(state.setup_required)await setup();else activate();
  }catch(error){notice('account-error',error.message);}
 }
 function activate(){
  phase='active';document.body.classList.remove('account-pending');el('account-screen').hidden=true;el('setup-screen').hidden=true;
  el('account-password-form').reset();el('account-current-password-label').hidden=!user.password_required;el('account-current-password').required=Boolean(user.password_required);
  el('current-account-name').textContent=user.name;window.dispatchEvent(new CustomEvent('kindle:account',{detail:{user}}));
 }
 async function setup(){
  phase='setup';document.body.classList.add('account-pending');el('account-screen').hidden=true;el('setup-screen').hidden=false;notice('setup-error','');
  const info=await request('/api/mail'),form=el('setup-form');form.reset();
  for(const [key,value] of Object.entries(info.settings))if(value&&form.elements[key])form.elements[key].value=value;
  form.elements.smtp_password.required=!info.password_set;
  form.elements.smtp_password.placeholder=info.password_set?T('Lascia vuoto per mantenere la password salvata.'):'';
  const match=Object.entries(providers).find(([,values])=>values[0]===form.elements.smtp_host.value);
  el('setup-provider').value=match?.[0]||'other';el('setup-advanced').open=!match;providerHelp();
  window.scrollTo(0,0);el('setup-screen').scrollTop=0;
 }
 el('account-create').onclick=()=>{if(busy)return;forms('register');el('account-register-name').focus();};
 for(const button of document.querySelectorAll('[data-account-back]'))button.onclick=()=>{if(busy)return;forms();el('account-login').reset();el('account-register').reset();render();};
 el('account-login').onsubmit=async event=>{
  event.preventDefault();if(busy)return;busy=true;el('account-login-submit').disabled=true;
  try{const result=await request('/api/auth/login',{name:selected.name,password:el('account-login-password').value,remember:el('account-login-remember').checked});csrf=result.csrf;user=result.user;sessionInfo.user=user;await openUser();}
  catch(error){notice('account-error',error.message);}finally{busy=false;el('account-login-submit').disabled=false;}
 };
 el('account-register').onsubmit=async event=>{
  event.preventDefault();if(busy)return;
  if(el('account-register-password').value!==el('account-register-confirm').value){notice('account-error','Le password non coincidono.');return;}
  busy=true;el('account-register-submit').disabled=true;
  try{const result=await request('/api/auth/register',{remember:el('account-register-remember').checked,language:window.KindleI18n.language,name:el('account-register-name').value,password:el('account-register-password').value,setup_code:activation||el('account-owner-code').value.trim()});csrf=result.csrf;user=result.user;sessionInfo.user=user;forgetActivation();await openUser();}
  catch(error){if(error.code==='activation_invalid'){forgetActivation();el('account-owner-code').readOnly=false;el('account-owner-code').focus();el('account-owner-code').select();}notice('account-error',error.message);}finally{busy=false;el('account-register-submit').disabled=false;}
 };
 el('setup-provider').onchange=()=>{providerHelp();const values=providers[el('setup-provider').value];el('setup-advanced').open=!values;if(values){for(const [key,value] of ['smtp_host','smtp_port','smtp_ssl'].map((key,index)=>[key,values[index]]))el('setup-form').elements[key].value=value;}};
 el('setup-form').onsubmit=async event=>{
  event.preventDefault();if(busy)return;busy=true;el('setup-save').disabled=true;notice('setup-error','Verifica TLS e autenticazione in corso…');
  try{await request('/api/setup/mail',{settings:Object.fromEntries(new FormData(event.target)),automatic:el('setup-automatic').checked,sender_approved:el('setup-approved').checked});event.target.reset();activate();}
  catch(error){notice('setup-error',error.message);}finally{busy=false;el('setup-save').disabled=false;}
 };
 el('setup-skip').onclick=async()=>{if(busy)return;try{await request('/api/setup/skip',{});activate();}catch(error){notice('setup-error',error.message);}};
 el('setup-back').onclick=()=>showPicker();el('change-account').onclick=()=>showPicker();
 el('configure-mail').onclick=()=>setup().catch(error=>window.toast?.(error.message));
 el('logout-account').onclick=async()=>{if(window.appIsUploading?.()){window.toast?.(T('Attendi la fine del caricamento prima di cambiare account.'));return;}try{await request('/api/auth/logout',{});await showPicker(true);}catch(error){window.toast?.(error.message);}};
 el('account-password-form').onsubmit=async event=>{
  event.preventDefault();if(busy)return;
  if(window.appIsUploading?.()){notice('account-password-error','Attendi la fine del caricamento prima di cambiare account.');return;}
  if(el('account-new-password').value!==el('account-new-password-confirm').value){notice('account-password-error','Le password non coincidono.');return;}
  busy=true;el('account-password-save').disabled=true;notice('account-password-error','');
  try{await request('/api/account/password',{current_password:el('account-current-password').value,password:el('account-new-password').value});await showPicker(true);notice('account-error','Password aggiornata. Seleziona il tuo account per continuare.');try{localStorage.setItem('kindle-auth-change',Date.now()+':'+Math.random());}catch{}}
  catch(error){notice('account-password-error',error.message);}finally{busy=false;el('account-password-save').disabled=false;}
 };
 el('account-language').onclick=()=>{el('language-welcome').hidden=false;document.body.classList.add('language-pending');};
 window.addEventListener('kindle:language',()=>{render();if(phase==='active'&&user&&user.language!==window.KindleI18n.language){user.language=window.KindleI18n.language;request('/api/account/language',{language:user.language}).catch(()=>{});}if(!el('setup-screen').hidden&&el('setup-form').elements.smtp_password.placeholder)el('setup-form').elements.smtp_password.placeholder=T('Lascia vuoto per mantenere la password salvata.');});
 window.addEventListener('storage',event=>{if(event.key==='kindle-auth-change'&&phase!=='closed')showPicker(true);});
 window.KindleAccounts={start,showPicker,request,headers,get user(){return user;},get epoch(){return epoch;},get active(){return phase==='active';},get csrf(){return csrf;}};
})();
