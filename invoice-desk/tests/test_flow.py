import copy
import hashlib
import os
import uuid
from datetime import date
from pathlib import Path

import fitz
import pytest
from fastapi.testclient import TestClient

from app.demo_data import sample_invoice
from app.db import Database, insert
from app.domain import Problem, money
from app.extraction import extract, parse_text
from app.main import create_app
from app.security import hash_password, verify_password

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def rig(tmp_path):
    url=os.environ.get('TEST_DATABASE_URL')
    if not url:pytest.skip('Ustaw TEST_DATABASE_URL na oddzielną bazę invoice_desk_test.')
    if 'invoice_desk_test' not in url and os.environ.get('TEST_EMBEDDED')!='1':
        pytest.fail('Testy wymagają oddzielnej bazy invoice_desk_test.')
    db=Database(url)
    tag=uuid.uuid4().hex[:10]
    with db.connect() as c:
        if not c.execute("SELECT to_regclass('core.clients') AS t").fetchone()['t']:
            c.execute((ROOT/'database/schema_v5.sql').read_text())
        staff=insert(c,'core.staff_users',{'email':f'test-{tag}@example.test','full_name':'Pracownik testowy','role':'accountant','password':hash_password('test-password-123')})
        cl=insert(c,'core.clients',{'odoo_partner_id':int(tag,16),'company_name':'Example Construction B.V.','legal_form':'eenmanszaak'})
        ac=insert(c,'accounting.accounts',{'account_code':tag,'account_name':'Materiały testowe','account_type':'expense'})
        vat=insert(c,'accounting.vat_codes',{'vat_code':tag,'vat_name':'Test zakup 21','vat_rate_percent':21,'vat_direction':'input'})
        cat=insert(c,'accounting.staff_booking_categories',{'category_code':tag,'category_name':'Materiały testowe'})
        insert(c,'accounting.booking_category_account_mappings',{'booking_category_id':cat['id'],'account_id':ac['id'],'default_vat_code_id':vat['id']})
    app=create_app({'DATABASE_URL':url,'APP_SESSION_SECRET':'test-only-secret-'*4,'DOCUMENT_STORAGE':str(tmp_path),'OCR_LANGUAGES':'eng'})
    with TestClient(app) as http:
        assert http.post('/api/login',json={'email':staff['email'],'password':'test-password-123'}).status_code==200
        session=http.get('/api/me').json();http.headers['X-CSRF-Token']=session['csrf']
        yield {'db':db,'http':http,'client':cl,'staff':staff,'account':ac,'vat':vat,'category':cat,'storage':tmp_path}


def upload(rig,changed=False):
    raw=sample_invoice()
    if changed:
        with fitz.open(stream=raw,filetype='pdf') as pdf:
            pdf.set_metadata({'subject':uuid.uuid4().hex});raw=pdf.tobytes()
    response=rig['http'].post('/api/documents/upload',data={'client_id':rig['client']['id']},files={'file':('invoice.pdf',raw,'application/pdf')})
    assert response.status_code==200,response.text
    return response.json()


def payload(rig,doc_id):
    state=rig['http'].get(f'/api/documents/{doc_id}').json();f=copy.deepcopy(state['ocr']['extracted_fields'])
    f.update({'client_id':rig['client']['id'],'invoice_type':'purchase','vat_date':f['invoice_date'],'version':state['document']['updated_at'],'reviewed':True})
    for line in f['lines']:line.update({'account_id':rig['account']['id'],'vat_code_id':rig['vat']['id'],'booking_category_id':rig['category']['id']})
    return f


def counts(rig):
    with rig['db'].connect() as c:
        return c.execute("SELECT (SELECT count(*) FROM accounting.invoices WHERE client_id=%s) AS invoices,(SELECT count(*) FROM core.client_counterparties WHERE client_id=%s) AS parties",(rig['client']['id'],rig['client']['id'])).fetchone()


def test_complete_approval_and_idempotency(rig):
    assert rig['http'].get('/api/clients').status_code==200
    refs=rig['http'].get('/api/references',params={'client_id':rig['client']['id']})
    assert refs.status_code==200 and refs.json()['client']['id']==rig['client']['id']
    doc=upload(rig)['id'];f=payload(rig,doc)
    response=rig['http'].post(f'/api/documents/{doc}/approve',json=f)
    assert response.status_code==200,response.text
    invoice_id=response.json()['id']
    with rig['db'].connect() as c:
        inv=c.execute('SELECT * FROM accounting.invoices WHERE id=%s',(invoice_id,)).fetchone()
        lines=c.execute('SELECT * FROM accounting.invoice_lines WHERE invoice_id=%s ORDER BY line_number',(invoice_id,)).fetchall()
        assert str(inv['gross_total'])=='1439.90' and inv['status']=='approved'
        assert inv['general_ledger_entry_id'] is None and inv['client_id']==rig['client']['id']
        assert len(lines)==3 and sum(x['net_amount'] for x in lines)==inv['net_total']
        assert lines[0]['quantity']==20 and str(lines[0]['unit_price'])=='24.5000'
        assert c.execute('SELECT count(*) AS n FROM accounting.invoice_validation_checks WHERE invoice_id=%s',(invoice_id,)).fetchone()['n']==5
        assert c.execute("SELECT count(*) AS n FROM audit.change_log WHERE schema_name='accounting' AND table_name='invoices' AND record_id=%s AND staff_user_id=%s",(invoice_id,rig['staff']['id'])).fetchone()['n']==1
    again=rig['http'].post(f'/api/documents/{doc}/approve',json=f)
    assert again.status_code==200 and again.json()['id']==invoice_id and again.json()['already_approved']
    assert counts(rig)=={'invoices':1,'parties':1}


def test_duplicates_hash_and_business_key(rig):
    first=upload(rig);same=upload(rig)
    assert same['id']==first['id'] and same['duplicate']
    assert rig['http'].post(f"/api/documents/{first['id']}/approve",json=payload(rig,first['id'])).status_code==200
    second=upload(rig,changed=True)['id'];r=rig['http'].post(f'/api/documents/{second}/approve',json=payload(rig,second))
    assert r.status_code==409 and 'Duplikat' in r.text
    assert counts(rig)=={'invoices':1,'parties':1}


def test_totals_and_review_rejected_without_partial_write(rig):
    doc=upload(rig)['id'];f=payload(rig,doc)
    for mutate in [lambda x:x.update(gross_total='1440.00'),lambda x:x['lines'][0].update(vat_amount='100.00'),lambda x:x.update(reviewed=False),lambda x:x.update(net_total='NaN')]:
        bad=copy.deepcopy(f);mutate(bad)
        assert rig['http'].post(f'/api/documents/{doc}/approve',json=bad).status_code==422
    assert counts(rig)=={'invoices':0,'parties':0}


def test_stale_version_and_wrong_client(rig):
    doc=upload(rig)['id'];f=payload(rig,doc);f['version']='old'
    assert rig['http'].post(f'/api/documents/{doc}/approve',json=f).status_code==409
    f=payload(rig,doc);f['client_id']+=10000
    assert rig['http'].post(f'/api/documents/{doc}/approve',json=f).status_code==409
    assert counts(rig)['invoices']==0


def test_role_csrf_and_file_access(rig):
    doc=upload(rig)['id'];http=rig['http'];f=payload(rig,doc)
    assert http.get(f'/api/documents/{doc}/preview/1').status_code==200
    assert http.get(f'/api/documents/{doc}/preview/2').status_code==404
    assert http.get(f'/api/documents/{doc}/file?file_id=999999999').status_code==404
    csrf=http.headers.pop('X-CSRF-Token')
    assert http.post(f'/api/documents/{doc}/approve',json=f).status_code==403
    http.headers['X-CSRF-Token']=csrf
    with rig['db'].connect() as c:c.execute("UPDATE core.staff_users SET role='read_only' WHERE id=%s",(rig['staff']['id'],))
    assert http.post(f'/api/documents/{doc}/approve',json=f).status_code==403
    assert http.get(f'/api/documents/{doc}/file').status_code==200
    http.cookies.clear()
    assert http.get(f'/api/documents/{doc}/file').status_code==401


def test_closed_period_and_invalid_vat(rig):
    doc=upload(rig)['id'];f=payload(rig,doc)
    with rig['db'].connect() as c:c.execute("UPDATE accounting.vat_codes SET vat_direction='output' WHERE id=%s",(rig['vat']['id'],))
    assert rig['http'].post(f'/api/documents/{doc}/approve',json=f).status_code==422
    with rig['db'].connect() as c:
        c.execute("UPDATE accounting.vat_codes SET vat_direction='input' WHERE id=%s",(rig['vat']['id'],))
        insert(c,'tax.accounting_period_locks',{'client_id':rig['client']['id'],'lock_type':'VAT','locked_through_date':date(2026,9,30)})
    assert rig['http'].post(f'/api/documents/{doc}/approve',json=f).status_code==409
    assert counts(rig)['invoices']==0


def test_failure_after_line_insert_rolls_back(rig):
    doc=upload(rig)['id'];f=payload(rig,doc)
    with rig['db'].connect() as c:
        c.execute("""CREATE OR REPLACE FUNCTION accounting.test_reject_validation() RETURNS trigger LANGUAGE plpgsql AS $$BEGIN RAISE EXCEPTION 'test failure'; END$$""")
        c.execute("CREATE TRIGGER test_failure BEFORE INSERT ON accounting.invoice_validation_checks FOR EACH ROW EXECUTE FUNCTION accounting.test_reject_validation()")
    try:
        r=rig['http'].post(f'/api/documents/{doc}/approve',json=f)
        assert r.status_code==503,r.text
        assert counts(rig)=={'invoices':0,'parties':0}
        with rig['db'].connect() as c:
            assert c.execute('SELECT count(*) AS n FROM accounting.invoice_source_documents WHERE accounting_document_id=%s',(doc,)).fetchone()['n']==0
    finally:
        with rig['db'].connect() as c:
            c.execute('DROP TRIGGER test_failure ON accounting.invoice_validation_checks')
            c.execute('DROP FUNCTION accounting.test_reject_validation()')


def test_pdf_scan_and_all_pages(tmp_path):
    sample=tmp_path/'invoice.pdf';sample.write_bytes(sample_invoice());fields=extract(sample,'eng')['fields']
    assert fields['seller_name_snapshot']=='Example Materials B.V.'
    assert len(fields['lines'])==3 and fields['gross_total']=='1439.90'
    with fitz.open(sample) as pdf:
        pix=pdf[0].get_pixmap(matrix=fitz.Matrix(2,2));path=tmp_path/'scan.png';pix.save(path)
    scan=extract(path,'eng')
    assert scan['fields']['gross_total']=='1439.90' and 'tesseract' in scan['engine']
    multi=tmp_path/'pages.pdf'
    with fitz.open() as pdf:
        p=pdf.new_page();p.insert_text((50,50),'Factuurnummer: MULTI-123\nFactuurdatum: 14-09-2026\nLeverancier: Multi Page B.V.\nAfnemer: Demo Buyer')
        p=pdf.new_page();p.insert_text((50,50),'Totaal excl. btw: EUR 100,00\nBTW 21%: EUR 21,00\nTotaal incl. btw: EUR 121,00')
        pdf.save(multi)
    extracted=extract(multi,'eng')
    assert len(extracted['raw']['pages'])==2 and extracted['fields']['gross_total']=='121.00'


def test_ambiguous_values_and_money():
    fields,_,warnings=parse_text('Factuurnummer: A-1\nFactuurnummer: B-2\nTotaal incl. btw: EUR 1.234,56')
    assert 'invoice_number' not in fields and warnings
    assert str(money('1.234,56'))=='1234.56' and str(money('1,234.56'))=='1234.56'
    for invalid in ('NaN','Infinity','-1.00','1.001'):
        with pytest.raises(Problem):money(invalid)
    encoded=hash_password('example-password')
    assert verify_password('example-password',encoded) and not verify_password('wrong',encoded)
