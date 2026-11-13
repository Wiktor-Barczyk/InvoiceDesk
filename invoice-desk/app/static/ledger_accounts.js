'use strict';
const AC={view:'entries',sequence:0,key:'',data:null,collapsed:new Set()};
const accountTypes={asset:'Aktywa · majątek',liability:'Zobowiązania',equity:'Kapitał',income:'Przychody',expense:'Koszty'};
const accountLabel=a=>a.account_code+' '+(a.account_name.startsWith(a.account_code+' ')?a.account_name.slice(a.account_code.length+1):a.account_name);
function resetLedgerAccounts(){
  AC.sequence++;AC.view='entries';AC.key='';AC.data=null;AC.collapsed.clear();L.accountId=null;
  $('ledgerAccountSearch').value='';$('ledgerHideEmpty').checked=false;
  $('ledgerAccountRows').replaceChildren();$('ledgerAccountSummary').textContent='';
  $('ledgerAccountTableWrap').hidden=true;$('ledgerAccountEmpty').hidden=true;
  syncLedgerAccountView();
}
function syncLedgerAccountView(){
  const accounts=AC.view==='accounts';
  $('ledgerViewEntries').setAttribute('aria-selected',String(!accounts));
  $('ledgerViewAccounts').setAttribute('aria-selected',String(accounts));
  $('ledgerAccounts').hidden=!accounts;
  if(accounts)$('ledgerResults').hidden=true;
  $('ledgerAccountScope').hidden=accounts||!L.accountId;
  $('ledgerHelp').hidden=accounts;
  $('ledgerFilter').querySelector('[type="submit"]').textContent=accounts?'Zastosuj okres':'Pokaż zapisy';
}
async function ledgerAccountData(client,from,to,sequence){
  const key=[client,from,to].join(':');
  if(AC.key===key&&AC.data)return AC.data;
  const query=new URLSearchParams({client_id:client,date_from:from,date_to:to});
  const data=await api('/api/ledger/accounts?'+query);
  if(sequence!==AC.sequence||client!==S.clientId||!L.open)return null;
  AC.key=key;AC.data=data;return data;
}
function accountHasValues(a){
  return [a.opening_balance,a.closing_balance,a.total_period_debit,a.total_period_credit]
    .some(v=>String(v||'0').replace(/[-.0]/g,'')!=='');
}
function accountTotal(rows,key){
  let total=0n;
  for(const row of rows){const str=String(row[key]),negative=str.startsWith('-');const value=cents(negative?str.slice(1):str);if(value===null)return '—';total+=negative?-value:value;}
  return (total<0n?'−':'')+formatCents(total<0n?-total:total,'EUR');
}
function renderLedgerAccounts(){
  if(!AC.data)return;
  const rows=AC.data.accounts,byId=new Map(rows.map(r=>[r.id,r]));
  const children=new Map(rows.map(r=>[r.id,[]]));const groups={};
  for(const row of rows){
    if(byId.has(row.parent_account_id))children.get(row.parent_account_id).push(row);
    else (groups[row.account_type]??=[]).push(row);
  }
  const search=$('ledgerAccountSearch').value.trim().toLocaleLowerCase('pl-PL');
  const hide=$('ledgerHideEmpty').checked;
  const visible=new Set();
  for(const row of rows){
    if(hide&&!accountHasValues(row))continue;
    if(search&&!accountLabel(row).toLocaleLowerCase('pl-PL').includes(search))continue;
    let cursor=row;
    while(cursor&&!visible.has(cursor.id)){visible.add(cursor.id);cursor=byId.get(cursor.parent_account_id);}
    // A matched parent also shows its matching-value descendants.
    if(search){const stack=[...(children.get(row.id)||[])];while(stack.length){const child=stack.pop();if(!hide||accountHasValues(child))visible.add(child.id);stack.push(...children.get(child.id));}}
  }
  let output='',shown=0;
  for(const [type,label] of Object.entries(accountTypes)){
    const roots=groups[type]||[],kept=roots.filter(r=>visible.has(r.id));if(!kept.length)continue;
    const key='type:'+type,open=!!search||!AC.collapsed.has(key);
    // Group totals include each root once. Parent and child values are not added twice.
    output+='<tr class="ledger-account-group"><th scope="row"><button type="button" data-account-toggle="'+key+'" aria-expanded="'+open+'">'+(open?'▾':'▸')+' '+esc(label)+'</button></th><td data-label="Kwota w okresie">'+esc(accountTotal(kept,'period_change'))+'</td><td data-label="Saldo na koniec">'+esc(accountTotal(kept,'closing_balance'))+'</td><td></td></tr>';
    if(!open)continue;
    const stack=kept.slice().reverse();
    while(stack.length){
      const row=stack.pop();if(!visible.has(row.id))continue;shown++;
      const key='account:'+row.id,open=!!search||!AC.collapsed.has(key);
      const toggle=row.has_children?'<button class="ledger-account-chevron" type="button" data-account-toggle="'+key+'" aria-expanded="'+open+'" aria-label="Rozwiń lub zwiń '+esc(accountLabel(row))+'">'+(open?'▾':'▸')+'</button>':'<span class="ledger-account-chevron" aria-hidden="true"></span>';
      output+='<tr><th scope="row"><div class="ledger-account-name" style="--account-depth:'+Math.min(row.depth,8)+'">'+toggle+
        '<button type="button" class="ledger-account-link" data-account-open="'+row.id+'"><b>'+esc(row.account_code)+'</b> '+esc(row.account_name.startsWith(row.account_code+' ')?row.account_name.slice(row.account_code.length+1):row.account_name)+
        (!row.is_active?'<small>Nieaktywne</small>':'')+(row.has_children?'<small>Z podkontami</small>':'')+'</button></div></th><td data-label="Kwota w okresie">'+esc(ledgerMoney(row.period_change))+
        '</td><td class="ledger-account-balance" data-label="Saldo na koniec">'+esc(ledgerMoney(row.closing_balance))+'</td><td><button type="button" class="secondary" data-account-open="'+row.id+'">Zapisy</button></td></tr>';
      if(open)stack.push(...children.get(row.id).filter(c=>visible.has(c.id)).slice().reverse());
    }
  }
  $('ledgerAccountRows').innerHTML=output;
  $('ledgerAccountEmpty').hidden=!!output;
  $('ledgerBalanceDate').textContent='Saldo na '+AC.data.date_to;
  $('ledgerAccountSummary').textContent='Kont: '+rows.length+' · z zapisami: '+AC.data.counts.with_activity+' · widocznych: '+shown;
  if(AC.data.hierarchy_warning)$('ledgerAccountSummary').textContent+=' · Sprawdź powiązania kont — wykryto cykl hierarchii.';
}
const originalLedgerLoad=loadLedger;
loadLedger=async function(offset){
  const from=$('ledgerFrom').value,to=$('ledgerTo').value;
  if(!from||!to||from>to){AC.sequence++;L.request++;$('ledgerMessage').textContent='Sprawdź zakres dat.';$('ledgerResults').hidden=true;$('ledgerAccounts').hidden=true;return;}
  const sequence=++AC.sequence,client=S.clientId,selected=L.accountId;
  syncLedgerAccountView();
  if(AC.view==='entries'&&!selected)return originalLedgerLoad(offset);
  L.request++;L.detailRequest++;
  $('ledgerMessage').textContent=AC.view==='accounts'?'Wczytywanie kont i sald…':'Wczytywanie zapisów konta…';
  $('ledgerResults').hidden=true;$('ledgerAccounts').setAttribute('aria-busy','true');
  if(AC.view==='accounts'){$('ledgerAccountTableWrap').hidden=true;$('ledgerAccountSummary').textContent='';$('ledgerAccountEmpty').hidden=true;}
  try{
    const data=await ledgerAccountData(client,from,to,sequence);
    if(!data||sequence!==AC.sequence||client!==S.clientId||!L.open)return;
    L.from=from;L.to=to;
    if(AC.view==='accounts'){
      renderLedgerAccounts();$('ledgerMessage').textContent='';$('ledgerAccounts').hidden=false;$('ledgerAccountTableWrap').hidden=false;
    }else{
      const account=data.accounts.find(a=>a.id===selected);
      if(!account)throw new Error('Konto nie jest dostępne w tym okresie. Wróć do listy kont.');
      $('ledgerAccountTitle').textContent=accountLabel(account);
      await originalLedgerLoad(offset);
      if(sequence!==AC.sequence||client!==S.clientId||!L.open||$('ledgerResults').hidden)return;
      $('ledgerTotals').innerHTML='<span>Saldo przed okresem <strong>'+esc(ledgerMoney(account.opening_balance))+'</strong></span><span>Kwota w okresie <strong>'+esc(ledgerMoney(account.period_change))+'</strong></span><span>Saldo na '+esc(to)+' <strong>'+esc(ledgerMoney(account.closing_balance))+'</strong></span><small>Kwoty z konta i podkont; debet/kredyt w tabeli dotyczy wyłącznie wybranego zakresu kont.</small>';
    }
  }catch(error){if(sequence===AC.sequence&&client===S.clientId&&L.open)$('ledgerMessage').textContent=error.message;}
  finally{if(sequence===AC.sequence)$('ledgerAccounts').setAttribute('aria-busy','false');}
};
function ledgerAccountView(view,accountId=null){
  AC.view=view;L.accountId=accountId;AC.sequence++;L.request++;L.detailRequest++;
  syncLedgerAccountView();void loadLedger(0);
}
$('ledgerViewEntries').onclick=()=>ledgerAccountView('entries');
$('ledgerViewAccounts').onclick=()=>ledgerAccountView('accounts');
$('ledgerBackAccounts').onclick=()=>ledgerAccountView('accounts');
$('ledgerAllEntries').onclick=()=>ledgerAccountView('entries');
$('ledgerAccountSearch').oninput=renderLedgerAccounts;
$('ledgerHideEmpty').onchange=renderLedgerAccounts;
$('ledgerExpandAccounts').onclick=()=>{AC.collapsed.clear();renderLedgerAccounts();};
$('ledgerCollapseAccounts').onclick=()=>{Object.keys(accountTypes).forEach(t=>AC.collapsed.add('type:'+t));renderLedgerAccounts();};
$('ledgerAccountRows').onclick=e=>{
  const toggle=e.target.closest('[data-account-toggle]');
  if(toggle){const key=toggle.dataset.accountToggle;if(AC.collapsed.has(key))AC.collapsed.delete(key);else AC.collapsed.add(key);renderLedgerAccounts();return;}
  const account=e.target.closest('[data-account-open]');
  if(account)ledgerAccountView('entries',Number(account.dataset.accountOpen));
};
// Arrow keys switch the two view tabs; standard buttons remain keyboard accessible.
for(const id of ['ledgerViewEntries','ledgerViewAccounts'])$(id).addEventListener('keydown',e=>{
  if(!['ArrowLeft','ArrowRight'].includes(e.key))return;
  e.preventDefault();const next=id==='ledgerViewEntries'?'ledgerViewAccounts':'ledgerViewEntries';$(next).focus();$(next).click();
});
