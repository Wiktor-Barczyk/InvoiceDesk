'use strict';
// Exact money arithmetic shared by header checks and the live account breakdown.
function cents(value){let text=String(value??'').replace(/[\s\u00a0]/g,'');if(text.includes(',')&&text.includes('.'))text=text.lastIndexOf(',')>text.lastIndexOf('.')?text.replace(/\./g,'').replace(',','.'):text.replace(/,/g,'');else text=text.replace(',','.');if(!/^\d+(\.\d{1,2})?$/.test(text))return null;const [whole,fraction='']=text.split('.');return BigInt(whole)*100n+BigInt(fraction.padEnd(2,'0'));}
function formatCents(value,currency='EUR'){return value===null?'—':`${value/100n},${String(value%100n).padStart(2,'0')} ${currency}`;}
function accountSummary(lines,refs){
  const accounts=new Map(refs.accounts.map(x=>[String(x.id),x]));
  const categories=new Map(refs.categories.map(x=>[String(x.id),x]));
  const vatCodes=new Map(refs.vat_codes.map(x=>[String(x.id),x]));
  const groups=new Map();
  for(const line of lines){
    const category=categories.get(String(line.booking_category_id))||(line.booking_category_id?{id:line.booking_category_id,category_name:line.booking_category_name||'Kategoria #'+line.booking_category_id}:null);
    const legacy=accounts.get(String(line.account_id))||(line.account_id?{id:line.account_id,account_code:line.account_code||'#'+line.account_id,account_name:line.account_name||'Konto archiwalne'}:null);
    const account=category?{id:category.id,account_code:'',account_name:category.category_name}:legacy;
    const key=category?'category:'+category.category_name.trim().toLocaleLowerCase('pl'):legacy?'account:'+legacy.id:'unassigned';
    if(!groups.has(key))groups.set(key,{account,net:0n,vat:0n,count:0,complete:true,details:new Map()});
    const group=groups.get(key),vat=vatCodes.get(String(line.vat_code_id))||(line.vat_code_id?{id:line.vat_code_id,vat_name:line.vat_display_name,vat_code:line.vat_code_name||'VAT #'+line.vat_code_id}:null);
    const detailKey=String(vat?.id||'');
    if(!group.details.has(detailKey))group.details.set(detailKey,{category:category||null,vatCode:vat||null,net:0n,vat:0n,count:0,complete:true});
    const detail=group.details.get(detailKey),net=cents(line.net_amount),tax=cents(line.vat_amount);
    group.count++;detail.count++;
    if(net===null||tax===null){group.complete=false;detail.complete=false;}
    else{group.net+=net;group.vat+=tax;detail.net+=net;detail.vat+=tax;}
  }
  return [...groups.values()].sort((a,b)=>a.account&&b.account?a.account.account_name.localeCompare(b.account.account_name,'pl'):a.account?-1:b.account?1:0)
    .map(g=>({...g,gross:g.net+g.vat,details:[...g.details.values()].map(d=>({...d,gross:d.net+d.vat}))}));
}
function availableBookings(){return (S.refs.booking_choices||[]).filter(x=>!x.invoice_type||x.invoice_type===S.form.invoice_type);}
function bookingChoice(line){return availableBookings().find(x=>line.booking_category_id?(x.category_ids||[x.booking_category_id]).some(id=>String(id)===String(line.booking_category_id)):!x.booking_category_id&&String(x.account_id)===String(line.account_id));}
function bookingKey(line){return bookingChoice(line)?.key||((line.booking_category_id||line.account_id)?'saved':'');}
function bookingOptions(line,query=''){
  const selected=bookingKey(line),term=query.trim().toLocaleLowerCase('nl');
  const choices=availableBookings().filter(x=>x.key===selected||x.label.toLocaleLowerCase('nl').includes(term));
  let html='<option value="">'+(choices.length?(S.refs.booking_catalog?'Wybierz konto…':'Wybierz kategorię / konto…'):'Brak pasujących kont')+'</option>';
  if(selected==='saved')html+=`<option value="saved" selected>${esc(line.booking_category_name||line.account_name||'Pozycja archiwalna')}</option>`;
  return html+choices.map(x=>`<option value="${esc(x.key)}" ${x.key===selected?'selected':''}>${esc(x.label)}</option>`).join('');
}
function unassignedBooking(){return S.form.lines.some(line=>!bookingChoice(line));}
function renderAccountSummary(){
  const currency=S.form.currency||'EUR',groups=accountSummary(S.form.lines,S.refs);
  const money=(row,key)=>esc(formatCents(row.complete?row[key]:null,currency));
  const cells=row=>['net','vat','gross'].map((key,i)=>`<td data-label="${['Netto','VAT','Brutto'][i]}">${money(row,key)}</td>`).join('');
  const detailLabel=d=>[d.category?.category_name,d.vatCode?.vat_name || d.vatCode?.vat_code].filter(Boolean).join(' · ');
  $('accountSummaryBody').innerHTML=groups.map(group=>{
    const title=group.account?[group.account.account_code,group.account.account_name].filter(Boolean).join(' · '):'Bez kategorii / konta';
    const subtitle=group.details.length===1?detailLabel(group.details[0]):group.count+' poz.';
    return `<tr class="account-total ${group.account?'':'unassigned'}"><th scope="row">${esc(title)}${subtitle?`<small>${esc(subtitle)}</small>`:''}</th>${cells(group)}</tr>`+
      (group.details.length>1?group.details.map(d=>`<tr class="account-detail"><th scope="row">${esc(detailLabel(d)||'Bez kategorii')}</th>${cells(d)}</tr>`).join(''):'');
  }).join('');
  $('accountSummaryEmpty').hidden=groups.length>0;
  $('bookingWarning').hidden=!unassignedBooking();
}
function chooseBooking(index,key){
  const line=S.form.lines[index],choice=availableBookings().find(x=>x.key===key);
  line.booking_category_id=choice?.booking_category_id||'';line.account_id=choice?.account_id||'';
  if(choice?.default_vat_code_id&&validVatCodes().some(x=>String(x.id)===String(choice.default_vat_code_id)))line.vat_code_id=choice.default_vat_code_id;
  dirty();renderLines();renderTotals();
}
if(typeof module!=='undefined')module.exports={cents,formatCents,accountSummary};
