/* Tabs preserve each independent analysis visualization. */
(() => {
  'use strict';
  document.querySelectorAll('[data-analysis-tabs]').forEach(root=>{
    const tabs=[...root.querySelectorAll('[role="tab"]')];
    function activate(index,focus=false){tabs.forEach((tab,i)=>{const active=i===index;tab.setAttribute('aria-selected',String(active));tab.tabIndex=active?0:-1;document.getElementById(tab.getAttribute('aria-controls')).hidden=!active;});if(focus)tabs[index].focus();window.dispatchEvent(new Event('resize'));}
    tabs.forEach((tab,i)=>{tab.addEventListener('click',()=>activate(i));tab.addEventListener('keydown',event=>{let n;if(event.key==='ArrowRight')n=(i+1)%tabs.length;else if(event.key==='ArrowLeft')n=(i+tabs.length-1)%tabs.length;else if(event.key==='Home')n=0;else if(event.key==='End')n=tabs.length-1;else return;event.preventDefault();activate(n,true);});});
    const frame=root.querySelector('iframe');if(frame)frame.addEventListener('load',()=>{try{const body=frame.contentDocument.body;const resize=()=>{frame.style.height=Math.max(950,body.scrollHeight)+'px';};resize();if(window.ResizeObserver)new ResizeObserver(resize).observe(body);}catch(_){}});
    const preferred=root.dataset.defaultTab;const initial=preferred?tabs.findIndex(tab=>tab.id==='tab-'+preferred):-1;
    activate(initial>=0?initial:0);
  });
})();
