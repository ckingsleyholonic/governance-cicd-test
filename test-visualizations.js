/* Render only the service relationships recorded for this test case. */
(() => {
  'use strict';
  const payload=document.getElementById('test-map-data');if(!payload)return;let data;
  try{data=JSON.parse(payload.textContent)}catch(_){return;}
  const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
  const short=x=>x.replace(/^ftgo-/,'').replace(/-service$/,'').replaceAll('-',' ');
  function draw(host,paths,services,empty){
    if(!host)return;
    if(!paths.length){host.append(el('p','small-note',empty));return;}
    const stage=el('div','test-service-stage'),row=el('div','test-service-row'),boxes=new Map(),inspector=el('div','test-inspector');
    inspector.setAttribute('aria-live','polite');inspector.append(el('span','','Select a method to inspect its recorded route.'));
    const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.classList.add('test-service-lines');svg.setAttribute('aria-hidden','true');stage.append(svg,row);
    services.forEach(service=>{
      const circle=el('div','test-service');circle.append(el('h3','',short(service)));
      const labels=new Set();for(const p of paths){if(p.from===service)labels.add(p.step);if(p.to===service)labels.add(p.handler);}
      for(const text of labels){const node=el('button','test-method');node.type='button';node.append(el('span','test-dot','●'),el('span','',text));node.addEventListener('click',()=>{const p=paths.find(x=>(x.from===service&&x.step===text)||(x.to===service&&x.handler===text));inspector.replaceChildren(el('strong','',text),el('p','',p.step+' → '+p.handler+' · '+p.kind.replaceAll('_',' ')));const href=p.from===service?p.source_href:p.target_href;if(href){const a=el('a','','View source ↗');a.href=href;a.target='_blank';a.rel='noopener noreferrer';inspector.append(a);}});circle.append(node);}row.append(circle);boxes.set(service,circle);
    });
    const routes=el('ul','test-routes');for(const p of paths){const li=el('li');li.append(el('strong','',p.step+' → '+p.handler),el('span','',` · ${short(p.from)} → ${short(p.to)} · ${p.kind.replaceAll('_',' ')}`));routes.append(li);}
    host.append(stage,routes,inspector);
    function lines(){
      const bounds=stage.getBoundingClientRect();if(!bounds.width||!bounds.height)return;
      svg.setAttribute('viewBox',`0 0 ${bounds.width} ${bounds.height}`);svg.replaceChildren();
      const defs=document.createElementNS(ns,'defs'),marker=document.createElementNS(ns,'marker'),arrow=document.createElementNS(ns,'path'),markerId='test-arrow-'+host.id;marker.setAttribute('id',markerId);marker.setAttribute('viewBox','0 0 10 10');marker.setAttribute('refX','9');marker.setAttribute('refY','5');marker.setAttribute('markerWidth','6');marker.setAttribute('markerHeight','6');marker.setAttribute('orient','auto');arrow.setAttribute('d','M 0 0 L 10 5 L 0 10 z');arrow.setAttribute('fill','#6b9a7d');marker.append(arrow);defs.append(marker);svg.append(defs);
      for(const p of paths){const a=boxes.get(p.from),b=boxes.get(p.to);if(!a||!b||a===b)continue;const ar=a.getBoundingClientRect(),br=b.getBoundingClientRect();const ax=ar.left+ar.width/2-bounds.left,ay=ar.top+ar.height/2-bounds.top,bx=br.left+br.width/2-bounds.left,by=br.top+br.height/2-bounds.top,dx=bx-ax,dy=by-ay,len=Math.hypot(dx,dy)||1;
        const x1=ax,y1=ar.top-bounds.top,x2=bx,y2=br.top-bounds.top;const line=document.createElementNS(ns,'path');line.setAttribute('d',`M ${x1} ${y1} C ${x1} ${y1-65}, ${x2} ${y2-65}, ${x2} ${y2}`);line.setAttribute('fill','none');line.setAttribute('stroke','#6b9a7d');line.setAttribute('stroke-width','3');line.setAttribute('marker-end','url(#'+markerId+')');const title=document.createElementNS(ns,'title');title.textContent=p.step+' → '+p.handler+' · '+p.kind;line.append(title);svg.append(line);}
    }
    if(window.ResizeObserver)new ResizeObserver(lines).observe(stage);window.addEventListener('resize',lines);requestAnimationFrame(lines);
  }
  draw(document.getElementById('test-ecosystem'),data.ecosystem_paths,data.ecosystem_services,'No service routes were recorded for this review.');
})();
