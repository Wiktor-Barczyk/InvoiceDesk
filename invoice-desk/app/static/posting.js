'use strict';
const postingVatLabel = x => ({PORTAL_0:'VAT 0%',PORTAL_9:'VAT 9%',PORTAL_21:'VAT 21%',PORTAL_EXEMPT:'Zwolniony z VAT'}[x.vat_code] || x.vat_name || x.vat_code);
const mayCorrect = () => ['admin','chief_accountant'].includes(S.user?.role);
const currentInvoiceStatus = () => S.state?.invoice?.status;
const editingPosted = () => S.postingCorrection===S.docId && currentInvoiceStatus()==='posted' && mayCorrect();
function canBookCurrent(){
  if(!S.form||S.user?.role==='read_only')return false;
  if(currentInvoiceStatus()==='approved')return S.state?.document?.status==='to_book';
  if(currentInvoiceStatus()==='posted')return editingPosted();
  return !readonly();
}
function syncPostingButton(){
  const enabled=canBookCurrent();
  $('approveBtn').disabled=!enabled||!S.form.reviewed||
    (currentInvoiceStatus()!=='approved'&&unassignedBooking());
}
function renderPostingVat(){
  const visible=canBookCurrent()&&S.form.invoice_type==='purchase'&&S.form.lines.some(line=>cents(line.vat_amount)>0n);
  $('postingVat').hidden=!visible;
  $('postingVatRows').innerHTML=visible?S.form.lines.map((line,i)=>{
    if(!(cents(line.vat_amount)>0n))return '';
    return '<div class="field"><label for="deductible-'+i+'">Poz. '+(i+1)+' · VAT z faktury '+esc(invMoney(line.vat_amount,S.form.currency))+'</label>'+
      '<input id="deductible-'+i+'" data-deduction="'+i+'" inputmode="decimal" placeholder="np. 0,00" value="'+esc(decimalInput(line.deductible_vat))+'" required maxlength="28"></div>';
  }).join(''):'';
}
function renderPostingControls(){
  const status=currentInvoiceStatus(), editing=editingPosted(), enabled=canBookCurrent();
  $('postingCorrect').hidden=status!=='posted'||!S.state?.invoice?.portal_posted||editing||!mayCorrect();
  $('postingCancel').hidden=!editing;
  $('correctionFields').hidden=!editing;
  $('correctionReason').required=editing;
  $('correctionReason').value=editing?(S.correctionReason||''):'';
  $('approveBtn').hidden=!enabled;
  $('reviewed').closest('label').hidden=!enabled;
  $('reviewed').disabled=!enabled;
  $('approveText').textContent=editing?'Zaksięguj poprawkę':'Zaksięguj fakturę';
  $('saveCaption').textContent=status==='posted'&&!editing?'Zaksięgowano':editing?'Poprawka faktury':status==='approved'?'Zatwierdzona':'Księgowanie';
  $('saveSubcaption').textContent=status==='posted'&&!editing?'Zapis w księdze #'+S.state.invoice.general_ledger_entry_id:
    editing?'Storno i poprawiony zapis':status==='approved'?'Gotowa do księgowania':'Zapis faktury i księgowanie w jednym kroku';
  $('postingOpenLedger').hidden=editing||!S.state?.invoice?.ledger_date;
  renderPostingVat();syncPostingButton();
}
$('postingVat').oninput=e=>{
  if(e.target.dataset.deduction===undefined)return;
  S.form.lines[Number(e.target.dataset.deduction)].deductible_vat=e.target.value;
  dirty();
};
for(const [id,all] of [['postingFullVat',true],['postingZeroVat',false]]){
  $(id).onclick=()=>{
    for(const line of S.form.lines)line.deductible_vat=all?line.vat_amount:'0';
    dirty();renderPostingVat();
  };
}
$('correctionReason').oninput=e=>{S.correctionReason=e.target.value;dirty();};
$('postingCorrect').onclick=()=>{
  if(S.busy||!mayCorrect()||currentInvoiceStatus()!=='posted')return;
  S.postingCorrection=S.docId;S.correctionReason='';S.form.reviewed=false;
  renderForm();
  $('editorScroll').scrollTop=0;
};
$('postingCancel').onclick=()=>{if(!S.busy&&guard())void openDocument(S.docId);};
async function bookCurrent(){
  if(S.busy||!canBookCurrent()||!S.form.reviewed)return;
  if(!$('invoiceForm').reportValidity()){toast('Uzupełnij wymagane pola.',true);return;}
  const deductions=S.form.lines.map(l=>cents(l.vat_amount)>0n?l.deductible_vat:'0');
  const doc=S.docId;
  busy(true,'Księgowanie faktury…');$('formError').hidden=true;
  try{
    const result=await api('/api/documents/'+doc+'/book',{method:'POST',
      body:JSON.stringify({...S.form,deductions,correction_reason:S.correctionReason||''})});
    S.dirty=false;
    await reloadList();await openDocument(doc);void refreshInventarisMenu();
    toast((result.corrected?'Zaksięgowano poprawkę. ':'Zaksięgowano fakturę. ')+'Zapis #'+result.entry_id+
      (result.plans_to_review?' · Sprawdź plan amortyzacji zmienionych pozycji.':''));
  }catch(err){$('formError').textContent=err.message;$('formError').hidden=false;$('editorScroll').scrollTop=0;toast(err.message,true);}
  finally{busy(false);}
}
