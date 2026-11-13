'use strict';
const D = {regions:[], page:1, pages:1, image:null, start:null, drawing:null, selected:-1, zoom:100, sequence:0, saving:false, source:null};

function renderSplitContext(){
  const split=S.state.split, parent=split&&!split.is_child;
  $('invoiceForm').hidden=!!parent;$('recognitionDetails').hidden=!!parent;$('approvalFooter').hidden=!!parent;
  $('splitAction').hidden=readonly()||!!S.state.invoice||!!split||S.state.files.length!==1||S.state.files[0]?.file_role!=='original';
  $('splitContext').hidden=!split;$('splitContext').classList.toggle('is-child',!!split?.is_child);
  if(!split)return;
  const index=split.children.findIndex(c=>c.id===S.docId);
  $('splitContext').innerHTML=parent
    ?`<strong>Plik rozdzielony na ${split.children.length} dokumenty</strong><p>Sprawdź i zaksięguj każdy dokument osobno. Ten oryginał nie jest liczony drugi raz.</p>`
    :`<strong>Dokument ${index+1} z ${split.children.length}</strong><p>Każdy wycinek ma własne kwoty i status.</p><button type="button" class="text-button" data-split-doc="${split.source_document_id}">Zobacz oryginał i podział →</button>`;
  $('splitContext').innerHTML+=`<div class="split-siblings">${split.children.map((c,i)=>`<button type="button" class="secondary ${c.id===S.docId?'current':''}" data-split-doc="${c.id}" ${c.id===S.docId?'disabled':''}>Dokument ${i+1}<small class="split-child-status">${c.invoice_status==='posted'?'Zaksięgowany':c.invoice_status==='approved'?'Zatwierdzony':c.status==='archived'?'Archiwum':'Do sprawdzenia'}</small></button>`).join('')}</div>`;
  if(parent){$('invoiceStatus').textContent='Rozdzielony';if(S.user.role!=='read_only')$('splitContext').innerHTML+='<button id="splitUndo" type="button" class="text-button">Cofnij podział</button>';}
}
$('splitContext').onclick=async e=>{
  const go=e.target.closest('[data-split-doc]');
  if(go&&!S.busy&&guard())await openDocument(Number(go.dataset.splitDoc));
  if(e.target.closest('#splitUndo')&&!S.busy){
    if(!confirm('Cofnąć podział? Wycinki zostaną wycofane z kolejki, a oryginał wróci do sprawdzenia.'))return;
    busy(true,'Cofanie podziału…');
    try{await api(`/api/documents/${S.docId}/unsplit`,{method:'POST',body:JSON.stringify({version:S.state.document.updated_at,batch_id:S.state.split.batch_id})});S.dirty=false;await reloadList();await openDocument(S.docId);toast('Cofnięto podział. Możesz zaznaczyć dokumenty ponownie.');}catch(err){toast(err.message,true);}finally{busy(false);}
  }
};

function splitError(message=''){$('splitError').textContent=message;}
function splitChanged(){D.selected=-1;$('splitReviewed').checked=false;splitError();renderRegions();drawSplit();}
function updateSplitSave(){$('splitSave').disabled=D.saving||D.regions.length<2||!$('splitReviewed').checked;}
function renderRegions(){
  $('splitCount').textContent=D.regions.length;$('splitEmpty').hidden=!!D.regions.length;
  $('splitRegions').innerHTML=D.regions.map((r,i)=>`<div class="split-region ${D.selected===i?'selected':''}"><button class="split-part-select" type="button" data-region-select="${i}"><span class="split-thumb"><img src="${r.thumb}" alt="Wycinek ${i+1}" style="transform:rotate(${r.rotation}deg)"></span><span><strong>Dokument ${i+1}</strong><small>Strona ${r.page} · obrót ${r.rotation}°</small></span></button><div class="split-region-actions"><button type="button" class="text-button" data-region-rotate="${i}">Obróć o 90°</button><button type="button" class="text-button" data-region-delete="${i}">Usuń</button></div></div>`).join('');
  updateSplitSave();
}
function setSplitZoom(value){D.fit=false;D.zoom=Math.max(10,Math.min(300,Math.round(value)));$('splitCanvas').style.width=D.zoom+'%';$('splitZoomLabel').textContent=D.zoom+'%';}
let splitPaint=0;
function drawSplit(){if(!splitPaint)splitPaint=requestAnimationFrame(()=>{splitPaint=0;paintSplit();});}
function paintSplit(){
  if(!D.image)return;
  const canvas=$('splitCanvas'),ctx=canvas.getContext('2d'),w=canvas.width,h=canvas.height;
  ctx.clearRect(0,0,w,h);ctx.drawImage(D.image,0,0,w,h);
  const factor=w/Math.max(1,canvas.getBoundingClientRect().width),line=Math.max(2,2*factor);
  for(const [i,r] of D.regions.entries()){
    if(r.page!==D.page)continue;
    const [x0,y0,x1,y1]=r.box,x=x0*w,y=y0*h,rw=(x1-x0)*w,rh=(y1-y0)*h;
    ctx.fillStyle=D.selected===i?'#da8e1530':'#1d57491c';ctx.fillRect(x,y,rw,rh);
    ctx.strokeStyle=D.selected===i?'#a26300':'#1d5749';ctx.lineWidth=line;ctx.strokeRect(x,y,rw,rh);
    const size=26*factor;ctx.fillStyle=ctx.strokeStyle;ctx.fillRect(x,y,size,size);ctx.fillStyle='#fff';ctx.font=`bold ${14*factor}px sans-serif`;ctx.fillText(String(i+1),x+8*factor,y+18*factor);
  }
  if(D.drawing){const [x0,y0,x1,y1]=D.drawing;ctx.strokeStyle='#bb7621';ctx.lineWidth=line;ctx.setLineDash([6*factor,4*factor]);ctx.strokeRect(x0*w,y0*h,(x1-x0)*w,(y1-y0)*h);ctx.setLineDash([]);}
}
async function splitPage(page){
  stopSplitDrag();const seq=++D.sequence;D.page=page;D.image=null;D.start=null;D.drawing=null;
  $('splitCanvas').hidden=true;$('splitLoadMessage').hidden=false;$('splitLoadMessage').textContent='Wczytywanie zdjęcia…';$('splitPage').textContent=`${page} / ${D.pages}`;
  $('splitPrev').disabled=page<=1;$('splitNext').disabled=page>=D.pages;
  try{
    const response=await fetch(`/api/documents/${D.source.id}/preview/${page}?file_id=${D.source.fileId}`,{credentials:'same-origin'});
    if(!response.ok){const error=await response.json();throw new Error(error.detail||'Nie można wczytać strony.');}
    const url=URL.createObjectURL(await response.blob());const img=new Image();
    try{img.src=url;await img.decode();}finally{URL.revokeObjectURL(url);}
    if(seq!==D.sequence||!$('splitDialog').open)return;
    D.image=img;const canvas=$('splitCanvas');canvas.width=img.naturalWidth;canvas.height=img.naturalHeight;canvas.hidden=false;$('splitLoadMessage').hidden=true;if(D.fit)fitSplit();drawSplit();
  }catch(error){if(seq===D.sequence){$('splitLoadMessage').textContent=error.message;splitError(error.message);}}
}
function thumbnail(box){
  const c=document.createElement('canvas');c.width=160;c.height=160;const ctx=c.getContext('2d');ctx.fillStyle='#fff';ctx.fillRect(0,0,160,160);
  const [x0,y0,x1,y1]=box,w=(x1-x0)*D.image.naturalWidth,h=(y1-y0)*D.image.naturalHeight,scale=Math.min(160/w,160/h);
  ctx.drawImage(D.image,x0*D.image.naturalWidth,y0*D.image.naturalHeight,w,h,(160-w*scale)/2,(160-h*scale)/2,w*scale,h*scale);return c.toDataURL('image/jpeg',.75);
}
function addRegion(box){
  if(!D.image||D.saving)return false;
  if(D.regions.length>=20){splitError('Możesz zaznaczyć maksymalnie 20 dokumentów.');return false;}
  const [x0,y0,x1,y1]=box;
  if(Math.min(x1-x0,y1-y0)<.01||Math.min((x1-x0)*D.image.naturalWidth,(y1-y0)*D.image.naturalHeight)<32){splitError('Zaznacz większy obszar obejmujący cały dokument.');return false;}
  for(const r of D.regions){if(r.page!==D.page)continue;const [a,b,c,d]=r.box;const overlap=Math.max(0,Math.min(c,x1)-Math.max(a,x0))*Math.max(0,Math.min(d,y1)-Math.max(b,y0));if(overlap>.001*Math.min((c-a)*(d-b),(x1-x0)*(y1-y0))){splitError('Ramki nachodzą na siebie. Usuń poprzednią ramkę i zaznacz dokument ponownie.');return false;}}
  D.regions.push({page:D.page,box,rotation:0,thumb:thumbnail(box)});splitChanged();return true;
}
function splitPoint(event){const rect=$('splitCanvas').getBoundingClientRect();return [Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width)),Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height))];}
let splitDragFrame=0, splitPointer=null, splitLastTime=0;
function updateSplitDrawing(){
  if(!D.start||!splitPointer)return;
  const [x,y]=splitPoint(splitPointer),[a,b]=D.start;
  D.drawing=[Math.min(x,a),Math.min(y,b),Math.max(x,a),Math.max(y,b)];drawSplit();
}
function scrollSplitDrag(time){
  if(!D.start||!splitPointer){splitDragFrame=0;return;}
  const wrap=$('splitCanvasWrap'),r=wrap.getBoundingClientRect(),edge=42;
  const speed=(p,lo,hi)=>p<lo+edge?-Math.min(1,(lo+edge-p)/edge):p>hi-edge?Math.min(1,(p-hi+edge)/edge):0;
  const dt=Math.min(40,time-(splitLastTime||time));splitLastTime=time;
  const x=wrap.scrollLeft,y=wrap.scrollTop;
  wrap.scrollBy(speed(splitPointer.clientX,r.left,r.right)*dt*.8,speed(splitPointer.clientY,r.top,r.bottom)*dt*.8);
  if(wrap.scrollLeft!==x||wrap.scrollTop!==y)updateSplitDrawing();
  splitDragFrame=requestAnimationFrame(scrollSplitDrag);
}
function stopSplitDrag(){
  cancelAnimationFrame(splitDragFrame);splitDragFrame=0;splitLastTime=0;splitPointer=null;D.start=null;D.drawing=null;
}
$('splitCanvas').onpointerdown=e=>{
  if(!D.image||D.saving||e.button!==0||!e.isPrimary)return;
  e.preventDefault();D.start=splitPoint(e);D.drawing=null;splitPointer={clientX:e.clientX,clientY:e.clientY};
  $('splitCanvas').setPointerCapture(e.pointerId);splitDragFrame=requestAnimationFrame(scrollSplitDrag);
};
$('splitCanvas').onpointermove=e=>{if(!D.start)return;splitPointer={clientX:e.clientX,clientY:e.clientY};updateSplitDrawing();};
$('splitCanvas').onpointerup=e=>{
  if(!D.start)return;
  splitPointer={clientX:e.clientX,clientY:e.clientY};updateSplitDrawing();const box=D.drawing;stopSplitDrag();
  if($('splitCanvas').hasPointerCapture(e.pointerId))$('splitCanvas').releasePointerCapture(e.pointerId);
  if(box)addRegion(box);drawSplit();
};
$('splitCanvas').onpointercancel=()=>{stopSplitDrag();drawSplit();};
$('splitCanvas').onlostpointercapture=()=>{if(D.start){stopSplitDrag();drawSplit();}};
$('splitCanvasWrap').addEventListener('scroll',updateSplitDrawing,{passive:true});
function fitSplit(){
  if(!D.image)return;
  const wrap=$('splitCanvasWrap'),style=getComputedStyle(wrap);
  const w=wrap.clientWidth-parseFloat(style.paddingLeft)-parseFloat(style.paddingRight);
  const h=wrap.clientHeight-parseFloat(style.paddingTop)-parseFloat(style.paddingBottom);
  setSplitZoom(Math.min(100,100*h*D.image.naturalWidth/(D.image.naturalHeight*w)));
  D.fit=true;wrap.scrollTo(0,0);drawSplit();
}
$('splitFit').onclick=fitSplit;
$('splitZoomLabel').onclick=()=>{setSplitZoom(100);drawSplit();};
new ResizeObserver(()=>{if(D.fit&&$('splitDialog').open)fitSplit();}).observe($('splitCanvasWrap'));
$('splitRegions').onclick=async e=>{
  if(D.saving)return;
  const remove=e.target.closest('[data-region-delete]'),rotate=e.target.closest('[data-region-rotate]'),select=e.target.closest('[data-region-select]');
  if(remove){D.regions.splice(Number(remove.dataset.regionDelete),1);splitChanged();}
  if(rotate){const r=D.regions[Number(rotate.dataset.regionRotate)];r.rotation=(r.rotation+90)%360;splitChanged();}
  if(select){D.selected=Number(select.dataset.regionSelect);const r=D.regions[D.selected];if(r.page!==D.page)await splitPage(r.page);renderRegions();drawSplit();}
};
function quickSplit(vertical){
  if(!D.image||D.saving)return;
  if(D.regions.some(r=>r.page===D.page)){splitError('Usuń najpierw ramki z tej strony.');return;}
  if(D.regions.length>18){splitError('Możesz zaznaczyć maksymalnie 20 dokumentów.');return;}
  if(vertical){addRegion([0,0,1,.5]);addRegion([0,.5,1,1]);}else{addRegion([0,0,.5,1]);addRegion([.5,0,1,1]);}
}
$('splitHorizontal').onclick=()=>quickSplit(false);$('splitVertical').onclick=()=>quickSplit(true);$('splitWholePage').onclick=()=>addRegion([0,0,1,1]);
$('splitPrev').onclick=()=>splitPage(D.page-1);$('splitNext').onclick=()=>splitPage(D.page+1);
$('splitZoomIn').onclick=()=>{setSplitZoom(D.zoom+25);drawSplit();};$('splitZoomOut').onclick=()=>{setSplitZoom(D.zoom-25);drawSplit();};
$('splitReviewed').onchange=updateSplitSave;

$('splitOpen').onclick=async()=>{
  if(S.busy||readonly()||!guard())return;
  D.regions=[];D.selected=-1;D.saving=false;D.source={id:S.docId,fileId:S.state.files[0].id,version:S.state.document.updated_at};
  $('splitFields').disabled=false;$('splitClose').disabled=false;$('splitBusy').hidden=true;$('splitReviewed').checked=false;splitError();renderRegions();setSplitZoom(100);
  $('splitCanvas').hidden=true;$('splitLoadMessage').hidden=false;$('splitLoadMessage').textContent='Wczytywanie zdjęcia…';$('splitDialog').showModal();
  try{const meta=await api(`/api/documents/${D.source.id}/pages?file_id=${D.source.fileId}`);D.pages=meta.pages;await splitPage(1);fitSplit();}catch(error){splitError(error.message);$('splitLoadMessage').textContent=error.message;}
};
function closeSplit(){if(D.saving)return;if(D.regions.length&&!confirm('Zamknąć podział bez zapisywania zaznaczeń?'))return;stopSplitDrag();D.sequence++;D.image=null;D.regions=[];$('splitDialog').close();}
$('splitClose').onclick=closeSplit;$('splitDialog').oncancel=e=>{e.preventDefault();closeSplit();};
$('splitSave').onclick=async()=>{
  if(D.saving||D.regions.length<2||!$('splitReviewed').checked)return;
  D.saving=true;$('splitFields').disabled=true;$('splitClose').disabled=true;$('splitBusy').hidden=false;splitError();
  try{
    const regions=D.regions.map(({page,box,rotation})=>({page,box,rotation}));
    const result=await api(`/api/documents/${D.source.id}/split`,{method:'POST',body:JSON.stringify({version:D.source.version,file_id:D.source.fileId,regions})});
    stopSplitDrag();D.sequence++;D.image=null;D.regions=[];$('splitDialog').close();S.dirty=false;S.filter='pending';$('pendingTab').classList.add('selected');$('approvedTab').classList.remove('selected');
    await reloadList();await openDocument(result.children[0].id);toast(`Utworzono ${result.children.length} osobne dokumenty. Sprawdź dane każdego wycinka.`);
  }catch(error){splitError(error.message);if(!$('splitDialog').open)toast(error.message,true);}finally{D.saving=false;$('splitFields').disabled=false;$('splitClose').disabled=false;$('splitBusy').hidden=true;updateSplitSave();}
};
window.addEventListener('beforeunload',e=>{if($('splitDialog').open&&D.regions.length){e.preventDefault();e.returnValue='';}});
