'use strict';
const L={open:false,request:0,detailRequest:0,offset:0,pageSize:30,from:'',to:'',accountId:null};
const ledgerMoney=value=>{
  const text=String(value??''), negative=text.startsWith('-');
  const amount=cents(negative?text.slice(1):text);
  return amount===null?'—':(negative&&amount!==0n?'−':'')+formatCents(amount,'EUR');
};
const ledgerKind={purchase:'Zakup',sale:'Sprzedaż',bank:'Bank',general:'Memoriał',opening:'Bilans otwarcia',closing:'Zamknięcie',depreciation:'Amortyzacja',vat:'VAT',payroll:'Płace',correction:'Korekta'};
function closeLedger(){
  if(!L.open)return;
  L.open=false;L.request++;L.detailRequest++;
  $('ledgerPanel').hidden=true;
  $('app').classList.remove('ledger-open');
  $('ledgerTab').removeAttribute('aria-current');
  $('invoiceTab').setAttribute('aria-current','page');
  $('portalTitle').textContent='Weryfikacja faktury';
}
function openLedger(fromInvoice=false){
  if(!S.clientId||S.busy||(inv.open&&!canLeaveInventaris()))return;
  if(L.open&&!fromInvoice){closeLedger();return;}
  if(inv.open)closeInventaris();
  L.open=true;
  if(typeof resetLedgerAccounts==='function')resetLedgerAccounts();
  $('app').classList.add('ledger-open');
  $('ledgerPanel').hidden=false;
  $('ledgerPanel').scrollTop=0;
  $('ledgerTab').setAttribute('aria-current','page');
  $('invoiceTab').removeAttribute('aria-current');
  $('portalTitle').textContent='Księga klienta';
  $('ledgerClient').textContent=S.refs?.client?.company_name||'';
  const now=new Date(),year=now.getFullYear();
  const postedDate=fromInvoice?S.state?.invoice?.ledger_date:null;
  $('ledgerFrom').value=postedDate||L.from||year+'-01-01';
  $('ledgerTo').value=postedDate||L.to||year+'-12-31';
  void loadLedger(0);
}
async function loadLedger(offset){
  const from=$('ledgerFrom').value,to=$('ledgerTo').value;
  if(!from||!to||from>to){$('ledgerMessage').textContent='Sprawdź zakres dat.';return;}
  const request=++L.request;L.detailRequest++;
  const client=S.clientId;
  const query=new URLSearchParams({client_id:client,date_from:from,date_to:to,offset});
  if(L.accountId!==null)query.set('account_id',L.accountId);
  $('ledgerMessage').textContent='Wczytywanie księgi…';
  $('ledgerResults').hidden=true;
  try{
    const data=await api('/api/ledger?'+query);
    if(!L.open||request!==L.request||client!==S.clientId)return;
    L.offset=offset;L.pageSize=data.page_size;L.from=from;L.to=to;
    const {totals,entries,balances}=data;
    $('ledgerTotals').innerHTML='<span>Aktualne zapisy <strong>'+esc(totals.entries)+'</strong></span>'+
      '<span title="Należności od klientów">Debiteuren <strong>'+esc(ledgerMoney(balances?.debiteuren))+'</strong></span>'+
      '<span title="Zobowiązania wobec dostawców">Crediteuren <strong>'+esc(ledgerMoney(balances?.crediteuren))+'</strong></span>'+
      '<small>Salda na '+esc(to)+' · wg zaksięgowanych operacji</small>';
    $('ledgerRows').innerHTML=entries.map(row=>{
      const party=row.invoice_type==='sale'?row.buyer_name_snapshot:row.seller_name_snapshot;
      const ref=row.invoice_number||row.reference||row.description;
      const kind=row.reverses_entry_id?'Storno #'+row.reverses_entry_id:
        row.is_reversed||row.status==='reversed'?'Wystornowany':ledgerKind[row.entry_type]||'Zapis';
      const document=row.document_id?'<button type="button" class="secondary" data-ledger-doc="'+esc(row.document_id)+'">'+
        (row.current_document?'Otwórz fakturę':'Otwórz aktualną fakturę')+'</button>':'';
      const history=row.has_history?'<button type="button" class="secondary" aria-expanded="false" data-ledger-history="'+esc(row.id)+'">Historia zmian</button>':'';
      return '<tr><td>'+esc(row.entry_date)+'</td><td><strong>'+esc(ref)+'</strong><small>'+
        esc([party,kind,'#'+row.id].filter(Boolean).join(' · '))+'</small></td><td class="ledger-number">'+
        esc(ledgerMoney(row.debit))+'</td><td class="ledger-number">'+esc(ledgerMoney(row.credit))+
        '</td><td><div class="ledger-actions"><button type="button" class="secondary" aria-expanded="false" data-ledger-entry="'+esc(row.id)+'">Szczegóły zapisu</button>'+document+history+'</div></td></tr>';
    }).join('');
    $('ledgerPrev').disabled=offset===0;
    $('ledgerNext').disabled=offset+entries.length>=totals.entries||!entries.length;
    $('ledgerPage').textContent=entries.length?(offset+1)+'–'+(offset+entries.length)+' z '+totals.entries:'';
    $('ledgerMessage').textContent=entries.length?'':'Brak zapisów w wybranym okresie.';
    $('ledgerResults').hidden=false;
  }catch(err){if(L.open&&request===L.request)$('ledgerMessage').textContent=err.message;}
}
function renderLedgerLines(entries){
  return '<div class="ledger-scroll"><table class="ledger-table ledger-detail" aria-label="Szczegóły zapisu księgowego"><thead><tr><th scope="col">Konto</th><th scope="col">Opis</th><th scope="col">Debet (Wn)</th><th scope="col">Kredyt (Ma)</th></tr></thead><tbody>'+
    entries.map(r=>'<tr><td>'+esc(r.account_code+' '+r.account_name)+'</td><td>'+esc(r.description)+'</td><td class="ledger-number">'+esc(ledgerMoney(r.debit_amount))+'</td><td class="ledger-number">'+esc(ledgerMoney(r.credit_amount))+'</td></tr>').join('')+
    '</tbody></table></div>';
}
function renderLedgerHistory(steps){
  if(!steps.length)return 'Brak historii zmian.';
  return '<section class="ledger-history" aria-label="Historia zmian">'+steps.map(step=>{
    const when=new Date(step.created_at).toLocaleString('pl-PL',{dateStyle:'short',timeStyle:'short'});
    return '<article><strong>Poprawka · '+esc(when)+'</strong><p>Powód: '+esc(step.reason)+'</p>'+
      '<details><summary>Poprzedni zapis #'+esc(step.previous_entry_id)+' · '+esc(step.previous_date)+
      (step.invoice_number?' · '+esc(step.invoice_number):'')+'</summary>'+renderLedgerLines(step.previous_lines)+'</details>'+
      '<details><summary>Storno #'+esc(step.reversal_entry_id)+' · '+esc(step.reversal_date)+
      '</summary>'+renderLedgerLines(step.reversal_lines)+'</details></article>';
  }).join('')+'</section>';
}
async function showLedgerDetails(button){
  const current=$('ledgerRows').querySelector('[aria-expanded="true"]');
  const detail=++L.detailRequest;
  if(current){current.setAttribute('aria-expanded','false');$('ledgerDetails')?.remove();if(current===button)return;}
  button.setAttribute('aria-expanded','true');
  const row=document.createElement('tr'), cell=document.createElement('td');
  const history=button.dataset.ledgerHistory;
  row.id='ledgerDetails';cell.colSpan=5;cell.textContent=history?'Wczytywanie historii…':'Wczytywanie zapisu…';row.append(cell);
  button.closest('tr').after(row);
  try{
    const id=history||button.dataset.ledgerEntry;
    const entries=await api('/api/ledger/'+encodeURIComponent(id)+(history?'/history':'')+'?client_id='+encodeURIComponent(S.clientId));
    if(!L.open||detail!==L.detailRequest)return;
    cell.innerHTML=history?renderLedgerHistory(entries):renderLedgerLines(entries);
  }catch(err){if(detail===L.detailRequest)cell.textContent=err.message;}
}
$('ledgerTab').onclick=()=>openLedger();
$('postingOpenLedger').onclick=()=>openLedger(true);
$('ledgerFilter').onsubmit=e=>{e.preventDefault();void loadLedger(0);};
$('ledgerPrev').onclick=()=>void loadLedger(Math.max(0,L.offset-L.pageSize));
$('ledgerNext').onclick=()=>void loadLedger(L.offset+L.pageSize);
$('ledgerRows').onclick=async e=>{
  const detail=e.target.closest('[data-ledger-entry],[data-ledger-history]');
  if(detail){void showLedgerDetails(detail);return;}
  const doc=e.target.closest('[data-ledger-doc]');
  if(doc&&!S.busy&&guard()){
    S.dirty=false;
    closeLedger();
    S.filter='approved';$('pendingTab').classList.remove('selected');$('approvedTab').classList.add('selected');
    $('search').value='';
    void reloadList().catch(err=>toast(err.message,true));
    await openDocument(Number(doc.dataset.ledgerDoc));
  }
};
