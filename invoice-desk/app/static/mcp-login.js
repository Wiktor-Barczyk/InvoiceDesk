'use strict';
const login=document.getElementById('login'),consent=document.getElementById('consent'),message=document.getElementById('message');
let csrf='';
async function call(url,options={}){const r=await fetch(url,{...options,headers:{'Content-Type':'application/json',...(csrf?{'X-CSRF-Token':csrf}:{}),...options.headers}});const data=await r.json();if(!r.ok)throw new Error(data.detail||'Operacja nie powiodła się.');return data;}
async function account(){const me=await call('/api/me');csrf=me.csrf;document.getElementById('account').textContent='Konto: '+me.email;login.hidden=true;consent.hidden=false;}
account().catch(()=>{});
login.onsubmit=async e=>{e.preventDefault();const button=login.querySelector('button');button.disabled=true;try{await call('/api/login',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(login)))});login.reset();await account();message.textContent='Potwierdź zakres dostępu przyciskiem poniżej.';}catch(e){message.textContent=e.message;}finally{button.disabled=false;}};
consent.onclick=async()=>{consent.disabled=true;try{const result=await call('/oauth/consent',{method:'POST',body:JSON.stringify({authorization:document.getElementById('authorization').value})});const target=new URL(result.redirect);if(target.origin!=='https://chatgpt.com')throw new Error('Nieprawidłowy adres powrotu.');location.assign(target.href);}catch(e){message.textContent=e.message;consent.disabled=false;}};
