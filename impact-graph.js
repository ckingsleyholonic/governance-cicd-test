/* Display only the relationships recorded in the PR evidence snapshot. */
(() => {
  'use strict';
  const host = document.getElementById('impact-graph');
  const payload = document.getElementById('impact-graph-data');
  if (!host || !payload) return;
  let data;
  try { data = JSON.parse(payload.textContent); }
  catch (_) { host.textContent = 'Impact graph data is unavailable.'; return; }
  const nodes = new Map(data.nodes.map(node => [node.id, node]));
  let selected = data.nodes[0]?.id || null;
  let expanded = false;
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const toolbar = el('div', 'ig-toolbar');
  const caption = data.no_law
    ? (data.verified_effects
      ? 'No approved specification was checked. This graph shows the verified downstream effects.'
      : 'No approved specification was checked and no downstream path was verified.')
    : 'Select a node to inspect its evidence and connections.';
  toolbar.append(el('span', 'ig-caption', caption));
  const toggle = el('button', 'ig-toggle', 'Show all nodes');
  toggle.type = 'button'; toolbar.append(toggle);
  const scroll = el('div', 'ig-scroll');
  const stage = el('div', 'ig-stage');
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg'); svg.classList.add('ig-edges');
  svg.setAttribute('aria-hidden', 'true'); stage.append(svg);
  const grid = el('div', 'ig-grid'); stage.append(grid); scroll.append(stage);
  if (data.no_law) grid.classList.add('ig-no-specifications');
  const details = el('div', 'ig-inspector'); details.setAttribute('aria-live', 'polite');
  host.replaceChildren(toolbar, scroll, details);
  const headings = {change: 'CHANGED CODE', downstream: 'DOWNSTREAM CODE', specification: 'SPECIFICATIONS'};
  const empty = {downstream: 'No downstream path was recorded.', specification: 'No specification association was recorded.'};

  function showDetails() {
    const node = nodes.get(selected);
    if (!node) { details.hidden = true; return; }
    details.hidden = false;
    details.replaceChildren(el('div', 'ig-inspector-kind', headings[node.kind]), el('h3', '', node.label), el('p', '', node.detail));
    if (node.result) details.append(el('p', 'ig-result', node.result));
    if (node.href) {
      const link = el('a', 'ig-link', node.kind === 'specification' ? 'View specification ↗' : node.label.startsWith('PR #') ? 'View pull request ↗' : 'View source ↗');
      link.href = node.href; if (/^https?:/.test(node.href)) { link.target = '_blank'; link.rel = 'noopener noreferrer'; }
      details.append(link);
    }
    const relations = data.edges.filter(edge => edge.from === node.id || edge.to === node.id);
    if (relations.length) {
      details.append(el('h4', '', 'Recorded connections'));
      const list = el('ul', 'ig-relations');
      for (const edge of relations) {
        const otherId = edge.from === node.id ? edge.to : edge.from;
        const other = nodes.get(otherId); if (!other) continue;
        const item = el('li'); const button = el('button', 'ig-related', other.label); button.type = 'button';
        button.addEventListener('click', () => { selected = otherId; if (!document.getElementById(otherId)) expanded = true; render(true); });
        item.append(button, el('span', '', ' · '+edge.label+' · '+edge.detail)); list.append(item);
      }
      details.append(list);
    }
  }
  function draw() {
    const bounds=stage.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return;
    svg.setAttribute('viewBox', `0 0 ${bounds.width} ${bounds.height}`);
    svg.setAttribute('width', bounds.width); svg.setAttribute('height', bounds.height);
    svg.replaceChildren();
    const defs=document.createElementNS(ns,'defs'),marker=document.createElementNS(ns,'marker'),triangle=document.createElementNS(ns,'path');
    marker.setAttribute('id','ig-arrow');marker.setAttribute('viewBox','0 0 10 10');marker.setAttribute('refX','9');marker.setAttribute('refY','5');marker.setAttribute('markerWidth','6');marker.setAttribute('markerHeight','6');marker.setAttribute('orient','auto');triangle.setAttribute('d','M 0 0 L 10 5 L 0 10 z');triangle.setAttribute('fill','#9caea6');marker.append(triangle);defs.append(marker);svg.append(defs);
    for (const edge of data.edges) {
      const from=document.getElementById(edge.from),to=document.getElementById(edge.to);
      if (!from || !to || from===to) continue;
      const a=from.getBoundingClientRect(),b=to.getBoundingClientRect();
      const same=nodes.get(edge.from).kind===nodes.get(edge.to).kind;
      const x1=(same?a.left:a.right)-bounds.left, y1=a.top+a.height/2-bounds.top;
      const x2=b.left-bounds.left, y2=b.top+b.height/2-bounds.top;
      const line=document.createElementNS(ns,'path');
      const longEdge=nodes.get(edge.from).kind==='change' && nodes.get(edge.to).kind==='specification';
      const bottom=bounds.height-12;
      line.setAttribute('d',same?`M ${x1} ${y1} C ${x1-25} ${y1}, ${x2-25} ${y2}, ${x2} ${y2}`:
        longEdge?`M ${x1} ${y1} L ${x1+25} ${y1} L ${x1+25} ${bottom} L ${x2-25} ${bottom} L ${x2-25} ${y2} L ${x2} ${y2}`:
        `M ${x1} ${y1} C ${x1+45} ${y1}, ${x2-45} ${y2}, ${x2} ${y2}`);
      line.setAttribute('class', 'ig-edge'+(edge.from===selected || edge.to===selected?' selected':''));
      line.setAttribute('marker-end','url(#ig-arrow)');
      const title=document.createElementNS(ns,'title');title.textContent=edge.label+': '+edge.detail;line.append(title);svg.append(line);
    }
  }
  function render(focusSelected=false) {
    grid.replaceChildren();
    let omitted=0;
    for (const kind of data.no_law ? ['change','downstream'] : ['change','downstream','specification']) {
      const column=el('div','ig-column');column.append(el('div','ig-column-title',headings[kind]));
      const all=data.nodes.filter(n=>n.kind===kind),shown=expanded?all:all.slice(0,7);omitted+=all.length-shown.length;
      const list=el('div','ig-nodes');
      for (const node of shown) {
        const button=el('button','ig-node ig-'+kind+(node.id===selected?' selected':''));
        button.type='button';button.id=node.id;button.setAttribute('aria-pressed',String(node.id===selected));
        button.append(el('strong','',node.label));
        if (node.result) button.append(el('small','',node.result));
        button.addEventListener('click',()=>{selected=node.id;render(true);});list.append(button);
      }
      if (!shown.length) list.append(el('p','ig-empty',empty[kind]||'No changed method was mapped.'));
      column.append(list);grid.append(column);
    }
    toggle.hidden=!expanded && omitted===0;toggle.textContent=expanded?'Show focused view':`Show all nodes (${omitted} more)`;
    showDetails();if (focusSelected) document.getElementById(selected)?.focus();requestAnimationFrame(draw);
  }
  toggle.addEventListener('click',()=>{expanded=!expanded;render();});
  if (typeof ResizeObserver!=='undefined') new ResizeObserver(draw).observe(stage);
  window.addEventListener('resize',draw);render();
})();
