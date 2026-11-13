'use strict';
// Presentation only: existing modules own all data, save actions and permissions.
(() => {
  const mobile=matchMedia('(max-width:820px)');
  const toggle=$('toggleDocuments'), sidebar=$('documentSidebar');
  const sync=()=>{
    const expanded=toggle.getAttribute('aria-expanded')==='true';
    toggle.setAttribute('aria-label',expanded?'Zamknij menu':'Otwórz menu');
    // Offscreen document controls must not remain in the keyboard tab sequence.
    sidebar.inert=mobile.matches&&!expanded;
  };
  const collapse=()=>{
    setDocuments(true);
    toggle.focus({preventScroll:true});
  };
  new MutationObserver(sync).observe(toggle,{attributes:true,attributeFilter:['aria-expanded']});
  let wantedDocument=null;
  new MutationObserver(()=>{
    if(mobile.matches&&wantedDocument!==null&&S.docId===wantedDocument){
      wantedDocument=null;collapse();
    }
  }).observe($('documentList'),{childList:true,subtree:true});
  sidebar.addEventListener('click',e=>{
    if(!mobile.matches||!(e.target instanceof Element))return;
    const section=e.target.closest('#clientSections button');
    if(section?.getAttribute('aria-current')==='page'){collapse();return;}
    const doc=e.target.closest('[data-doc]');
    if(doc){
      const id=Number(doc.dataset.doc);
      if(id===S.docId)collapse();else wantedDocument=id;
    }
  });
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape'&&mobile.matches&&toggle.getAttribute('aria-expanded')==='true'&&!document.querySelector('dialog[open]')){
      e.preventDefault();collapse();
    }
  });
  mobile.addEventListener('change',()=>{
    wantedDocument=null;
    if(mobile.matches)setDocuments(true);
    sync();
  });
  if(mobile.matches)setDocuments(true);
  sync();
})();
