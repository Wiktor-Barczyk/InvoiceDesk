'use strict';
// Independent panel scrolling and a persistent document-list toggle.
function setDocuments(collapsed){
  $('app').classList.toggle('documents-collapsed',collapsed);
  $('toggleDocuments').setAttribute('aria-expanded',String(!collapsed));
  $('toggleDocuments').textContent=collapsed?'☰ Menu':'☰ Zwiń menu';
  try{localStorage.setItem('invoice-documents-collapsed',String(collapsed));}catch{}
}
let savedCollapse=null;try{savedCollapse=localStorage.getItem('invoice-documents-collapsed');}catch{}
setDocuments(savedCollapse===null?matchMedia('(max-width:650px)').matches:savedCollapse==='true');
$('toggleDocuments').onclick=()=>setDocuments(!$('app').classList.contains('documents-collapsed'));
function setView(view){
  S.view=view;
  $('workspace').classList.toggle('view-form',view==='form');
  $('workspace').classList.toggle('view-preview',view==='preview');
  document.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.view===view)));
}
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>setView(b.dataset.view));
const smallWorkspace=matchMedia('(max-width:1000px)');
setView(smallWorkspace.matches?'form':'both');
smallWorkspace.addEventListener('change',e=>{if(e.matches&&S.view==='both')setView('form');});

function preparationName(mode){return mode==='mcp'?'ChatGPT':mode==='ai'?'AI':'OCR';}
const jobLabels={queued:'W kolejce',processing:'Odczytywanie',ready:'Gotowe do sprawdzenia',review:'Wymaga uwagi',failed:'Błąd odczytu',cancelled:'Odczyt przerwany'};
function renderPreparation(){
  const panel=$('aiPanel'),state=S.state,job=state?.preparation;
  panel.hidden=!state?.background||!!state?.invoice||!['received','to_book','needs_explanation','waiting_for_documents'].includes(state?.document.status);
  if(panel.hidden)return;
  const newer=S.latestVersion&&S.latestVersion!==S.form?.version;
  const needsSplit=job?.needs_split||state.ocr?.raw_result?.needs_split;
  const name=preparationName(job?.mode);
  let message=job?.status==='processing'?'Dane są przygotowywane w tle. Możesz przejść do innej faktury.':
    job?.status==='queued'?(job.error_message|| (job.mode==='mcp'?'Dokument czeka na zadanie okresowe ChatGPT.':'Dokument czeka na odczyt w tle. Formularz uzupełni się po zakończeniu i ponownym otwarciu.')):
    job?.status==='failed'?job.error_message:
    job?.status==='cancelled'?'Odczyt przerwano, ponieważ dokument zmienił się podczas przetwarzania.':
    needsSplit?'Rozpoznano kilka faktur lub paragonów. Rozdziel je przed sprawdzeniem kwot.':
    'Odczyt jest propozycją. Sprawdź kwoty, daty i przypisania przed zatwierdzeniem.';
  panel.classList.toggle('warning',!!needsSplit||['failed','review'].includes(job?.status));
  panel.innerHTML=`<strong>${esc(name)} · ${esc(jobLabels[job?.status]||'Oczekiwanie na kolejkę')}</strong><p>${esc(message)}</p>`;
  if(newer){
    panel.innerHTML+='<p><strong>Dostępne są nowsze dane. Twój formularz pozostał bez zmian.</strong></p><button type="button" class="text-button" data-ai="refresh">Wczytaj aktualne dane</button>';
    if(S.dirty)panel.innerHTML+='<button type="button" class="text-button" data-ai="keep">Zachowaj moje zmiany</button>';
  }
  if((!job||['failed','cancelled'].includes(job.status))&&S.user?.role!=='read_only')panel.innerHTML+='<button type="button" class="text-button" data-ai="retry">Ponów odczyt w tle</button>';
}
$('aiPanel').onclick=async e=>{
  const action=e.target.closest('[data-ai]')?.dataset.ai;if(!action||S.busy)return;
  const id=S.docId;
  try{
    if(action==='refresh'){if(guard())await openDocument(id);}
    if(action==='retry'){await api(`/api/documents/${id}/prepare`,{method:'POST'});await pollPreparation();toast('Dodano dokument do kolejki.');}
    if(action==='keep'){
      const current=await api(`/api/documents/${id}`);
      if(id!==S.docId)return;
      if(current.invoice||!['received','to_book','needs_explanation','waiting_for_documents'].includes(current.document.status))throw new Error('Dokument został zmieniony. Otwórz aktualne dane przed dalszą pracą.');
      S.state.document=current.document;S.state.preparation=current.preparation;
      S.form.version=current.document.updated_at;S.latestVersion=S.form.version;
      dirty();renderPreparation();toast('Zachowano Twój formularz. Sprawdź go ponownie przed zatwierdzeniem.');
    }
  }catch(error){toast(error.message,true);}
};
let polling=false, nextPoll=null;
async function pollPreparation(){
  if(polling||!S.user||!S.clientId||document.hidden||S.busy||$('splitDialog').open)return;
  polling=true;const clientId=S.clientId,id=S.docId;
  try{
    const ids=S.docs.slice(0,200).map(d=>d.id).join(',');
    const summary=await api(`/api/processing?client_id=${clientId}&doc_ids=${encodeURIComponent(ids)}`);
    if(clientId!==S.clientId)return;
    S.processing=summary;$('aiSummary').hidden=!summary.enabled;
    if(!summary.enabled)return;
    const c=summary.counts;
    for(const job of summary.jobs||[]){
      const doc=S.docs.find(d=>d.id===job.accounting_document_id);
      if(doc){doc.preparation_status=job.status;doc.preparation_mode=job.mode;}
      const item=$('documentList').querySelector(`[data-doc="${job.accounting_document_id}"]`);
      if(item){let label=item.querySelector('.doc-job');if(!label){label=document.createElement('span');label.className='doc-job';item.append(label);}label.textContent=(preparationName(job.mode)+' · ')+(jobLabels[job.status]||'');}
    }
    $('aiSummary').textContent=`${preparationName(summary.mode)}: w kolejce ${c.queued||0} · odczyt ${c.processing||0} · gotowe ${(c.ready||0)+(c.review||0)}${c.failed?' · błędy '+c.failed:''}${summary.mode==='mcp'?' · przetwarzanie według harmonogramu ChatGPT':summary.worker?.online?'':' · proces w tle nieaktywny'}`;
    if((c.failed||c.cancelled)&&S.user.role!=='read_only'){
      const retry=document.createElement('button');retry.type='button';retry.className='text-button';retry.textContent='Ponów nieudane odczyty';
      retry.onclick=async()=>{retry.disabled=true;try{const r=await api(`/api/processing/retry?client_id=${clientId}`,{method:'POST'});toast(`Ponowiono odczyt ${r.queued} dokumentów.`);await pollPreparation();}catch(e){toast(e.message,true);}finally{retry.disabled=false;}};
      $('aiSummary').append(document.createElement('br'),retry);
    }
    if(id){
      const result=await api(`/api/documents/${id}/preparation`);
      if(clientId!==S.clientId||id!==S.docId||S.busy||!S.state)return;
      S.state.preparation=result.job;S.latestVersion=result.version;renderPreparation();
    }
  }catch(error){if(S.user){$('aiSummary').hidden=false;$('aiSummary').textContent='Nie udało się odświeżyć stanu odczytu.';}}
  finally{polling=false;}
}
function schedulePreparation(){
  clearTimeout(nextPoll);
  nextPoll=setTimeout(async()=>{await pollPreparation();schedulePreparation();},15000);
}
document.addEventListener('visibilitychange',()=>{if(!document.hidden)pollPreparation();});
schedulePreparation();

$('topLogout').onclick=()=>$('logoutBtn').click();

