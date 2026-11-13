'use strict';
// Local layout analysis uses the same revision and permissions as manual splitting.
(() => {
  const attempted=new Set();
  const suggestions=new Map();
  let timer=null;
  window.rememberAutoSplit=(id,result)=>{
    if(result?.status==='review'&&result.regions?.length>1)suggestions.set(Number(id),result);
    else if(result?.failed&&result.message)toast(result.message,true);
  };
  const eligible=()=>S.user&&S.user.role!=='read_only'&&S.state&&!S.state.invoice&&!S.state.split&&
    ['received','to_book','needs_explanation','waiting_for_documents'].includes(S.state.document.status)&&S.state.files?.length===1&&S.state.files[0].file_role==='original';
  async function propose(id,result){
    if(S.docId!==id||S.dirty||S.busy||!eligible())return;
    if(result.version&&result.version!==S.state.document.updated_at)return;
    await $('splitOpen').onclick();
    if(!$('splitDialog').open||D.source?.id!==id||!D.image)return;
    for(const r of result.regions)addRegion(r.box);
    splitError('Proponowany podział. Sprawdź całe dokumenty, popraw ramki i potwierdź poniżej.');
  }
  async function automatic(force=false){
    if(S.busy||S.dirty||!eligible()||$('splitDialog').open)return;
    const id=S.docId,version=S.state.document.updated_at;
    const key=`${id}:${version}`;
    attempted.add(key);
    busy(true,'Wykrywanie i rozdzielanie dokumentów…');
    $('splitAuto').disabled=true;
    let proposal=null;
    try{
      const result=await api(`/api/documents/${id}/auto-split`,{method:'POST',body:JSON.stringify({version,force})});
      if(result.status==='split'){
        S.dirty=false;S.filter='pending';$('pendingTab').classList.add('selected');$('approvedTab').classList.remove('selected');
        await reloadList();
        if(result.children?.length)await openDocument(result.children[0].id);
        toast(`Rozdzielono na ${result.children.length} dokumenty. Sprawdź dane każdego z nich.`);
      }else if(result.status==='review')proposal=result;
      else if(force)toast(result.message||'Nie znaleziono pewnego podziału. Wybierz podział ręczny.');
    }catch(error){toast(error.message,true);}
    finally{busy(false);$('splitAuto').disabled=false;}
    if(proposal)await propose(id,proposal);
  }
  $('splitAuto').onclick=()=>automatic(true);
  function check(){
    if(!eligible()||S.busy||S.dirty||$('splitDialog').open)return;
    if(S.latestVersion&&S.latestVersion!==S.state.document.updated_at)return;
    const result=suggestions.get(S.docId);
    if(result){suggestions.delete(S.docId);attempted.add(`${S.docId}:${S.state.document.updated_at}`);propose(S.docId,result);return;}
    const needs=S.state.preparation?.needs_split||S.state.ocr?.raw_result?.needs_split;
    if(needs&&!attempted.has(`${S.docId}:${S.state.document.updated_at}`))automatic();
  }
  const schedule=()=>{clearTimeout(timer);timer=setTimeout(check,0);};
  new MutationObserver(schedule).observe($('busyOverlay'),{attributes:true,attributeFilter:['hidden']});
  new MutationObserver(schedule).observe($('aiPanel'),{childList:true,subtree:true});
  schedule();
})();
