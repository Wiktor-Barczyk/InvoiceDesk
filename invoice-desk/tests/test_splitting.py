import io

import fitz
import pytest
from PIL import Image

from app.domain import Problem
from app.splitting import crop_bytes, prepare_parts, validate_regions
from test_flow import payload, rig, upload


def receipt_image():
    image = Image.new("RGB", (1400, 850), "#e6e6e6")
    for index in range(2):
        with fitz.open() as pdf:
            page = pdf.new_page(width=700, height=850)
            page.insert_text((35, 80), "\n".join([
                f"Factuurnummer: SPLIT-{index+1}", "Factuurdatum: 14-09-2026",
                f"Leverancier: Supplier {index+1}", "Afnemer: Example Construction B.V.", "",
                f"Totaal netto: {(index+1)*100},00", f"Totaal btw: {(index+1)*21},00",
                f"Te betalen: {(index+1)*121},00", "Valuta: EUR", "BTW 21%",
            ]), fontsize=23, lineheight=1.8)
            pix = page.get_pixmap()
            image.paste(Image.frombytes("RGB", (pix.width, pix.height), pix.samples), (index*700, 0))
    output=io.BytesIO();image.save(output,"PNG");return output.getvalue()


@pytest.fixture(autouse=True)
def manual_uploads(monkeypatch):
    monkeypatch.setattr('app.auto_split.detect', lambda *args: {'status':'manual','regions':[]})


def photo_upload(rig):
    result=rig['http'].post('/api/documents/upload',data={'client_id':rig['client']['id']},
        files={'file':('dwa-paragony.png',receipt_image(),'image/png')})
    assert result.status_code==200,result.text
    doc_id=result.json()['id']
    state=rig['http'].get(f'/api/documents/{doc_id}').json()
    return doc_id,{'version':state['document']['updated_at'],'file_id':state['files'][0]['id'],
        'regions':[{'page':1,'box':[0,0,.5,1]},{'page':1,'box':[.5,0,1,1]}]}


def test_split_separate_ocr_approval_and_idempotency(rig):
    http=rig['http'];doc_id,data=photo_upload(rig)
    original=http.get(f'/api/documents/{doc_id}/file').content
    response=http.post(f'/api/documents/{doc_id}/split',json=data)
    assert response.status_code==200,response.text
    children=response.json()['children'];assert len(children)==2
    again=http.post(f'/api/documents/{doc_id}/split',json=data)
    assert again.status_code==200 and again.json()['already_split']
    assert [c['id'] for c in again.json()['children']]==[c['id'] for c in children]
    parent=http.get(f'/api/documents/{doc_id}').json()
    assert parent['document']['status']=='archived' and not parent['split']['is_child']
    assert http.get(f'/api/documents/{doc_id}/file').content==original
    listing=http.get('/api/documents',params={'client_id':rig['client']['id']}).json()
    assert {d['id'] for d in listing}=={c['id'] for c in children}
    assert listing[0]['total_count']==2
    for index,ch in enumerate(children,1):
        state=http.get(f"/api/documents/{ch['id']}").json()
        assert state['split']['is_child'] and state['split']['source_document_id']==doc_id
        assert state['ocr']['extracted_fields']['invoice_number']==f'SPLIT-{index}'
        assert state['ocr']['extracted_fields']['gross_total']==f'{index*121}.00'
        approved=http.post(f"/api/documents/{ch['id']}/approve",json=payload(rig,ch['id']))
        assert approved.status_code==200,approved.text
    undo=http.post(f'/api/documents/{doc_id}/unsplit',json={'version':parent['document']['updated_at'],'batch_id':parent['split']['batch_id']})
    assert undo.status_code==409 and 'zapisaną fakturę' in undo.text
    assert http.post(f'/api/documents/{doc_id}/approve',json={}).status_code==409
    with rig['db'].connect() as c:
        assert c.execute('SELECT count(*) AS n FROM accounting.invoices WHERE client_id=%s',(rig['client']['id'],)).fetchone()['n']==2
        assert c.execute("SELECT count(*) AS n FROM documents.stored_files WHERE original_file_id=%s",(data['file_id'],)).fetchone()['n']>=2


def test_undo_and_resplit_preserve_history(rig):
    http=rig['http'];doc_id,data=photo_upload(rig)
    first=http.post(f'/api/documents/{doc_id}/split',json=data).json()
    parent=http.get(f'/api/documents/{doc_id}').json()
    result=http.post(f'/api/documents/{doc_id}/unsplit',json={'version':parent['document']['updated_at'],'batch_id':parent['split']['batch_id']})
    assert result.status_code==200,result.text
    restored=http.get(f'/api/documents/{doc_id}').json()
    assert restored['document']['status']=='to_book' and restored['split'] is None
    assert http.post(f'/api/documents/{doc_id}/split',json=data).status_code==409
    for ch in first['children']:
        assert http.get(f"/api/documents/{ch['id']}").json()['document']['status']=='archived'
        assert http.post(f"/api/documents/{ch['id']}/approve",json={}).status_code==409
    data['version']=restored['document']['updated_at']
    second=http.post(f'/api/documents/{doc_id}/split',json=data)
    assert second.status_code==200,second.text
    assert {c['id'] for c in second.json()['children']}.isdisjoint(c['id'] for c in first['children'])


def test_split_permissions_stale_input_and_saved_invoice(rig):
    http=rig['http'];doc_id,data=photo_upload(rig)
    stale={**data,'version':'old'}
    assert http.post(f'/api/documents/{doc_id}/split',json=stale).status_code==409
    bad={**data,'file_id':999999999}
    assert http.post(f'/api/documents/{doc_id}/split',json=bad).status_code==404
    csrf=http.headers.pop('X-CSRF-Token')
    assert http.post(f'/api/documents/{doc_id}/split',json=data).status_code==403
    http.headers['X-CSRF-Token']=csrf
    with rig['db'].connect() as c:c.execute("UPDATE core.staff_users SET role='read_only' WHERE id=%s",(rig['staff']['id'],))
    assert http.post(f'/api/documents/{doc_id}/split',json=data).status_code==403
    with rig['db'].connect() as c:c.execute("UPDATE core.staff_users SET role='accountant' WHERE id=%s",(rig['staff']['id'],))
    d=upload(rig)['id'];assert http.post(f'/api/documents/{d}/approve',json=payload(rig,d)).status_code==200
    state=http.get(f'/api/documents/{d}').json()
    bad={**data,'file_id':state['files'][0]['id'],'version':state['document']['updated_at']}
    assert http.post(f'/api/documents/{d}/split',json=bad).status_code==409


def test_partial_split_rollback(rig):
    http=rig['http'];doc_id,data=photo_upload(rig)
    with rig['db'].connect() as c:
        c.execute("""CREATE FUNCTION documents.test_split_failure() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF NEW.source_key='2' THEN RAISE EXCEPTION 'test failure'; END IF; RETURN NEW; END$$""")
        c.execute('CREATE TRIGGER test_split_failure BEFORE INSERT ON documents.document_import_items FOR EACH ROW EXECUTE FUNCTION documents.test_split_failure()')
    try:
        response=http.post(f'/api/documents/{doc_id}/split',json=data)
        assert response.status_code==503,response.text
        parent=http.get(f'/api/documents/{doc_id}').json()
        assert parent['document']['status']=='to_book' and parent['split'] is None
        with rig['db'].connect() as c:
            assert c.execute('SELECT count(*) AS n FROM documents.accounting_documents WHERE client_id=%s',(rig['client']['id'],)).fetchone()['n']==1
            assert c.execute('SELECT count(*) AS n FROM documents.document_imports WHERE client_id=%s',(rig['client']['id'],)).fetchone()['n']==0
    finally:
        with rig['db'].connect() as c:
            c.execute('DROP TRIGGER test_split_failure ON documents.document_import_items')
            c.execute('DROP FUNCTION documents.test_split_failure()')


@pytest.mark.parametrize('regions',[
    [],[{'page':1,'box':[0,0,1,1]}],
    [{'page':1,'box':[0,0,.7,1]},{'page':1,'box':[.5,0,1,1]}],
    [{'page':1,'box':[-.1,0,.5,1]},{'page':1,'box':[.5,0,1,1]}],
    [{'page':0,'box':[0,0,.5,1]},{'page':1,'box':[.5,0,1,1]}],
    [{'page':True,'box':[0,0,.5,1]},{'page':1,'box':[.5,0,1,1]}],
    [{'page':1,'box':[0,0,float('nan'),1]},{'page':1,'box':[.5,0,1,1]}],
    [{'page':1,'box':[0,0,.5,1],'rotation':45},{'page':1,'box':[.5,0,1,1]}],
])
def test_invalid_regions(regions):
    with pytest.raises(Problem):validate_regions(regions,1)


def test_crop_orientation_and_pdf_pages(tmp_path):
    im=Image.new('RGB',(200,100),'red');im.paste('blue',(100,0,200,100))
    exif=Image.Exif();exif[274]=6
    path=tmp_path/'rotated.jpg';im.save(path,exif=exif)
    region={'page':1,'box':[0,0,1,.5],'rotation':90}
    crop=Image.open(io.BytesIO(crop_bytes(path,region)))
    assert crop.size==(100,100) and crop.getpixel((50,50))[0]>240
    path=tmp_path/'two-pages.pdf'
    with fitz.open() as pdf:
        for color in [(1,0,0),(0,0,1)]:
            page=pdf.new_page(width=200,height=100);page.draw_rect(page.rect,color=color,fill=color)
        pdf.save(path)
    regions=validate_regions([{'page':1,'box':[0,0,1,1]},{'page':2,'box':[0,0,1,1]}],2)
    crops=[Image.open(io.BytesIO(crop_bytes(path,r))) for r in regions]
    assert crops[0].getpixel((50,50))[0]>240 and crops[1].getpixel((50,50))[2]>240


def test_identical_crops_are_rejected(tmp_path, monkeypatch):
    path=tmp_path/'identical.png';Image.new('RGB',(200,100),'white').save(path)
    monkeypatch.setattr('app.splitting.extract',lambda *a:{'raw':{'pages':[]},'fields':{}})
    regions=validate_regions([{'page':1,'box':[0,0,.5,1]},{'page':1,'box':[.5,0,1,1]}],1)
    with pytest.raises(Problem,match='identyczną zawartość'):prepare_parts(path,regions,'eng')
