'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const icons = {
  plus:'M12 5v14M5 12h14', minus:'M5 12h14', check:'m5 12 4 4L19 6', left:'m14 6-6 6 6 6', right:'m9 6 6 6-6 6',
  file:'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M8 13h8M8 17h5',
  stack:'M4 7h16M4 12h16M4 17h16M8 3h8M8 21h8', search:'M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
  scan:'M8 3H4a1 1 0 0 0-1 1v4M16 3h4a1 1 0 0 1 1 1v4M3 16v4a1 1 0 0 0 1 1h4M21 16v4a1 1 0 0 1-1 1h-4M7 8h10M7 12h10M7 16h6',
  lock:'M5 10h14v11H5zM8 10V6a4 4 0 0 1 8 0v4M12 14v3', download:'M12 3v12m-5-5 5 5 5-5M5 16v5h14v-5',
  logout:'M9 4H4v16h5M10 12h11m-4-4 4 4-4 4', trash:'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7',
};
const icon = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${icons[name] || icons.file}"/></svg>`;
document.querySelectorAll('[data-icon]').forEach(el => el.innerHTML=icon(el.dataset.icon));
const S = {user:null, clients:[], refs:null, docs:[], state:null, form:null, clientId:null, docId:null, fileId:null, filter:'pending', dirty:false, page:1, pages:1, zoom:100, busy:false, previewUrl:null, previewSequence:0, listTotal:0, listSequence:0, openSequence:0, latestVersion:null};
let toastTimer;
function toast(message, failure=false) {$('toast').textContent=message;$('toast').classList.toggle('failure',failure);$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,6500);}
function busy(value, message='Odczytywanie dokumentu…'){S.busy=value;$('busyOverlay').hidden=!value;$('busyText').textContent=message;}
function readonly(){if(editingPosted())return false;return S.user?.role==='read_only' || !!(S.state?.document && !['received','to_book','needs_explanation','waiting_for_documents'].includes(S.state.document.status)) || !!(S.state?.invoice && !['draft','review'].includes(S.state.invoice.status));}
const decimalInput = value => {let text=String(value??'').replace(/[\s\u00a0]/g,'');if(text.includes(',')&&text.includes('.'))text=text.lastIndexOf(',')>text.lastIndexOf('.')?text.replace(/\./g,''):text.replace(/,/g,'');return text.replace('.',',');};
const decimalFields = new Set(['net_total','vat_total','gross_total','exchange_rate_to_eur']);
async function api(path, options={}) {
  const headers=new Headers(options.headers || {});
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type','application/json');
  if (S.user?.csrf) headers.set('X-CSRF-Token',S.user.csrf);
  let r;try{r=await fetch(path,{...options,headers,credentials:'same-origin'});}catch{throw new Error('Brak połączenia z aplikacją. Sprawdź sieć i spróbuj ponownie.');}
  if (!r.ok){let body;try{body=await r.json();}catch{body={detail:'Operacja nie powiodła się.'};}if(r.status===401 && path!='/api/login'){showLogin();}throw new Error(typeof body.detail==='string'?body.detail:'Formularz zawiera nieprawidłowe dane.');}
  return r.json();
}
function showLogin(){$('app').hidden=true;$('loadingScreen').hidden=true;$('loginScreen').hidden=false;S.user=null;}
async function start(){
  try{S.user=await api('/api/me');}catch(e){showLogin();if(!e.message.includes('Zaloguj'))$('loginError').textContent=e.message;return;}
  $('loginScreen').hidden=true;$('loadingScreen').hidden=true;$('app').hidden=false;
  $('userName').textContent=S.user.full_name;$('userRole').textContent=({admin:'Administrator',chief_accountant:'Główny księgowy',accountant:'Księgowość',reviewer:'Weryfikacja',read_only:'Tylko odczyt'})[S.user.role];
  $('avatar').textContent=S.user.full_name.split(/\s+/).map(x=>x[0]).slice(0,2).join('');$('modeBadge').textContent=S.user.demo?'DANE DEMONSTRACYJNE':'';
  try{S.clients=await api('/api/clients');$('clientSelect').innerHTML=S.clients.map(c=>`<option value="${c.id}">${esc(c.company_name)}</option>`).join('');
    if(S.clients.length){await changeClient(S.clients[0].id);}else{S.clientId=null;empty('Brak klientów w bazie','Najpierw zsynchronizuj kartotekę klientów z Odoo.');}
    $('uploadBtn').disabled=S.user.role==='read_only'||!S.clients.length;$('emptyUpload').disabled=$('uploadBtn').disabled;
  }catch(e){empty('Nie można wczytać danych',e.message);toast(e.message,true);}
}
function empty(title='Gotowe na pierwszą fakturę',description='Wybierz klienta i dodaj PDF, JPG lub PNG. Dane do sprawdzenia pojawią się obok dokumentu.'){
  $('workspace').hidden=true;$('emptyState').hidden=false;$('emptyState').querySelector('h2').textContent=title;$('emptyState').querySelector('p').textContent=description;
}
async function changeClient(id){closeLedger();resetInventaris();$('clientSections').hidden=false;S.openSequence++;S.previewSequence++;S.latestVersion=null;S.clientId=Number(id);S.docId=null;S.state=null;S.form=null;S.dirty=false;S.docs=[];$('workspace').hidden=true;
  S.refs=await api(`/api/references?client_id=${S.clientId}`);await reloadList();void refreshInventarisMenu();const rows=filtered();if(rows.length)await openDocument(rows[0].id);else empty();
}
async function reloadList(append=false){
  const seq=++S.listSequence,clientId=S.clientId;
  const query=new URLSearchParams({client_id:clientId,offset:append?S.docs.length:0,status:S.filter,search:$('search').value.trim()});
  $('loadMore').disabled=true;
  try{
    const rows=await api('/api/documents?'+query);
    if(seq!==S.listSequence||clientId!==S.clientId)return;
    S.docs=append?[...S.docs,...rows]:rows;S.listTotal=rows[0]?.total_count??(append?S.docs.length:0);renderList();
  }finally{if(seq===S.listSequence)$('loadMore').disabled=false;}
}
const approved = d=>['approved','posted'].includes(d.invoice_status);
function filtered(){return S.docs;}
function amount(value,currency='EUR'){try{return new Intl.NumberFormat('pl-PL',{style:'currency',currency:currency||'EUR'}).format(Number(String(value).replace(',','.')));}catch{return String(value??'—')+' '+currency;}}
function renderList(){const rows=filtered();$('queueCount').textContent=S.listTotal;$('loadMore').hidden=S.docs.length>=S.listTotal;
  $('documentList').innerHTML=rows.length?rows.map(d=>`<button class="doc-item ${d.id===S.docId?'active':''}" data-doc="${d.id}" aria-current="${d.id===S.docId?'true':'false'}"><span class="doc-top"><span>${esc(d.seller_name_snapshot||d.original_filename||d.description||'Dokument')}</span>${approved(d)?icon('check'):icon('file')}</span><span class="doc-number">${esc(d.invoice_number||d.original_filename||'Nowa faktura')}</span><span class="doc-meta"><span>${new Date(d.received_at).toLocaleDateString('pl-PL')}</span><span>${d.gross_total?esc(amount(d.gross_total,d.currency)):'Do weryfikacji'}</span></span>${d.preparation_status?`<span class="doc-job">${esc((preparationName(d.preparation_mode)+' · ')+(jobLabels[d.preparation_status]||''))}</span>`:''}</button>`).join(''):`<p class="list-empty">${S.filter==='approved'?'Nie ma jeszcze zatwierdzonych faktur.':'Brak dokumentów do sprawdzenia.'}</p>`;
}
function blankLine(){return {description:'',net_amount:'0.00',vat_amount:'0.00',booking_category_id:'',account_id:'',vat_code_id:''};}
function guard(){if(!canLeaveInventaris())return false;return !S.dirty||confirm('Masz niezapisane zmiany. Opuścić ten dokument?');}
async function openDocument(id){
  const openSequence=++S.openSequence;S.previewSequence++;
  busy(true,'Wczytywanie faktury…');
  try{const state=await api(`/api/documents/${id}`);if(openSequence!==S.openSequence)return;S.latestVersion=state.document.updated_at;S.docId=Number(id);S.state=state;S.dirty=false;S.page=1;S.zoom=100;
    const fields=state.invoice||state.ocr?.extracted_fields||{};
    S.form={client_id:S.clientId,invoice_type:'purchase',invoice_kind:'invoice',currency:S.refs.client.default_currency||'EUR',counterparty_id:'',...JSON.parse(JSON.stringify(fields))};
    delete S.form.vat_date;
    if(!S.form.buyer_name_snapshot&&S.form.invoice_type==='purchase')S.form.buyer_name_snapshot=S.refs.client.company_name;
    if(!S.form.seller_name_snapshot&&S.form.invoice_type==='sale')S.form.seller_name_snapshot=S.refs.client.company_name;
    if(!S.form.lines?.length)S.form.lines=[blankLine()];
    if(!state.invoice&&Number(fields.suggested_vat_rate)>0){const vatOptions=validVatCodes().filter(v=>v.vat_rate_percent!==null&&Number(v.vat_rate_percent)===Number(fields.suggested_vat_rate));if(vatOptions.length===1)S.form.lines.forEach(l=>{if(!l.vat_code_id)l.vat_code_id=vatOptions[0].id;});}
    S.postingCorrection=null;S.correctionReason='';S.form.reviewed=false;S.form.version=state.document.updated_at;S.form.invoice_version=state.invoice?.updated_at||null;
    renderForm();renderList();$('emptyState').hidden=true;$('workspace').hidden=false;$('editorScroll').scrollTop=0;
    $('fileChooser').innerHTML=state.files.map(f=>`<option value="${f.id}">${esc(f.original_filename)}</option>`).join('');$('fileChooserWrap').hidden=state.files.length<2;S.fileId=state.files[0]?.id;
    await setupPreview();if(typeof renderPreparation==='function'){renderPreparation();pollPreparation();}
  }catch(e){toast(e.message,true);}finally{if(openSequence===S.openSequence){busy(false);if(typeof pollPreparation==='function')pollPreparation();}}
}
function options(rows, label, value, first='Wybierz…'){return `<option value="">${esc(first)}</option>`+rows.map(r=>`<option value="${r.id}" ${String(r.id)===String(value)?'selected':''}>${esc(label(r))}</option>`).join('');}
function validVatCodes(){const d=S.form?.invoice_date||'';const direction=S.form?.invoice_type==='sale'?'output':'input';return S.refs.vat_codes.filter(v=>[direction,'both','none'].includes(v.vat_direction)&&(!d||(!v.valid_from||v.valid_from<=d)&&(!v.valid_to||v.valid_to>=d)));}
function renderForm(){
  const f=S.form;$('formError').hidden=true;$('invoiceTitle').textContent=f.invoice_number||S.state.files[0]?.original_filename||'Nowa faktura';
  const isApproved=S.state.invoice?.status==='approved';const status=readonly()?(isApproved?'Zatwierdzona':S.state.invoice?.status==='posted'?'Zaksięgowana':'Tylko odczyt'):'Do sprawdzenia';
  $('invoiceStatus').textContent=status;$('invoiceStatus').classList.toggle('approved',readonly());
  $('counterparty_id').innerHTML=options(S.refs.counterparties,x=>x.name+(x.vat_number?' · '+x.vat_number:''),f.counterparty_id,'Dopasuj automatycznie / utwórz przy zatwierdzeniu');
  document.querySelectorAll('[data-key]').forEach(el=>{el.value=decimalFields.has(el.dataset.key)?decimalInput(f[el.dataset.key]):f[el.dataset.key]??'';el.dataset.extracted=String(!S.state.invoice&&!!S.state.ocr?.raw_result?.evidence?.[el.dataset.key]);const evidence=S.state.ocr?.raw_result?.evidence?.[el.dataset.key];el.title=evidence?'Odczyt: '+evidence:'';});
  $('rateField').hidden=f.currency==='EUR';$('formFields').disabled=readonly();$('reviewed').checked=false;$('reviewed').disabled=readonly();
  $('approveBtn').hidden=readonly();$('reviewed').closest('label').hidden=readonly();$('approveBtn').disabled=true;
  $('saveCaption').textContent=isApproved?'Zapisano w bazie':'Zapis do bazy';$('saveSubcaption').textContent=isApproved?`Faktura #${S.state.invoice.id} · oczekuje na księgowanie`:'Faktura będzie oczekiwać na księgowanie';
  const warnings=S.state.ocr?.raw_result?.warnings||[];const pages=S.state.ocr?.raw_result?.pages||[];
  $('recognitionLabel').textContent=S.state.invoice?'Dane zapisane w bazie':S.state.ocr?.status==='completed'?'Odczytano dane · sprawdź z oryginałem':'Uzupełnij dane z dokumentu';
  $('recognitionBody').innerHTML=`<p>Odczyt jest propozycją. Sprawdź strony transakcji, kwoty, daty i podział pozycji. BTW-ID nie jest weryfikowany w VIES.</p>${warnings.length?'<ul>'+warnings.map(w=>`<li>${esc(w)}</li>`).join('')+'</ul>':''}${pages.length?'<details><summary>Tekst odczytany z pliku</summary><pre>'+esc(pages.map(p=>`STRONA ${p.page}\n${p.text}`).join('\n\n'))+'</pre></details>':''}`;
  $('recognitionDetails').open=false;renderLines();renderTotals();renderPostingControls();partyWarning();renderSplitContext();if(typeof renderPreparation==='function')renderPreparation();
}
function renderLines(){
  $('lines').innerHTML=S.form.lines.map((line,i)=>`<div class="line-card"><div class="line-head"><span>POZYCJA ${String(i+1).padStart(2,'0')}</span><button type="button" class="icon-button" data-delete="${i}" aria-label="Usuń pozycję ${i+1}" ${S.form.lines.length===1?'disabled':''}>${icon('trash')}</button></div><div class="fields two">
    <div class="field full"><label for="line-${i}-description">Opis</label><input id="line-${i}-description" data-line="${i}" data-prop="description" value="${esc(line.description)}" placeholder="Czego dotyczy pozycja?" required></div>
    <div class="field"><label for="line-${i}-quantity">Ilość · opcjonalnie</label><input id="line-${i}-quantity" data-line="${i}" data-prop="quantity" value="${esc(decimalInput(line.quantity))}" inputmode="decimal"></div>
    <div class="field"><label for="line-${i}-unit">Cena netto · opcjonalnie</label><input id="line-${i}-unit" data-line="${i}" data-prop="unit_price" value="${esc(decimalInput(line.unit_price))}" inputmode="decimal"></div>
    <div class="field"><label for="line-${i}-booking">${S.refs.booking_catalog?'Konto GnuCash':'Kategoria / konto'}</label><div class="booking-control">${S.refs.booking_catalog&&!readonly()?'<input type="search" data-booking-search="'+i+'" aria-label="Szukaj konta po numerze lub nazwie" placeholder="Szukaj konta, np. 430" autocomplete="off">':''}<select id="line-${i}-booking" data-line="${i}" data-prop="booking_choice" required>${bookingOptions(line)}</select></div></div>
    <div class="field"><label for="line-${i}-vat">Kod VAT</label><select id="line-${i}-vat" data-line="${i}" data-prop="vat_code_id" required>${options(validVatCodes().sort((a,b)=>Number(a.vat_rate_percent ?? Infinity)-Number(b.vat_rate_percent ?? Infinity)),x=>x.vat_name,line.vat_code_id)}</select></div>
    <div class="field"><label for="line-${i}-net">Netto</label><input id="line-${i}-net" data-line="${i}" data-prop="net_amount" value="${esc(decimalInput(line.net_amount))}" inputmode="decimal" required></div>
    <div class="field"><label for="line-${i}-tax">VAT</label><input id="line-${i}-tax" data-line="${i}" data-prop="vat_amount" value="${esc(decimalInput(line.vat_amount))}" inputmode="decimal" required></div>
    </div></div>`).join('');
  renderPostingVat();
}
function renderTotals(){const f=S.form;if(!f)return;const net=cents(f.net_total),vat=cents(f.vat_total),gross=cents(f.gross_total);let good=net!==null&&vat!==null&&gross!==null&&net+vat===gross;let lineNet=0n,lineVat=0n;
  for(const l of f.lines){const n=cents(l.net_amount),v=cents(l.vat_amount);if(n===null||v===null)good=false;else{lineNet+=n;lineVat+=v;}}
  good=good&&lineNet===net&&lineVat===vat;
  $('sumCheck').classList.toggle('good',good);$('sumCheck').innerHTML=(good?icon('check'):'')+esc(good?'Netto, VAT i sumy pozycji zgadzają się.':'Sprawdź zgodność kwot i sum pozycji.');
  $('totalDisplay').textContent=formatCents(gross,f.currency);renderAccountSummary();syncPostingButton();
}
function partyWarning(){const key=S.form.invoice_type==='purchase'?'buyer_name_snapshot':'seller_name_snapshot';const clean=x=>String(x||'').toLowerCase().replace(/[^\p{L}\p{N}]/gu,'');const mismatch=clean(S.form[key])!==clean(S.refs.client.company_name);$('partyWarning').hidden=!mismatch;$('partyWarning').textContent='Nazwa klienta na fakturze różni się od wybranej kartoteki. Sprawdź, czy dokument należy do tego klienta.';}
function dirty(){S.dirty=true;if(typeof renderPreparation==='function')renderPreparation();S.form.reviewed=false;$('reviewed').checked=false;$('approveBtn').disabled=true;}
async function setupPreview(){
  S.page=1;S.zoom=100;setZoom(100);$('documentImage').hidden=true;$('previewMessage').hidden=false;$('previewMessage').textContent='Wczytywanie podglądu…';
  if(!S.fileId){$('previewMessage').textContent='Brak załączonego dokumentu.';return;}
  const file=S.state.files.find(x=>x.id===Number(S.fileId));$('filename').textContent=file?.original_filename||'';$('downloadLink').href=`/api/documents/${S.docId}/file?file_id=${S.fileId}`;
  try{const meta=await api(`/api/documents/${S.docId}/pages?file_id=${S.fileId}`);S.pages=meta.pages;$('previewMeta').textContent=`${meta.extension.slice(1).toUpperCase()} · ${meta.pages} str.`;await loadPage();}catch(e){$('previewMessage').textContent=e.message;}
}
async function loadPage(){
  const sequence=++S.previewSequence;$('pageIndicator').textContent=`${S.page} / ${S.pages}`;$('prevPage').disabled=S.page<=1;$('nextPage').disabled=S.page>=S.pages;
  $('previewMessage').hidden=false;$('previewMessage').textContent='Wczytywanie strony…';$('documentImage').hidden=true;
  try{const r=await fetch(`/api/documents/${S.docId}/preview/${S.page}?file_id=${S.fileId}`,{credentials:'same-origin'});if(!r.ok){const data=await r.json();throw new Error(data.detail||'Nie można wyświetlić strony.');}const blob=await r.blob();if(sequence!==S.previewSequence)return;
    if(S.previewUrl)URL.revokeObjectURL(S.previewUrl);S.previewUrl=URL.createObjectURL(blob);const img=$('documentImage');img.onload=()=>{if(sequence===S.previewSequence){img.hidden=false;$('previewMessage').hidden=true;}};img.onerror=()=>{$('previewMessage').textContent='Nie udało się wyświetlić obrazu dokumentu.';};img.src=S.previewUrl;img.alt=`Oryginał faktury — strona ${S.page}`;
  }catch(e){if(sequence===S.previewSequence)$('previewMessage').textContent=e.message;}
}
function setZoom(value){S.zoom=Math.max(20,Math.min(300,value));$('zoomReset').textContent=`${S.zoom}%`;$('documentImage').style.width=S.zoom+'%';}
$('loginForm').addEventListener('submit',async e=>{e.preventDefault();$('loginError').textContent='';$('loginSubmit').disabled=true;try{await api('/api/login',{method:'POST',body:JSON.stringify({email:$('email').value,password:$('password').value})});$('password').value='';await start();}catch(err){$('loginError').textContent=err.message;}finally{$('loginSubmit').disabled=false;}});
$('logoutBtn').onclick=async()=>{if(!guard())return;try{await api('/api/logout',{method:'POST'});S.dirty=false;showLogin();}catch(e){toast(e.message,true);}};
$('clientSelect').onchange=async e=>{if(!guard()){e.target.value=S.clientId;return;}busy(true,'Wczytywanie klienta…');try{await changeClient(e.target.value);}catch(err){toast(err.message,true);}finally{busy(false);}};
let searchTimer;
$('search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>reloadList().catch(e=>toast(e.message,true)),250);};
$('documentList').onclick=async e=>{const b=e.target.closest('[data-doc]');if(b&&!S.busy&&Number(b.dataset.doc)!==S.docId&&guard())await openDocument(Number(b.dataset.doc));};
for(const [id,filter] of [['pendingTab','pending'],['approvedTab','approved']])$(id).onclick=()=>{S.filter=filter;$('pendingTab').classList.toggle('selected',filter==='pending');$('approvedTab').classList.toggle('selected',filter==='approved');reloadList().catch(e=>toast(e.message,true));};
$('loadMore').onclick=()=>reloadList(true).catch(e=>toast(e.message,true));
for(const id of ['uploadBtn','emptyUpload'])$(id).onclick=()=>{if(guard())$('fileInput').click();};
$('fileInput').onchange=async e=>{const files=[...e.target.files];if(!files.length)return;const clientId=S.clientId;busy(true);let lastId,success=0,duplicates=0,splitCount=0;
  try{for(let i=0;i<files.length;i++){busy(true,`Przesyłanie ${i+1}/${files.length}: ${files[i].name}`);const form=new FormData();form.append('client_id',clientId);form.append('file',files[i]);const result=await api('/api/documents/upload',{method:'POST',body:form});lastId=result.split_children?.[0]?.id||result.id;splitCount+=result.split_children?.length||0;if(typeof window.rememberAutoSplit==='function')window.rememberAutoSplit(result.id,result.auto_split);if(result.duplicate)duplicates++;else success++;}
    if(splitCount)toast(`Automatycznie wydzielono: ${splitCount} dokumenty. Sprawdź dane każdego z nich.`);else toast(duplicates?`Dodano: ${success}. Już w bazie: ${duplicates}.`:`Dodano ${success} dokumentów. Odczyt sprawdzisz po otwarciu dokumentu.`);
  }catch(err){toast(err.message,true);}finally{e.target.value='';S.dirty=false;try{await reloadList();if(lastId)await openDocument(lastId);}catch(err){toast(err.message,true);}busy(false);}
};
$('invoiceForm').onsubmit=e=>e.preventDefault();
$('invoiceForm').addEventListener('input',e=>{const el=e.target;if(readonly())return;
  if(el.dataset.bookingSearch!==undefined){const i=Number(el.dataset.bookingSearch);$('line-'+i+'-booking').innerHTML=bookingOptions(S.form.lines[i],el.value);return;}
  if(el.dataset.key){const key=el.dataset.key;S.form[key]=el.value;dirty();if(key==='currency'){$('rateField').hidden=el.value.toUpperCase()==='EUR';}renderTotals();partyWarning();}
  if(el.dataset.line!==undefined&&el.dataset.prop!=='booking_choice'){S.form.lines[Number(el.dataset.line)][el.dataset.prop]=el.value;dirty();renderTotals();}
});
$('invoiceForm').addEventListener('change',e=>{const el=e.target;if(readonly())return;
  if(decimalFields.has(el.dataset.key)||(el.dataset.line!==undefined&&['quantity','unit_price','net_amount','vat_amount'].includes(el.dataset.prop))){el.value=decimalInput(el.value);if(el.dataset.key)S.form[el.dataset.key]=el.value;else S.form.lines[Number(el.dataset.line)][el.dataset.prop]=el.value;}
  if(['invoice_type','invoice_date'].includes(el.dataset.key)){S.form.lines.forEach(l=>{if(!validVatCodes().some(v=>String(v.id)===String(l.vat_code_id)))l.vat_code_id='';});renderLines();partyWarning();}
  if(el.dataset.key==='counterparty_id'&&el.value){const cp=S.refs.counterparties.find(x=>String(x.id)===el.value);const prefix=S.form.invoice_type==='purchase'?'seller':'buyer';if(cp){for(const [key,value] of [[`${prefix}_name_snapshot`,cp.name],[`${prefix}_vat_number_snapshot`,cp.vat_number]]){S.form[key]=value||'';$(key).value=value||'';}if(prefix==='seller'){S.form.seller_iban_snapshot=cp.iban||'';$('seller_iban_snapshot').value=cp.iban||'';}}}
  if(el.dataset.prop==='vat_amount')renderPostingVat();
  if(el.dataset.prop==='booking_choice')chooseBooking(Number(el.dataset.line),el.value);
});
$('addLine').onclick=()=>{S.form.lines.push(blankLine());dirty();renderLines();renderTotals();};
$('lines').onclick=e=>{const b=e.target.closest('[data-delete]');if(b&&S.form.lines.length>1){S.form.lines.splice(Number(b.dataset.delete),1);dirty();renderLines();renderTotals();}};
$('reviewed').onchange=e=>{S.form.reviewed=e.target.checked;renderTotals();};
$('approveBtn').onclick=()=>bookCurrent();
$('fileChooser').onchange=async e=>{S.fileId=Number(e.target.value);await setupPreview();};
$('prevPage').onclick=()=>{if(S.page>1){S.page--;loadPage();}};$('nextPage').onclick=()=>{if(S.page<S.pages){S.page++;loadPage();}};
$('zoomIn').onclick=()=>setZoom(S.zoom+20);$('zoomOut').onclick=()=>setZoom(S.zoom-20);$('zoomReset').onclick=()=>setZoom(100);
window.addEventListener('beforeunload',e=>{if(S.dirty||inventarisState.dirty){e.preventDefault();e.returnValue='';}});
// Start after all deferred modules have registered their controls.
window.addEventListener('DOMContentLoaded',start,{once:true});
