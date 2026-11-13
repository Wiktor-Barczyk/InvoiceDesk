const assert=require('node:assert/strict');
const {cents,formatCents,accountSummary}=require('../app/static/booking.js');
const refs={accounts:[],categories:[{id:1,category_name:'Transportkosten'},{id:2,category_name:'Transportkosten'},{id:3,category_name:'Inventaris'}],vat_codes:[{id:1,vat_code:'IN21'},{id:2,vat_code:'IN9'}]};
assert.equal(cents('1.200,13'),120013n);assert.equal(cents('1,200.13'),120013n);assert.equal(cents('0,1'),10n);assert.equal(cents(''),null);assert.equal(cents('NaN'),null);assert.equal(cents('1.234'),null);
assert.equal(formatCents(999999999999999999n),'9999999999999999,99 EUR');
const groups=accountSummary([
 {booking_category_id:1,vat_code_id:1,net_amount:'49,00',vat_amount:'10,29'},
 {booking_category_id:2,vat_code_id:2,net_amount:'0,10',vat_amount:'0,01'},
 {booking_category_id:2,vat_code_id:2,net_amount:'0,20',vat_amount:'0,02'},
 {booking_category_id:3,vat_code_id:1,net_amount:'100.00',vat_amount:'21.00'},
 {vat_code_id:1,net_amount:'10.00',vat_amount:'2.10'}
],refs);
assert.equal(groups.length,3);assert.equal(groups[0].account.account_name,'Inventaris');assert.equal(groups[1].net,4930n);assert.equal(groups[1].vat,1032n);assert.equal(groups[1].gross,5962n);assert.equal(groups[1].details.length,2);assert.equal(groups[1].details[1].net,30n);assert.equal(groups[2].account,null);
const invalid=accountSummary([{booking_category_id:1,net_amount:'',vat_amount:'0'}],refs);assert.equal(invalid[0].complete,false);
const historic=accountSummary([{account_id:900,account_code:'9990',account_name:'Historyczne',net_amount:'1.00',vat_amount:'0.00'}],refs);assert.equal(historic[0].account.account_name,'Historyczne');
console.log('PASS: unified catalog without ledger accounts, duplicate aliases, VAT breakdown, exact money and legacy accounts.');
