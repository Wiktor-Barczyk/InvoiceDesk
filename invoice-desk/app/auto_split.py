"""Conservative local layout detection. Original files remain untouched."""
import csv
import io
import hashlib
import re
import subprocess
import tempfile
import unicodedata
from pathlib import Path

from PIL import Image

from . import processing
from .db import document, audit, jsonb
from .domain import Problem
from .extraction import image_bytes, inspect_file
from .splitting import (active_batch, batch_children, check_split, prepare_parts,
                        save_split, split_context, split_signature, validate_regions)

VERSION = 'local-layout-1'
HEADER = re.compile(r'parag|fiscal|fiskal|rechnung|quittung|kassenbon|receipt|invoice|factuur|faktura|beleg',re.I)
TOTAL = re.compile(r'total|totaal|zaplat|betalen|gesamt|sum[ame]|betrag|amount',re.I)
DATE = re.compile(r'\b\d{1,4}[-/.]\d{1,2}[-/.]\d{2,4}\b')
AMOUNT = re.compile(r'\b\d+[,.]\d{2}\b')


def words(raw, languages):
    with tempfile.TemporaryDirectory(prefix='invoice-layout-') as tmp:
        image = Path(tmp)/'page.png'
        image.write_bytes(raw)
        run = subprocess.run(['tesseract',str(image),'stdout','-l',languages,'--psm','11','tsv'],
                             capture_output=True,check=True,timeout=40)
    result = []
    for row in csv.DictReader(io.StringIO(run.stdout.decode('utf-8',errors='replace')),delimiter='\t',quoting=csv.QUOTE_NONE):
        text = row.get('text','').strip()
        if not text or float(row['conf']) < 15:
            continue
        x,y,w,h = (int(row[key]) for key in ('left','top','width','height'))
        if w > 0 and h > 0:
            result.append((x,y,x+w,y+h,text))
    return result


def text_kind(items):
    text=' '.join(x[4] for x in sorted(items,key=lambda x:(x[1],x[0])))
    text=''.join(c for c in unicodedata.normalize('NFKD',text) if not unicodedata.combining(c))
    if len(items)<8 or not AMOUNT.search(text):
        return 0
    return 2 if len(items)>=12 and HEADER.search(text) and TOTAL.search(text) and DATE.search(text) else 1


def partitions(items, image, axis):
    size=image.size[axis]
    spans=sorted((w[axis],w[axis+2]) for w in items)
    gaps=[];end=spans[0][1]
    for start,finish in spans[1:]:
        if start-end>=max(14,round(size*.017)):
            cut=(start+end)//2
            if .10*size<cut<.90*size:
                gaps.append(cut)
        end=max(end,finish)
    if not gaps or len(gaps)>24:
        return []
    groups=[];bounds=[0,*gaps,size]
    for lo,hi in zip(bounds,bounds[1:]):
        group=[w for w in items if lo<=(w[axis]+w[axis+2])/2<hi]
        groups.append(group)
    # Dynamic programming merges text columns which belong to one receipt.
    best={0:[]}
    for right in range(1,len(groups)+1):
        for left in range(right):
            if left not in best:
                continue
            group=[word for part in groups[left:right] for word in part]
            kind=text_kind(group)
            if not kind or bounds[right]-bounds[left]<size*.12:
                continue
            candidate=best[left]+[(bounds[left],bounds[right],kind)]
            rank=lambda rows:(sum(k==2 for _,_,k in rows),len(rows))
            if right not in best or rank(candidate)>rank(best[right]):
                best[right]=candidate
    result=best.get(len(groups),[])
    if not 2<=len(result)<=20:
        return []
    headers=sum(bool(HEADER.search(' '.join(w[4] for w in items if lo<=(w[axis]+w[axis+2])/2<hi)))
                for lo,hi,_ in result)
    if headers<2:
        return []
    # A cut must also cross a light gutter, not just missed OCR text.
    gray=image.convert('L')
    dark=gray.point(lambda p:255 if p<155 else 0)
    for _,edge,_ in result[:-1]:
        box=(max(0,edge-3),0,min(image.width,edge+3),image.height) if axis==0 else (0,max(0,edge-3),image.width,min(image.height,edge+3))
        density=dark.crop(box).resize((1,1),Image.Resampling.BOX).getpixel((0,0))/255
        if density>.045:
            return []
    return [{'page':1,'rotation':0,'box':[lo/size,0,hi/size,1] if axis==0 else [0,lo/size,1,hi/size],
             'confident':kind==2} for lo,hi,kind in result]


def detect(path, languages='pol+nld+eng'):
    info=inspect_file(path.read_bytes())
    if info['pages']!=1:
        return {'status':'manual','regions':[],'message':'Dokument ma kilka stron. Użyj ręcznego podziału, aby zachować strony jednej faktury razem.'}
    try:
        raw=image_bytes(path,1,2.5,max_dimension=2000)
        items=words(raw,languages)
        if not items:
            raise ValueError('no text')
        with Image.open(io.BytesIO(raw)) as image:
            candidate=layout(items,image)
        if not candidate:
            return {'status':'manual','regions':[],'message':'Nie rozpoznano pewnego podziału. Możesz zaznaczyć dokumenty ręcznie.'}
        confident=all([x.pop('confident') for x in candidate])
        regions=validate_regions(candidate,1)
        return {'status':'confident' if confident else 'review','regions':regions,
                'message':'Sprawdź zaproponowane ramki przed rozdzieleniem.'}
    except (OSError,subprocess.SubprocessError,ValueError,KeyError):
        return {'status':'manual','regions':[],'message':'Nie udało się odczytać układu skanu. Zaznacz dokumenty ręcznie.'}


def layout(items, image, depth=0):
    proposals=[p for p in (partitions(items,image,axis) for axis in (0,1)) if p]
    if not proposals:
        return []
    chosen=max(proposals,key=lambda p:(all(x['confident'] for x in p),len(p)))
    if depth>=3:
        return chosen
    result=[]
    for part in chosen:
        x0,y0,x1,y1=part['box']
        left,top,right,bottom=round(x0*image.width),round(y0*image.height),round(x1*image.width),round(y1*image.height)
        group=[(a-left,b-top,c-left,d-top,text) for a,b,c,d,text in items
               if left<=(a+c)/2<right and top<=(b+d)/2<bottom]
        nested=layout(group,image.crop((left,top,right,bottom)),depth+1) if group else []
        if not nested:
            result.append(part)
            continue
        for sub in nested:
            a,b,c,d=sub['box']
            sub['box']=[x0+a*(x1-x0),y0+b*(y1-y0),x0+c*(x1-x0),y0+d*(y1-y0)]
            result.append(sub)
    return result if len(result)<=20 else chosen


def run(db, files, storage, doc_id, version, languages, background, mode, staff_id, force=False):
    with db.connect() as c:
        state=document(c,doc_id,for_update=True)
        batch=active_batch(c,doc_id)
        if batch:
            return {'status':'split','children':batch_children(c,batch['id']),'already_split':True}
        if state['invoice'] or state['document']['status'] not in processing.EDITABLE:
            raise Problem('Ten dokument nie może być automatycznie rozdzielony.',409)
        if split_context(c,doc_id):
            return {'status':'manual','regions':[],'message':'To jest już wycinek. Podział możesz poprawić na oryginale.'}
        if not force and c.execute("""SELECT 1 FROM documents.document_imports
            WHERE source_template_version='invoice-desk-split-v1'
            AND import_details @> %s LIMIT 1""",
            (jsonb({'source_document_id':doc_id,'state':'undone'}),)).fetchone():
            return {'status':'manual','regions':[],'message':'Poprzedni podział cofnięto. Możesz ponownie wybrać podział automatyczny lub ręczny.'}
        originals=[f for f in state['files'] if f['file_role']=='original']
        if len(originals)!=1 or len(state['files'])!=1:
            return {'status':'manual','regions':[],'message':'Dokument ma kilka załączników. Użyj ręcznego podziału.'}
        file_id=originals[0]['id']
        if version!=state['document']['updated_at'].isoformat():
            raise Problem('Dokument się zmienił. Otwórz go ponownie.',409)
        path,row=files.resolve(c,doc_id,file_id)
        fingerprint=files.fingerprint(path)
    result=detect(path,languages)
    result.update(file_id=file_id,version=version)
    if result['status']!='confident':
        return result
    signature=split_signature(version,file_id,result['regions'])
    with db.connect() as c:
        check_split(c,doc_id,version,file_id,signature)
    parts=prepare_parts(path,result['regions'],languages,background=background)
    if files.fingerprint(path)!=fingerprint or hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:
        raise Problem('Plik zmienił się podczas rozdzielania. Spróbuj ponownie.',409)
    with db.connect() as c:
        saved=save_split(c,doc_id,version,file_id,signature,parts,storage,staff_id)
        if not saved['already_split']:
            batch=active_batch(c,doc_id)
            updated=c.execute("""UPDATE documents.document_imports
                SET import_details=import_details || %s WHERE id=%s RETURNING *""",
                (jsonb({'method':'automatic','detector_version':VERSION}),batch['id'])).fetchone()
            audit(c,'documents.document_imports',batch['id'],staff_id,updated,batch)
        if background:
            for child in saved['children']:
                processing.enqueue(c,mode,doc_id=child['id'])
            c.execute("""UPDATE documents.processing_jobs SET status='cancelled',updated_at=now(),
                         error_message='Oryginał został automatycznie rozdzielony na osobne dokumenty.'
                         WHERE accounting_document_id=%s""",(doc_id,))
    return {'status':'split',**saved}
