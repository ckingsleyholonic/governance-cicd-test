/* A projection of verified baseline graphs and recorded PR evidence. */
(() => {
  'use strict';
  const embedded=new URLSearchParams(location.search).get('embed')==='1';
  if(embedded){document.documentElement.classList.add('ecosystem-embed');document.addEventListener('click',event=>{const a=event.target.closest('a');if(a)a.target='_top';},true);}
  const host=document.getElementById('ecosystem-map'), payload=document.getElementById('ecosystem-data');if(!host||!payload)return;
  let data;try{data=JSON.parse(payload.textContent)}catch(_){host.textContent='Ecosystem evidence is unavailable.';return;}
  const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
  const link=(text,href)=>{const a=el('a','eco-link',text);a.href=href;return a;};
  const short=s=>s.replace(/^ftgo-/,'').replace(/-service$/,'').replace(/-api-gateway$/,' gateway').replaceAll('-',' ');
  const label=s=>s.split(':')[0].split('.').slice(-2).join('.');
  const nodeMap=new Map(data.nodes.map(n=>[n.id,n]));
  const controls=el('div','eco-controls');
  function selector(title,items,empty){const wrap=el('label','eco-control',title),s=el('select');s.append(el('option','',empty));items.forEach(([value,text])=>{const o=el('option','',text);o.value=value;s.append(o);});wrap.append(s);controls.append(wrap);return s;}
  const wfSelect=selector('Workflow',data.workflows.map(w=>[w.id,w.name]),'All workflows');
  const prSelect=selector('Pull request',data.prs.map(p=>[String(p.number),'#'+p.number+' · '+p.title]),'Baseline');
  const query=new URLSearchParams(location.search);
  if(host.hasAttribute('data-per-pr') && data.prs.length===1){
    query.set('pr',String(data.prs[0].number));prSelect.parentElement.hidden=true;
  }
  if(embedded)prSelect.parentElement.hidden=true;
  if(query.has('pr')&&data.prs.some(p=>String(p.number)===query.get('pr'))){
    prSelect.value=query.get('pr');if(embedded)prSelect.parentElement.hidden=true;
    const pr=data.prs.find(p=>String(p.number)===prSelect.value),checked=pr.check&&data.workflows.find(w=>w.name===pr.check.workflow);
    const ids=[...new Set(pr.matches.map(m=>m.workflow))];if(checked)wfSelect.value=checked.id;else if(ids.length===1)wfSelect.value=ids[0];
  }
  const stats=el('p','eco-stats',`${data.services.length} services · ${data.nodes.length} recorded methods · ${data.graph_count} verified baseline graphs · ${data.workflows.length} workflows`);
  const scroll=el('div','eco-scroll');scroll.tabIndex=0;scroll.setAttribute('role','region');scroll.setAttribute('aria-label','Service ecosystem diagram');
  const stage=el('div','eco-stage');scroll.append(stage);
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('class','eco-lines');svg.setAttribute('viewBox','0 0 1160 650');svg.setAttribute('aria-hidden','true');stage.append(svg);
  const buttons=new Map(),circles=new Map(),positions=new Map(),serviceCenters=new Map();let selected=null;
  data.services.forEach(service=>{
    const circle=el('div','eco-service');
    circle.append(el('div','eco-service-label',short(service)),el('div','eco-service-sub',service));stage.append(circle);circles.set(service,circle);
    const role=el('span','eco-effect-role');role.hidden=true;circle.append(role);
    const methods=data.nodes.filter(n=>n.service===service).sort((a,b)=>a.method.localeCompare(b.method)||a.file.localeCompare(b.file));
    if(!methods.length)circle.append(el('span','eco-no-methods','No mapped methods'));
    methods.forEach((n,i)=>{
      const radius=88*Math.sqrt((i+.5)/methods.length),angle=i*2.3999632297;
      const px=115+Math.cos(angle)*radius,py=137+Math.sin(angle)*radius;
      const dot=el('button','eco-dot');dot.type='button';dot.style.left=(px-5)+'px';dot.style.top=(py-5)+'px';dot.title=label(n.method);dot.setAttribute('aria-label',short(service)+': '+label(n.method));dot.addEventListener('click',()=>{selected=n.id;render();});circle.append(dot);buttons.set(n.id,dot);
    });
  });
  const legend=el('div','eco-legend');legend.append(el('span','eco-legend-workflow','◯ Participating service'),el('span','','● Recorded method'),el('span','eco-legend-saga','● Saga / participant'),el('span','eco-legend-witness','● Workflow witness'),el('span','eco-legend-workflow','● PR workflow association'),el('span','eco-legend-changed','● Changed method'),el('span','eco-legend-potential','– – Possible method reference'),el('span','eco-legend-saga','— Recorded saga route'),el('span','eco-legend-workflow','– – Workflow evidence path'),el('span','eco-legend-path','– – PR evidence path'));
  const effectLegend=el('span','eco-legend-effect','→ Downstream effect');effectLegend.hidden=true;legend.append(effectLegend);
  const inspector=el('div','eco-inspector');inspector.setAttribute('aria-live','polite');
  function effectPath(effect) {
    if (effect.origin === effect.target) return;
    const from=serviceCenters.get(effect.origin),to=serviceCenters.get(effect.target);
    if (!from || !to) return;
    const dx=to[0]-from[0],dy=to[1]-from[1],length=Math.hypot(dx,dy);
    if (length<=235) return;
    const a=[from[0]+117*dx/length,from[1]+117*dy/length];
    const b=[to[0]-117*dx/length,to[1]-117*dy/length];
    const line=document.createElementNS(svg.namespaceURI,'path');
    const sameRowWithServiceBetween=dy===0&&Math.abs(dx)>520;
    line.setAttribute('d',sameRowWithServiceBetween
      ? `M ${from[0]} ${from[1]-117} C ${from[0]} 0, ${to[0]} 0, ${to[0]} ${to[1]-117}`
      : `M ${a[0]} ${a[1]} L ${b[0]} ${b[1]}`);
    line.setAttribute('class','eco-effect-edge'+(effect.removed?' eco-effect-removed':''));
    line.setAttribute('marker-end','url(#eco-effect-arrow)');
    const title=document.createElementNS(svg.namespaceURI,'title');
    title.textContent=effect.detail;line.append(title);svg.append(line);
  }
  function path(a,b,cls,title){const p=positions.get(a),q=positions.get(b);if(!p||!q)return;const line=document.createElementNS(svg.namespaceURI,'path');const dx=q[0]-p[0];line.setAttribute('d',Math.abs(dx)>260?`M ${p[0]} ${p[1]} C ${p[0]} ${p[1]-200}, ${q[0]} ${q[1]-200}, ${q[0]} ${q[1]}`:`M ${p[0]} ${p[1]} C ${p[0]+dx*.4} ${p[1]}, ${q[0]-dx*.4} ${q[1]}, ${q[0]} ${q[1]}`);line.setAttribute('class',cls);line.setAttribute('fill','none');line.setAttribute('stroke',cls.includes('eco-pr-path')?'#d1882a':cls.includes('eco-saga-route')?'#375bb0':cls.includes('eco-workflow-path')?'#6786bc':'#7ba68c');line.setAttribute('marker-end',cls.includes('eco-pr-path')?'url(#eco-pr-arrow)':cls.includes('eco-saga-route')?'url(#eco-saga-arrow)':cls.includes('eco-workflow-path')?'url(#eco-wf-arrow)':'url(#eco-arrow)');const t=document.createElementNS(svg.namespaceURI,'title');t.textContent=title;line.append(t);svg.append(line);}
  function render(){
    const w=data.workflows.find(w=>w.id===wfSelect.value),pr=data.prs.find(p=>String(p.number)===prSelect.value),related=new Set(w?.nodes||[]),witnesses=new Set(w?.witnesses.map(x=>x.node)||[]),changed=new Set(pr?.changed||[]);
    const effects=pr?.effects||[],origins=new Set(effects.map(e=>e.origin)),targets=new Set(effects.map(e=>e.target));
    effectLegend.hidden=!effects.length;
    const sagas=data.sagas.filter(s=>w?.sagas.includes(s.node)),sagaNodes=new Set(sagas.flatMap(s=>[s.node,...s.steps.flatMap(step=>[...step.builders,...step.handlers])]));
    circles.forEach((c,s)=>{
      const source=origins.has(s),target=targets.has(s),role=c.querySelector('.eco-effect-role');
      c.classList.toggle('eco-participant',!!w&&w.services.includes(s));
      c.classList.toggle('eco-effect-origin',source);c.classList.toggle('eco-effect-target',target);
      role.textContent=target?(source?'Changed and affected':'Downstream effect'):'Source of change';
      role.hidden=!(source||target);
    });
    buttons.forEach((b,id)=>{b.classList.toggle('eco-associated',related.has(id));b.classList.toggle('eco-witness',witnesses.has(id));b.classList.toggle('eco-saga',sagaNodes.has(id));b.classList.toggle('eco-changed',changed.has(id));b.classList.toggle('eco-selected',id===selected);});
    svg.replaceChildren();
    const defs=document.createElementNS(svg.namespaceURI,'defs');for(const [id,color] of [['eco-arrow','#7ba68c'],['eco-pr-arrow','#d1882a'],['eco-wf-arrow','#6786bc'],['eco-saga-arrow','#375bb0'],['eco-effect-arrow','#9d377e']]){const m=document.createElementNS(svg.namespaceURI,'marker'),t=document.createElementNS(svg.namespaceURI,'path');m.setAttribute('id',id);m.setAttribute('viewBox','0 0 10 10');m.setAttribute('refX','9');m.setAttribute('refY','5');m.setAttribute('markerWidth','5');m.setAttribute('markerHeight','5');m.setAttribute('orient','auto');t.setAttribute('d','M 0 0 L 10 5 L 0 10 z');t.setAttribute('fill',color);m.append(t);defs.append(m);}svg.append(defs);
    for(const edge of data.edges){const a=nodeMap.get(edge.from),b=nodeMap.get(edge.to);if(!a||!b)continue;const visible=selected?(edge.from===selected||edge.to===selected):w?((related.has(edge.from)||witnesses.has(edge.from))&&(related.has(edge.to)||witnesses.has(edge.to))):(!pr&&a.service!==b.service);if(visible)path(edge.from,edge.to,'eco-edge'+(edge.kinds.includes('METHOD_REFERENCE')||edge.kinds.includes('POTENTIAL_EVENT_TYPE_MATCH')?' eco-potential':''),edge.kinds.join(' → '));}
    for(const saga of sagas)for(const step of saga.steps)for(const builder of step.builders){path(saga.node,builder,'eco-edge eco-saga-route','Saga declaration · '+step.kind+' · '+step.participant);for(const handler of step.handlers)path(builder,handler,'eco-edge eco-saga-route','Saga step builder and handler route · '+step.participant);}
    if(w)for(const p of w.paths)path(p.from,p.to,'eco-edge eco-workflow-path',p.kinds.join(' → ')+' · '+p.obligation+' · slice '+p.slice);
    if(pr)for(const p of pr.paths)path(p.from,p.to,'eco-edge eco-pr-path',p.kinds.join(' → ')+' · slice '+p.slice);
    for(const effect of new Map(effects.map(e=>[e.origin+'→'+e.target,e])).values())effectPath(effect);
    inspector.replaceChildren();
    if(effects.length){
      const panel=el('section','eco-effects'),list=el('ul','eco-effect-list');
      panel.append(el('h3','','Downstream effects'));
      for(const effect of effects){
        const item=el('li');
        item.append(el('strong','',effect.title),el('p','',effect.detail),
          link('Changed source ↗',effect.origin_url),
          link(effect.removed?'Baseline consumer ↗':'Consumer source ↗',effect.target_url));
        list.append(item);
      }
      panel.append(list);inspector.append(panel);
    }
    if(selected){const n=nodeMap.get(selected);inspector.append(el('div','eco-kicker','RECORDED METHOD'),el('h3','',label(n.method)),el('p','',n.method),link('View baseline source ↗',n.href));const links=data.edges.filter(e=>e.from===selected||e.to===selected);inspector.append(el('p','eco-note',`${links.length} recorded method connections in the saved graphs (including possible references where labeled).`));if(w){const ws=w.witnesses.filter(x=>x.node===selected);if(ws.length){inspector.append(el('h4','','Workflow specification evidence'));const list=el('ul','eco-witness-list');for(const row of ws){const item=el('li');item.append(link(row.obligation,row.href),document.createTextNode(' · '),link('source witness ↗',row.source),el('span','eco-witness-meta',row.hpg?' · mapped to saved HPG':' · source witness; HPG link unavailable'));list.append(item)}inspector.append(list);}}if(links.length){const list=el('ul','eco-connections');for(const e of links.slice(0,20)){const other=nodeMap.get(e.from===selected?e.to:e.from),li=el('li'),b=el('button','eco-related',label(other.method));b.type='button';b.addEventListener('click',()=>{selected=other.id;render()});li.append(b,el('span','', ' · '+e.kinds.join(' / ')));list.append(li)}inspector.append(list);}}
    else if(w){
      const unique=[...new Set(w.witnesses.map(x=>x.node))];
      inspector.append(el('div','eco-kicker','WORKFLOW'),el('h3','',w.name),el('p','',`${w.services.length} participating services · ${unique.length} source-anchored workflow witness methods · ${w.nodes.length} methods associated through recorded PR analysis.`),link('View workflow specification ↗',w.href));
      if(unique.length){const heading=el('h4','','Workflow witness methods');inspector.append(heading);const list=el('ul','eco-witness-list');for(const id of unique){const node=nodeMap.get(id),rows=w.witnesses.filter(x=>x.node===id),item=el('li'),button=el('button','eco-related',label(node.method));button.type='button';button.addEventListener('click',()=>{selected=id;render()});item.append(button,el('span','eco-witness-meta',' · '+short(node.service)+' · '+(rows.some(x=>x.hpg)?'also in saved HPG':'source witness; HPG link unavailable')));const obligations=[...new Map(rows.map(x=>[x.obligation,x])).values()];for(const row of obligations){item.append(document.createTextNode(' · '),link(row.obligation,row.href));}list.append(item);}inspector.append(list);}
      else inspector.append(el('p','eco-note','No source-anchored witness method is available for this workflow in the saved promotion record.'));
    }
    else inspector.append(el('div','eco-kicker','EXPLORE'),el('h3','','Select a method or workflow'),el('p','','Select a dot to see its direct method connections and source. Choose a workflow to highlight its participating services and recorded method associations.'));
    if(w&&sagas.length){const section=el('div','eco-saga-details');section.append(el('div','eco-kicker','SAGA RELATIONSHIPS · BASELINE HPG'));for(const saga of sagas){section.append(el('h3','',saga.name),el('p','eco-note','Recorded declarations in source order, with links to their command builders and handlers.'));const list=el('ol','eco-saga-list');for(const step of saga.steps){const item=el('li',step.kind==='withCompensation'?'eco-comp-step':'');const title=step.participant.replace('this::','');item.append(el('strong','',step.kind==='withCompensation'?'Compensate: '+title:title),document.createTextNode(' · '),link('saga declaration ↗',step.source));for(const id of step.builders){const node=nodeMap.get(id);if(!node)continue;const btn=el('button','eco-related',label(node.method));btn.type='button';btn.addEventListener('click',()=>{selected=id;render()});item.append(document.createTextNode(' · builder '),btn);}for(const id of step.handlers){const node=nodeMap.get(id);if(!node)continue;const btn=el('button','eco-related',label(node.method));btn.type='button';btn.addEventListener('click',()=>{selected=id;render()});item.append(document.createTextNode(' → handler '),btn);}if(!step.handlers.length)item.append(el('span','eco-witness-meta',' · no handler mapped'));if(pr?.check?.obligation==='cancel.compensate_ticket'&&pr.check.verdict==='violation'&&step.participant==='this::undoBeginCancelTicket'&&step.builders.some(id=>changed.has(id)))item.append(el('span','eco-saga-violation',' · PR check: violation'));list.append(item);}section.append(list);}inspector.append(section);}
    if(pr){const block=el('div','eco-pr-summary');block.append(el('h4','','PR #'+pr.number+' · '+pr.title),el('p','',`${pr.changed.length} changed methods and ${pr.paths.length} recorded paths are mapped to the displayed services.`));if(pr.check){block.append(el('p','',`Rule check: ${pr.check.obligation} · ${pr.check.verdict}`),link('View checked specification ↗',pr.check.href));}if(!host.hasAttribute('data-per-pr'))block.append(link('Open PR analysis ↗','pr-'+pr.number+'.html'));inspector.append(block);}
  }
  wfSelect.addEventListener('change',()=>{selected=null;render()});prSelect.addEventListener('change',()=>{selected=null;render()});
  host.replaceChildren(controls,stats,scroll,legend,inspector);
  function layout(){
    const width=scroll.clientWidth;
    if(!width)return;
    const columns=Math.max(1,Math.min(4,Math.floor(width/260)));
    const cell=width/columns,height=Math.ceil(data.services.length/columns)*320;
    stage.style.width=width+'px';stage.style.height=height+'px';
    svg.style.width=width+'px';svg.style.height=height+'px';svg.setAttribute('viewBox',`0 0 ${width} ${height}`);
    data.services.forEach((service,index)=>{
      const circle=circles.get(service),x=(index%columns)*cell+(cell-circle.offsetWidth)/2,y=25+Math.floor(index/columns)*320;
      circle.style.left=x+'px';circle.style.top=y+'px';
      serviceCenters.set(service,[x+circle.offsetWidth/2,y+circle.offsetHeight/2]);
    });
    buttons.forEach((button,id)=>{
      const circle=circles.get(nodeMap.get(id).service);
      positions.set(id,[parseFloat(circle.style.left)+parseFloat(button.style.left)+5,
                        parseFloat(circle.style.top)+parseFloat(button.style.top)+5]);
    });
    render();
  }
  new ResizeObserver(layout).observe(scroll);
  layout();
})();
