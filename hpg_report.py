#!/usr/bin/env python3
"""Render a self-contained PR analysis from an identity-checked HPG review result."""
import hashlib
import html
import json
import re
from pathlib import Path
from urllib.parse import quote

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]


def asset(name):
    packaged=HERE/name
    if packaged.is_file():return packaged.read_text(encoding='utf-8')
    folder='ftgo' if name=='test-visualizations.js' else 'will-prs'
    return (ROOT/'reports'/folder/name).read_text(encoding='utf-8')


def safe(value):
    return html.escape(str(value if value is not None else ''),quote=True)


def source_url(repo,revision,path,line):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo or '') or not re.fullmatch(r'[0-9a-f]{40}',revision or ''):
        return None
    if not isinstance(path,str) or not path or path.startswith('/') or any(x in ('','.','..') for x in path.split('/')):
        return None
    url='https://github.com/'+repo+'/blob/'+revision+'/'+quote(path,safe='/')
    if type(line) is int and line>0:url+='#L'+str(line)
    return url


def _step_key(step):
    if not isinstance(step,dict) or not isinstance(step.get('kind'),str) or not isinstance(step.get('command'),str):return None
    channel=step.get('channel') if isinstance(step.get('channel'),str) else None
    return step['kind'],step['command'],channel


def _service(path):
    if not isinstance(path,str) or not path:return ''
    module=path.split('/')[0]
    return module if re.fullmatch(r'[A-Za-z0-9_.-]+-service',module) else ''


def _project_saga(findings):
    """Compare only the supplied saga steps and explicit handler routes."""
    paths=[];services=set();baseline_paths=[];baseline_services=set();before=[];after=[];available=False
    for finding in findings:
        if not isinstance(finding,dict):continue
        saga=finding.get('saga_steps') if isinstance(finding.get('saga_steps'),dict) else {}
        bs=saga.get('base');hs=saga.get('head')
        if not isinstance(bs,list) or not isinstance(hs,list):continue
        available=True
        base={_step_key(s):s for s in bs if _step_key(s)};head={_step_key(s):s for s in hs if _step_key(s)}
        changed=set(base)^set(head);common=sorted(set(base)&set(head),key=repr)
        for i,a in enumerate(common):
            for b in common[i+1:]:
                positions=(base[a].get('index'),base[b].get('index'),head[a].get('index'),head[b].get('index'))
                if all(type(x) is int for x in positions) and (positions[0]-positions[1])*(positions[2]-positions[3])<0:changed.update((a,b))
            old={r.get('method'):r.get('source_sha256') for r in base[a].get('routes',[]) if isinstance(r,dict) and r.get('method')}
            new={r.get('method'):r.get('source_sha256') for r in head[a].get('routes',[]) if isinstance(r,dict) and r.get('method')}
            if any(old[k] and new[k] and old[k]!=new[k] for k in old.keys()&new.keys()):changed.add(a)
        before.append(' → '.join(s['command'].rsplit('.',1)[-1] for s in sorted(base.values(),key=lambda x:x.get('index') if type(x.get('index')) is int else 999999)))
        after.append(' → '.join(s['command'].rsplit('.',1)[-1] for s in sorted(head.values(),key=lambda x:x.get('index') if type(x.get('index')) is int else 999999)))
        def add_routes(step,out,group,version):
            source=_service(step.get('file'))
            for route in step.get('routes',[]):
                if not isinstance(route,dict):continue
                target=_service(route.get('file'));method=route.get('method')
                if not source or not target or not isinstance(method,str) or not method:continue
                group.update((source,target))
                row={'from':source,'to':target,'step':step['command'].rsplit('.',1)[-1],
                     'handler':'.'.join(method.split(':',1)[0].split('.')[-2:]),
                     'kind':route.get('status') if isinstance(route.get('status'),str) else 'recorded relationship',
                     'source_file':step.get('file'),'source_line':step.get('line'),
                     'target_file':route.get('file'),'target_line':route.get('line'),'version':version}
                if row not in out:out.append(row)
        for step in base.values():add_routes(step,baseline_paths,baseline_services,'base')
        for key in sorted(changed,key=repr):add_routes(base.get(key) or head[key],paths,services,'base' if key in base else 'head')
    return {'services':sorted(services),'paths':paths,'ecosystem_services':sorted(baseline_services),
            'ecosystem_paths':baseline_paths,'evidence_available':available,'before':before,'after':after}


def render(result,repo,number,title=None):
    if not isinstance(result,dict) or result.get('verdict') not in ('pass','violation','unknown') or not isinstance(result.get('findings'),list):
        raise ValueError('Invalid HPG review result')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo or '') or type(number) is not int or number<=0:
        raise ValueError('Invalid pull request identity')
    base=result.get('base_revision');head=result.get('head_revision')
    if not all(isinstance(x,str) and re.fullmatch(r'[0-9a-f]{40}',x) for x in (base,head)):
        raise ValueError('Invalid review revisions')
    findings=result['findings'];nodes=[];by_id={};edges=[];sections=[];saga=_project_saga(findings)
    for path in saga['paths']+saga['ecosystem_paths']:
        revision=base if path['version']=='base' else head
        path['source_href']=source_url(repo,revision,path['source_file'],path['source_line'])
        path['target_href']=source_url(repo,revision,path['target_file'],path['target_line'])
    report_title=str(title or 'Change analysis')[:300]
    def node(kind,key,label,detail,href=None):
        ident=kind+'-'+hashlib.sha256(key.encode()).hexdigest()[:16]
        if ident not in by_id:
            item={'id':ident,'kind':kind,'label':label,'detail':detail,'href':href};nodes.append(item);by_id[ident]=item
        return ident
    root=node('change','pr:'+str(number),'PR #'+str(number),'Reviewed change',f'https://github.com/{repo}/pull/{number}')
    for f in findings:
        if not isinstance(f,dict) or f.get('verdict') not in ('pass','violation','unknown'):raise ValueError('Invalid review finding')
        ident=str(f.get('binding_id') or '');title=str(f.get('intent_handle') or 'Workflow rule')
        reason=str(f.get('reason') or 'No explanation supplied.')
        source=f.get('source') if isinstance(f.get('source'),dict) else {};path=source.get('file');method=source.get('method')
        rule=node('specification','rule:'+ident,title,reason)
        by_id[rule]['result']='Baseline: '+str(f.get('baseline_verdict') or 'not reported')+' · PR head: '+f['verdict']
        edges.append({'from':root,'to':rule,'label':'rule check','detail':reason})
        if path:
            href=source_url(repo,head,path,source.get('line'));label=str(method or path).split(':',1)[0].split('.')[-1]
            loc=node('downstream','source:'+str(path)+'\n'+str(method),label,str(method or path),href)
            edges.append({'from':rule,'to':loc,'label':'review evidence','detail':'Method reported by the review'})
        downstream=f.get('downstream') if isinstance(f.get('downstream'),dict) else None
        if downstream and downstream.get('file'):
            file=str(downstream['file']);href=source_url(repo,head,file,downstream.get('line'))
            item=node('downstream','downstream:'+file,str(downstream.get('method') or file),str(downstream.get('status') or 'reported by review'),href)
            edges.append({'from':rule,'to':item,'label':'reported downstream','detail':str(downstream.get('status') or 'reported by review')})
        sections.append(f'<section class="card"><div class="eyebrow">WORKFLOW RULE CHECK</div><h2>{safe(title)}</h2><p>{safe(reason)}</p><div class="compare-grid"><div class="compare-panel before"><div class="compare-heading">BEFORE <span>baseline</span></div><p>Result: <b>{safe(f.get("baseline_verdict") or "not reported")}</b></p></div><div class="compare-panel after"><div class="compare-heading">AFTER <span>PR head</span></div><p>Result: <b>{safe(f["verdict"])}</b></p></div></div></section>')
    for path in saga['paths']:
        step=node('downstream','saga:'+path['from']+path['step'],path['step'],'Recorded saga participant')
        handler=node('downstream','handler:'+path['to']+path['handler'],path['handler'],path['to'])
        edges.append({'from':root,'to':step,'label':'related workflow step','detail':'Related to a recorded step or handler difference'})
        edges.append({'from':step,'to':handler,'label':'recorded route','detail':path['kind']})
    description_before='; '.join(filter(None,saga['before'])) or 'No workflow steps recorded.'
    description_after='; '.join(filter(None,saga['after'])) or 'No workflow steps recorded.'
    map_payload=json.dumps(saga,ensure_ascii=True,separators=(',',':')).replace('&','\\u0026').replace('<','\\u003c').replace('>','\\u003e')
    payload=json.dumps({'nodes':nodes,'edges':edges},ensure_ascii=True,separators=(',',':')).replace('&','\\u0026').replace('<','\\u003c').replace('>','\\u003e')
    styles=asset('style.css');impact=asset('impact-graph.js');tabs=asset('analysis-tabs.js');visuals=asset('test-visualizations.js')
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>PR #{number} · Change analysis</title><style>{styles}</style></head><body><header class="top"><a class="brand" href="https://github.com/{safe(repo)}/pull/{number}"><span class="mark">✳</span> Holonic <span class="muted">/ Change analysis</span></a></header><main><div class="heading"><div><div class="eyebrow">PULL REQUEST #{number}</div><h1>{safe(report_title)}</h1><p class="lead"><a href="https://github.com/{safe(repo)}/pull/{number}">Open the original pull request ↗</a></p></div></div><div class="revision"><span>BRANCH POINT <code>{base[:12]}</code></span><span class="arrow">→</span><span>PR HEAD <code>{head[:12]}</code></span></div><div class="primary"><section class="card"><div class="eyebrow">WHAT CHANGED</div><h2>Before and after</h2><div class="compare-grid"><div class="compare-panel before"><div class="compare-heading">BEFORE <span>recorded sequences</span></div><p>{safe(description_before)}</p></div><div class="compare-panel after"><div class="compare-heading">AFTER <span>recorded sequences</span></div><p>{safe(description_after)}</p></div></div></section><section class="analysis-tabs" data-analysis-tabs><div class="analysis-tab-list" role="tablist" aria-label="Analysis visualizations"><button type="button" role="tab" id="tab-impact" aria-controls="panel-impact">Impact graph</button><button type="button" role="tab" id="tab-ecosystem" aria-controls="panel-ecosystem">Service ecosystem</button></div><div id="panel-impact" role="tabpanel" aria-labelledby="tab-impact"><section class="card impact-graph-card"><h2>Impact graph</h2><div id="impact-graph"></div></section></div><div id="panel-ecosystem" role="tabpanel" aria-labelledby="tab-ecosystem"><section class="card"><h2>Service ecosystem</h2><p>Services and routes supplied by the review.</p><div id="test-ecosystem"></div></section></div></section>{''.join(sections)}</div><footer>Holonic · Pull request analysis</footer></main><script type="application/json" id="impact-graph-data">{payload}</script><script type="application/json" id="test-map-data">{map_payload}</script><script>{visuals}</script><script>{impact}</script><script>{tabs}</script></body></html>'''
