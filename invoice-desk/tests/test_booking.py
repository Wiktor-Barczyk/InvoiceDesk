from datetime import date
from app.db import insert, references
from app.ai import SCHEMA, sanitize
from test_flow import rig, upload, payload, counts
from test_processing import proposal


def test_single_date_and_category_only_approval(rig):
    doc=upload(rig)['id'];p=payload(rig,doc);p.pop('vat_date')
    for line in p['lines']:line.pop('account_id')
    r=rig['http'].post(f'/api/documents/{doc}/approve',json=p)
    assert r.status_code==200,r.text
    with rig['db'].connect() as c:
        row=c.execute('SELECT invoice_date,vat_date FROM accounting.invoices WHERE id=%s',(r.json()['id'],)).fetchone()
        assert row['vat_date']==row['invoice_date']
        assert {r['account_id'] for r in c.execute('SELECT account_id FROM accounting.invoice_lines WHERE invoice_id=%s',(r.json()['id'],)).fetchall()}=={rig['account']['id']}
    assert 'vat_date' not in rig['http'].get(f'/api/documents/{doc}').json()['invoice']
    assert 'vat_date' not in SCHEMA['properties']['fields']['properties']


def test_old_date_cannot_bypass_period_or_vat_validity(rig):
    doc=upload(rig)['id'];p=payload(rig,doc)
    p['invoice_date']='2026-09-20';p['vat_date']='2026-10-01'
    with rig['db'].connect() as c:
        insert(c,'tax.accounting_period_locks',{'client_id':rig['client']['id'],'lock_type':'VAT','locked_through_date':date(2026,9,30)})
    r=rig['http'].post(f'/api/documents/{doc}/approve',json=p)
    assert r.status_code==409 and counts(rig)['invoices']==0
    p['invoice_date']='2026-10-01';p['vat_date']='broken old value'
    r=rig['http'].post(f'/api/documents/{doc}/approve',json=p)
    assert r.status_code==200,r.text


def test_mapping_is_explicit_audited_and_client_scoped(rig):
    http=rig['http'];doc=upload(rig)['id'];p=payload(rig,doc)
    with rig['db'].connect() as c:
        other=insert(c,'accounting.accounts',{'account_code':'new-'+str(rig['category']['id']),'account_name':'Drugie konto','account_type':'expense'})
        old=references(c,rig['client']['id'])
    url=f"/api/booking-mappings/{rig['category']['id']}"
    body={'client_id':rig['client']['id'],'account_id':other['id'],'expected_account_id':rig['account']['id']}
    csrf=http.headers.pop('X-CSRF-Token');assert http.put(url,json=body).status_code==403;http.headers['X-CSRF-Token']=csrf
    for bad in [{**body,'account_id':True},{**body,'sql':'update'},{**body,'expected_account_id':None}]:
        assert http.put(url,json=bad).status_code in (409,422)
    r=http.put(url,json=body);assert r.status_code==200,r.text
    assert http.put(url,json=body).status_code==409
    refs=r.json();choice=next(x for x in refs['booking_choices'] if x['booking_category_id']==rig['category']['id'])
    assert choice['account_id']==other['id'] and choice['default_vat_code_id']==rig['vat']['id']
    # Stale forms must not silently save their previous ledger account.
    assert http.post(f'/api/documents/{doc}/approve',json=p).status_code==422
    for line in p['lines']:line.pop('account_id')
    assert http.post(f'/api/documents/{doc}/approve',json=p).status_code==200
    with rig['db'].connect() as c:
        original=c.execute('SELECT account_id FROM accounting.booking_category_account_mappings WHERE booking_category_id=%s AND client_id IS NULL',(rig['category']['id'],)).fetchone()
        assert original['account_id']==rig['account']['id']
        assert c.execute("SELECT count(*) AS n FROM audit.change_log WHERE table_name='booking_category_account_mappings' AND staff_user_id=%s",(rig['staff']['id'],)).fetchone()['n']==1
        c.execute("UPDATE core.staff_users SET role='read_only' WHERE id=%s",(rig['staff']['id'],))
    assert http.put(url,json={**body,'expected_account_id':other['id']}).status_code==403


def test_category_alone_approves_purchase_and_sale_without_active_ledger_accounts(rig):
    http=rig['http']
    with rig['db'].connect() as c:
        cat=insert(c,'accounting.staff_booking_categories',{'category_code':'unified-'+str(rig['category']['id']),'category_name':'Unmapped test category '+str(rig['category']['id'])})
        active=[x['id'] for x in c.execute('SELECT id FROM accounting.accounts WHERE is_active').fetchall()]
        c.execute('UPDATE accounting.accounts SET is_active=false WHERE is_active')
        c.execute("UPDATE accounting.vat_codes SET vat_direction='both' WHERE id=%s",(rig['vat']['id'],))
    try:
        refs=http.get('/api/references',params={'client_id':rig['client']['id']}).json()
        assert refs['accounts']==[]
        assert any(x['booking_category_id']==cat['id'] for x in refs['booking_choices'])
        for kind in ('purchase','sale'):
            doc=upload(rig,True)['id'];p=payload(rig,doc)
            p['invoice_type']=kind;p['invoice_number']='unified-'+kind
            for line in p['lines']:
                line['booking_category_id']=cat['id'];line.pop('account_id')
            response=http.post(f'/api/documents/{doc}/approve',json=p)
            assert response.status_code==200,response.text
            state=http.get(f'/api/documents/{doc}').json()
            assert len(state['invoice']['lines'])==len(p['lines'])
            assert all(x['booking_category_id']==cat['id'] and x['account_id'] is None for x in state['invoice']['lines'])
    finally:
        with rig['db'].connect() as c:
            c.execute('UPDATE accounting.accounts SET is_active=true WHERE id=ANY(%s)',(active,))


def test_missing_or_unknown_unified_choice_is_rejected(rig):
    doc=upload(rig)['id'];p=payload(rig,doc)
    for line in p['lines']:line['booking_category_id']=None;line['account_id']=None
    assert rig['http'].post(f'/api/documents/{doc}/approve',json=p).status_code==422
    for line in p['lines']:line['booking_category_id']=999999999
    assert rig['http'].post(f'/api/documents/{doc}/approve',json=p).status_code==422


def test_duplicate_names_have_one_choice_without_deleting_ids(rig):
    from app.booking import choices
    result=choices({'categories':[{'id':1,'category_name':'Transportkosten'},{'id':2,'category_name':'Transportkosten'}], 'accounts':[], 'mappings':[]})
    assert len(result)==1 and result[0]['category_ids']==[1,2]


def test_ai_uses_canonical_account_and_ignores_cached_date(rig):
    with rig['db'].connect() as c:refs=references(c,rig['client']['id'])
    p=proposal(rig);p['fields']['vat_date']='garbage from cached 1.3 tool'
    p['fields']['lines'][0]['account_id']=None
    r=sanitize(p,refs,{})
    assert 'vat_date' not in r['fields'] and r['fields']['lines'][0]['account_id']==rig['account']['id']
    assert r['raw']['warnings']==[]
    p['fields']['lines'][0]['account_id']=9999999
    r=sanitize(p,refs,{})
    assert r['fields']['lines'][0]['account_id']==rig['account']['id']
    assert r['raw']['warnings']
    p['fields']['lines'][0]['booking_category_id']=9999999
    r=sanitize(p,refs,{})
    assert r['fields']['lines'][0]['booking_category_id'] is None
    assert r['fields']['lines'][0]['account_id'] is None


def test_catalog_migration_is_repeatable_without_replacing_ids(rig):
    from pathlib import Path
    migration=(Path(__file__).resolve().parents[1]/'app/migrations/003_booking_categories.sql').read_text()
    with rig['db'].connect() as c:
        c.execute(migration)
        before=c.execute("SELECT id FROM accounting.staff_booking_categories WHERE category_name='Transportkosten'").fetchone()['id']
        c.execute(migration)
        after=c.execute("SELECT id FROM accounting.staff_booking_categories WHERE category_name='Transportkosten'").fetchone()['id']
        assert after==before
        assert not c.execute("SELECT 1 FROM accounting.staff_booking_categories WHERE lower(category_name)='totaal'").fetchone()


def test_historical_ledger_names_survive_inactive_catalog_entries(rig):
    doc=upload(rig)['id'];p=payload(rig,doc)
    result=rig['http'].post(f'/api/documents/{doc}/approve',json=p)
    assert result.status_code==200
    with rig['db'].connect() as c:
        c.execute('UPDATE accounting.accounts SET is_active=false WHERE id=%s',(rig['account']['id'],))
        c.execute('UPDATE accounting.staff_booking_categories SET is_active=false WHERE id=%s',(rig['category']['id'],))
        c.execute('UPDATE accounting.vat_codes SET is_active=false WHERE id=%s',(rig['vat']['id'],))
    state=rig['http'].get(f'/api/documents/{doc}').json()
    line=state['invoice']['lines'][0]
    assert line['account_code']==rig['account']['account_code']
    assert line['account_name']==rig['account']['account_name']
    assert line['booking_category_name']==rig['category']['category_name']
    assert line['vat_code_name']==rig['vat']['vat_code']
