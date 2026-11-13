'use strict';
const inventarisState={open:false,dirty:false,saving:false,request:0,menuRequest:0,offset:0,rows:[],current:null};
const inv=inventarisState;
const inventoryUrl=(line='',suffix='')=>'/api/inventaris'+line+'?client_id='+encodeURIComponent(S.clientId)+suffix;
const invMoney=(value,currency)=>formatCents(cents(value),currency);
function inventarisMessage(text){$('inventarisMessage').textContent=text;}
function canLeaveInventaris(){return !inv.saving&&(!inv.dirty||confirm('Porzucić niezapisane zmiany planu?'));}
function closeInventaris(){
  inv.request++;inv.open=false;inv.dirty=false;inv.current=null;
  $('app').classList.remove('inventaris-open');
  $('inventarisPanel').hidden=true;
  $('inventarisEditor').hidden=true;
  $('inventarisTab').removeAttribute('aria-current');
  $('invoiceTab').setAttribute('aria-current','page');
  $('portalTitle').textContent='Weryfikacja faktury';
}
function resetInventaris(){
  inv.menuRequest++;
  closeInventaris();
  $('inventarisTab').hidden=true;
  $('clientSections').hidden=true;
}
async function refreshInventarisMenu(){
  if(!S.clientId)return;
  const client=S.clientId, request=++inv.menuRequest;
  try{
    const result=await api(inventoryUrl('','&summary=true'));
    if(client!==S.clientId||request!==inv.menuRequest)return;
    $('inventarisTab').hidden=!result.available;
    $('clientSections').hidden=false;
    if(!result.available&&inv.open)closeInventaris();
  }catch(err){if(client===S.clientId&&request===inv.menuRequest)console.warn('Nie udało się sprawdzić listy Inventaris.');}
}
function inventarisLossLabel(type){return type==='stolen'?'Skradziony':'Zgubiony';}
function syncInventarisLoss(){
  const active=!!$('inventarisLossType').value;
  $('inventarisLossDateField').hidden=!active;
  $('inventarisLossHint').hidden=!active;
  $('inventarisLossDate').disabled=!active;
  $('inventarisLossDate').required=active;
  $('inventarisLossDate').min=$('inventarisDate').value||'1900-01-01';
}
function renderInventarisList(){
  $('inventarisList').innerHTML=inv.rows.map(row=>{
    const label=row.asset_name||row.description||'Pozycja Inventaris';
    const id=String(row.id);
    return '<button type="button" class="secondary inventaris-item" data-inventaris-line="'+esc(id)+'" aria-current="'+String(String(inv.current?.line.id)===id)+'"><strong>'+esc(label)+'</strong><small>'+esc(row.invoice_date+' · '+(row.invoice_number||'Bez numeru')+' · '+invMoney(row.net_amount,row.currency))+'</small><small>'+ (row.loss_type?esc(inventarisLossLabel(row.loss_type)+' · '+row.loss_date):row.revision?'Plan zapisany':'Do uzupełnienia')+'</small></button>';
  }).join('');
}
async function listInventaris(offset=0,clearEditor=true){
  const req=++inv.request;
  if(clearEditor){inv.current=null;inv.dirty=false;$('inventarisEditor').hidden=true;}
  inventarisMessage('Wczytywanie pozycji…');
  try{
    const data=await api(inventoryUrl('','&offset='+offset));
    if(!inv.open||req!==inv.request)return;
    inv.rows=data.rows;inv.offset=offset;
    renderInventarisList();
    $('inventarisPrev').hidden=offset===0;
    $('inventarisNext').hidden=!data.more;
    inventarisMessage(data.rows.length?'':'Brak pozycji Inventaris na tej stronie.');
  }catch(err){if(inv.open&&req===inv.request)inventarisMessage(err.message);}
}
function renderInventarisEditor(data){
  inv.current=data;inv.dirty=false;
  const line=data.line,plan=data.plan,currency=line.currency;
  $('inventarisEditor').hidden=false;
  $('inventarisTitle').textContent=plan?.asset_name||'Nowy plan amortyzacji';
  $('inventarisSource').textContent=[line.invoice_number||'Faktura bez numeru',line.invoice_date,line.seller_name_snapshot,
    'Netto '+invMoney(line.net_amount,currency),'VAT '+invMoney(line.vat_amount,currency)].filter(Boolean).join(' · ');
  $('inventarisName').value=plan?.asset_name||line.description||'';
  $('inventarisDate').value=plan?.in_use_date||'';
  $('inventarisLife').value=plan?.useful_life_months??60;
  $('inventarisCost').value=decimalInput(plan?.acquisition_cost??line.net_amount);
  $('inventarisResidual').value=decimalInput(plan?.residual_value??'0,00');
  $('inventarisNotes').value=plan?.notes||'';
  $('inventarisLossType').value=plan?.loss_type||'';
  $('inventarisLossDate').value=plan?.loss_date||'';
  syncInventarisLoss();
  $('inventarisFields').disabled=S.user?.role==='read_only';
  $('inventarisSave').hidden=S.user?.role==='read_only';
  $('inventarisSave').disabled=false;
  const target=$('inventarisSchedule');
  target.hidden=!data.schedule;
  target.textContent='';
  if(data.schedule){
    const table=(rows,key,label)=>'<div class="inventaris-table-scroll"><table class="inventaris-table"><thead><tr><th scope="col">'+label+'</th><th scope="col">Odpis</th><th scope="col">Wartość po odpisie</th></tr></thead><tbody>'+
      rows.map(row=>'<tr><th scope="row">'+esc(row[key])+'</th><td>'+esc(invMoney(row.amount,currency))+'</td><td>'+esc(invMoney(row.book_value,currency))+'</td></tr>').join('')+
      '</tbody></table></div>';
    const schedule=data.schedule;
    const loss=plan?.loss_type?'<p class="inventaris-hint"><strong>'+esc(inventarisLossLabel(plan.loss_type))+' · '+esc(plan.loss_date)+
      '</strong><br>Wartość księgowa w dniu utraty (wg planu): '+esc(invMoney(schedule.remaining_value,currency))+
      (schedule.stopped_by_loss?'<br>Odpisy zatrzymane od daty utraty.':'')+'</p>':'';
    const last=schedule.end_date?'Ostatni dzień planu: '+esc(schedule.end_date)+'. Niepełne miesiące liczone proporcjonalnie do dni.':'Brak odpisów przed datą utraty.';
    target.innerHTML='<h4>Plan roczny</h4>'+loss+'<p class="inventaris-hint">'+last+'</p>'+
      (schedule.months.length?table(schedule.years,'year','Rok')+'<details><summary>Odpisy miesięczne</summary>'+table(schedule.months,'date','Data')+'</details>':'');
  }
  renderInventarisList();
}
async function selectInventaris(lineId){
  if(!canLeaveInventaris())return;
  const req=++inv.request;inv.dirty=false;inv.current=null;
  $('inventarisEditor').hidden=true;inventarisMessage('Wczytywanie planu…');
  try{
    const data=await api(inventoryUrl('/'+encodeURIComponent(lineId)));
    if(!inv.open||req!==inv.request)return;
    renderInventarisEditor(data);inventarisMessage('');
  }catch(err){if(inv.open&&req===inv.request)inventarisMessage(err.message);}
}
$('invoiceTab').onclick=()=>{closeLedger();if(inv.open&&canLeaveInventaris())closeInventaris();};
$('inventarisTab').onclick=()=>{
  if(inv.open){if(canLeaveInventaris())closeInventaris();return;}
  if(S.busy)return;
  closeLedger();
  inv.open=true;
  $('app').classList.add('inventaris-open');
  $('inventarisPanel').hidden=false;
  $('invoiceTab').removeAttribute('aria-current');
  $('inventarisTab').setAttribute('aria-current','page');
  $('portalTitle').textContent='Amortyzacja';
  $('inventarisList').textContent='';
  void listInventaris();
};
$('inventarisList').onclick=e=>{
  const line=e.target.closest('[data-inventaris-line]')?.dataset.inventarisLine;
  if(line)void selectInventaris(line);
};
$('inventarisPrev').onclick=()=>{if(canLeaveInventaris())void listInventaris(Math.max(0,inv.offset-50));};
$('inventarisNext').onclick=()=>{if(canLeaveInventaris())void listInventaris(inv.offset+50);};
$('inventarisForm').oninput=()=>{inv.dirty=true;};
$('inventarisLossType').onchange=syncInventarisLoss;
$('inventarisDate').onchange=syncInventarisLoss;
$('inventarisForm').onsubmit=async e=>{
  e.preventDefault();
  if(inv.saving||!inv.current||S.user?.role==='read_only')return;
  const data={asset_name:$('inventarisName').value,in_use_date:$('inventarisDate').value,
    acquisition_cost:$('inventarisCost').value,residual_value:$('inventarisResidual').value,
    notes:$('inventarisNotes').value,loss_type:$('inventarisLossType').value||null,
    loss_date:$('inventarisLossType').value?$('inventarisLossDate').value:null,
    useful_life_months:Number($('inventarisLife').value),revision:inv.current.plan?.revision??0};
  const lineId=inv.current.line.id;
  inv.saving=true;$('inventarisSave').disabled=true;$('inventarisFields').disabled=true;
  inventarisMessage('Zapisywanie planu…');
  try{
    const result=await api(inventoryUrl('/'+encodeURIComponent(lineId)),{method:'PUT',body:JSON.stringify(data)});
    if(!inv.open)return;
    inv.rows=inv.rows.map(row=>String(row.id)===String(lineId)?{...row,asset_name:result.plan.asset_name,revision:result.plan.revision,loss_type:result.plan.loss_type,loss_date:result.plan.loss_date}:row);
    renderInventarisEditor(result);inventarisMessage('Zapisano plan amortyzacji.');
  }catch(err){if(inv.open)inventarisMessage(err.message);}
  finally{inv.saving=false;$('inventarisFields').disabled=S.user?.role==='read_only';$('inventarisSave').disabled=false;}
};


function dismissInventarisEditor(){
  if(!inv.open||(!inv.current&&$('inventarisEditor').hidden)||!canLeaveInventaris())return;
  inv.request++;inv.current=null;inv.dirty=false;
  $('inventarisEditor').hidden=true;inventarisMessage('');
  renderInventarisList();
}
$('inventarisCloseEditor').onclick=dismissInventarisEditor;
document.addEventListener('click',e=>{
  if(!inv.open||!(e.target instanceof Element)||e.target.closest('dialog'))return;
  const details=$('inventarisSchedule').querySelector('details[open]');
  if(details&&!details.contains(e.target))details.open=false;
  if(!e.target.closest('#inventarisEditor,#inventarisList,#inventarisPrev,#inventarisNext,#documentSidebar,#toggleDocuments')){
    dismissInventarisEditor();
  }
});
