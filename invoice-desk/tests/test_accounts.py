from datetime import date
from decimal import Decimal
from pathlib import Path
import uuid

import pytest

from app.db import insert
from app.posting import journal
from test_flow import rig, upload, payload


@pytest.fixture
def chart(rig):
    tag=uuid.uuid4().hex[:7]
    with rig['db'].connect() as c:
        def account(code,name,kind,parent=None,active=True):
            return insert(c,'accounting.accounts',dict(account_code=tag+code,account_name=name,
                account_type=kind,parent_account_id=parent,is_active=active))
        parent=account('40','Koszty razem','expense')
        c.execute('UPDATE accounting.accounts SET parent_account_id=%s WHERE id=%s',(parent['id'],rig['account']['id']))
        second=account('42','Drugi koszt','expense',parent['id'])
        cash=account('11','Bank','asset')
        capital=account('03','Kapitał','equity')
        empty=account('00','Puste konto','asset')
        inactive=account('99','Nieaktywne bez zapisów','expense',active=False)
        control={r['account_code']:r for r in c.execute("SELECT * FROM accounting.accounts WHERE account_code IN ('130','140','150','151')")}
        vat=c.execute("""INSERT INTO accounting.vat_codes(vat_code,vat_name,vat_rate_percent,vat_direction)
            VALUES ('PORTAL_21','VAT 21%',21,'both') ON CONFLICT(vat_code) DO UPDATE SET vat_name=EXCLUDED.vat_name RETURNING *""").fetchone()
    return {**rig,'parent':parent,'second':second,'cash':cash,'capital':capital,'empty':empty,'inactive':inactive,'control':control,'portal_vat':vat}


def post(rig,day,rows,client_id=None,draft=False):
    with rig['db'].connect() as c:
        prepared=[dict(account_id=account['id'],debit_amount=Decimal(str(debit)),credit_amount=Decimal(str(credit)),
            description='Zapis testowy',counterparty_id=None,invoice_line_id=None) for account,debit,credit in rows]
        if draft:
            e=insert(c,'accounting.general_ledger_entries',dict(client_id=client_id or rig['client']['id'],
                entry_date=date.fromisoformat(day),entry_type='general',description='Szkic'))
            for number,row in enumerate(prepared,1):insert(c,'accounting.general_ledger_entry_lines',dict(row,general_ledger_entry_id=e['id'],line_number=number))
            return e['id']
        return journal(c,rig['staff']['id'],client_id or rig['client']['id'],None,date.fromisoformat(day),'general','Zapis testowy',None,prepared)


def fetch(rig,**extra):
    params=dict(client_id=rig['client']['id'],date_from='2026-01-01',date_to='2026-12-31',**extra)
    response=rig['http'].get('/api/ledger/accounts',params=params)
    assert response.status_code==200,response.text
    return {r['id']:r for r in response.json()['accounts']},response.json()


def test_zero_accounts_and_actual_hierarchy(chart):
    rows,data=fetch(chart)
    assert rows[chart['empty']['id']]['closing_balance']=='0'
    assert rows[chart['account']['id']]['parent_account_id']==chart['parent']['id']
    assert rows[chart['parent']['id']]['has_children']
    assert chart['inactive']['id'] not in rows
    assert data['currency']=='EUR' and data['counts']['with_activity']==0


def test_openings_movements_cutoff_drafts_and_client_isolation(chart):
    r=chart
    post(r,'2025-12-31',[(r['cash'],100,0),(r['capital'],0,100)])
    first=post(r,'2026-07-07',[(r['account'],'41.31',0),(r['control']['150'],'8.68',0),(r['control']['140'],0,'49.99')])
    post(r,'2026-07-20',[(r['control']['140'],20,0),(r['cash'],0,20)])
    post(r,'2027-01-01',[(r['account'],9,0),(r['cash'],0,9)])
    post(r,'2026-08-01',[(r['account'],999,0),(r['cash'],0,999)],draft=True)
    with r['db'].connect() as c:
        other=insert(c,'core.clients',dict(odoo_partner_id=int(uuid.uuid4().hex[:9],16),company_name='Inny klient',legal_form='eenmanszaak'))
    post(r,'2026-07-07',[(r['account'],1000,0),(r['control']['140'],0,1000)],client_id=other['id'])
    rows,data=fetch(r)
    assert Decimal(rows[r['cash']['id']]['opening_balance'])==100
    assert Decimal(rows[r['cash']['id']]['period_change'])==-20
    assert Decimal(rows[r['cash']['id']]['closing_balance'])==80
    assert Decimal(rows[r['capital']['id']]['closing_balance'])==100
    assert Decimal(rows[r['parent']['id']]['closing_balance'])==Decimal('41.31')
    assert Decimal(rows[r['control']['140']['id']]['closing_balance'])==Decimal('29.99')
    response=r['http'].get('/api/ledger',params=dict(client_id=r['client']['id'],date_from='2026-01-01',date_to='2026-12-31',account_id=r['parent']['id']))
    assert response.status_code==200,response.text
    body=response.json();assert len(body['entries'])==1 and body['entries'][0]['id']==first
    assert Decimal(body['entries'][0]['debit'])==Decimal('41.31') and Decimal(body['entries'][0]['credit'])==0
    assert body['totals']['entries']==1 and Decimal(body['totals']['debit'])==Decimal('41.31')
    # Existing unfiltered list keeps both posted entries and full balanced amounts.
    normal=r['http'].get('/api/ledger',params=dict(client_id=r['client']['id'],date_from='2026-01-01',date_to='2026-12-31')).json()
    assert normal['totals']['entries']==2 and normal['totals']['debit']==normal['totals']['credit']


def test_multiple_child_lines_count_one_entry_and_parent_rollup(chart):
    r=chart
    post(r,'2026-01-01',[(r['account'],10,0),(r['second'],15,0),(r['control']['140'],0,25)])
    rows,_=fetch(r)
    assert Decimal(rows[r['parent']['id']]['closing_balance'])==25
    assert Decimal(rows[r['parent']['id']]['direct_closing_balance'])==0
    q=dict(client_id=r['client']['id'],date_from='2026-01-01',date_to='2026-12-31',account_id=r['parent']['id'])
    body=r['http'].get('/api/ledger',params=q).json()
    assert len(body['entries'])==1 and Decimal(body['totals']['debit'])==25
    q['account_id']=r['second']['id'];leaf=r['http'].get('/api/ledger',params=q).json()
    assert Decimal(leaf['totals']['debit'])==15


def test_inactive_historical_accounts_and_parents_are_retained(chart):
    r=chart;post(r,'2025-12-31',[(r['account'],12,0),(r['cash'],0,12)])
    with r['db'].connect() as c:c.execute('UPDATE accounting.accounts SET is_active=false WHERE id=ANY(%s)',([r['parent']['id'],r['account']['id']],))
    rows,_=fetch(r)
    assert not rows[r['parent']['id']]['is_active'] and not rows[r['account']['id']]['is_active']
    assert Decimal(rows[r['parent']['id']]['closing_balance'])==12


def test_posted_invoice_correction_keeps_one_current_entry(chart):
    r=chart;http=r['http'];doc=upload(r)['id'];form=payload(r,doc)
    for line in form['lines']:line['vat_code_id']=r['portal_vat']['id']
    form['deductions']=[line['vat_amount'] for line in form['lines']]
    result=http.post(f'/api/documents/{doc}/book',json=form)
    assert result.status_code==200,result.text
    net=Decimal(form['net_total'])
    with r['db'].connect() as c:c.execute("UPDATE core.staff_users SET role='admin' WHERE id=%s",(r['staff']['id'],))
    state=http.get(f'/api/documents/{doc}').json()
    changed={**form,'version':state['document']['updated_at'],'invoice_version':state['invoice']['updated_at'],'correction_reason':'Poprawa konta'}
    for line in changed['lines']:line.update(account_id=r['second']['id'],booking_category_id=None)
    result=http.post(f'/api/documents/{doc}/book',json=changed)
    assert result.status_code==200,result.text
    rows,_=fetch(r)
    assert Decimal(rows[r['account']['id']]['closing_balance'])==0
    assert Decimal(rows[r['second']['id']]['closing_balance'])==net
    assert Decimal(rows[r['parent']['id']]['closing_balance'])==net
    params=dict(client_id=r['client']['id'],date_from='2026-01-01',date_to='2026-12-31',account_id=r['parent']['id'])
    current=http.get('/api/ledger',params=params).json()
    assert current['totals']['entries']==1 and current['entries'][0]['has_history']
    assert Decimal(current['totals']['debit'])==net
    params['account_id']=r['account']['id']
    assert http.get('/api/ledger',params=params).json()['totals']['entries']==0


def test_permissions_dates_and_unknown_accounts(chart):
    r=chart;http=r['http'];base=dict(client_id=r['client']['id'],date_from='2026-01-01',date_to='2026-12-31')
    assert http.get('/api/ledger/accounts',params={**base,'date_to':'2025-01-01'}).status_code==422
    assert http.get('/api/ledger/accounts',params={**base,'client_id':999999999}).status_code==404
    assert http.get('/api/ledger',params={**base,'account_id':0}).status_code==422
    assert http.get('/api/ledger',params={**base,'account_id':999999999}).status_code==404
    with r['db'].connect() as c:c.execute("UPDATE core.staff_users SET role='read_only' WHERE id=%s",(r['staff']['id'],))
    assert http.get('/api/ledger/accounts',params=base).status_code==200
    assert http.get('/api/ledger',params={**base,'account_id':r['parent']['id']}).status_code==200
    http.post('/api/logout')
    assert http.get('/api/ledger/accounts',params=base).status_code==401


def test_cyclic_hierarchy_is_safe_and_does_not_change_database(chart):
    r=chart
    with r['db'].connect() as c:c.execute('UPDATE accounting.accounts SET parent_account_id=%s WHERE id=%s',(r['account']['id'],r['parent']['id']))
    rows,data=fetch(r)
    assert data['hierarchy_warning'] and len(rows)==len(set(rows))
    assert r['parent']['id'] in rows and r['account']['id'] in rows
    with r['db'].connect() as c:
        assert c.execute('SELECT parent_account_id FROM accounting.accounts WHERE id=%s',(r['parent']['id'],)).fetchone()['parent_account_id']==r['account']['id']
    params=dict(client_id=r['client']['id'],date_from='2026-01-01',date_to='2026-12-31',account_id=r['parent']['id'])
    assert r['http'].get('/api/ledger',params=params).status_code==200


def test_large_values_keep_exact_cents_and_end_date_is_inclusive(chart):
    r=chart;value='9999999999999999.99'
    post(r,'2026-12-31',[(r['account'],value,0),(r['control']['140'],0,value)])
    rows,_=fetch(r)
    assert rows[r['account']['id']]['closing_balance']==value
    assert rows[r['parent']['id']]['closing_balance']==value
    assert rows[r['control']['140']['id']]['closing_balance']==value
