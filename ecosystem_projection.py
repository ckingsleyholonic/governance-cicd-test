"""Shared baseline graph projection for snapshot and per-PR reports."""
import hashlib


def ecosystem_data(doc, spec, graphs, manifest, source_link, spec_link, workflow_link):
    """Project verified baseline method graphs and recorded PR paths for display."""
    services=sorted({s for w in spec['workflows'] for s in w.get('participating_services',[])})
    nodes={};edges={};sagas={};graph_count=0
    def add(path,method,line=None,end=None,hpg=False):
        service=path.split('/')[0]
        if service not in services or not method:return None
        key=path+'\n'+method;ident=hashlib.sha256(key.encode()).hexdigest()[:20]
        if ident not in nodes:
            nodes[ident]={'id':ident,'service':service,'method':method,'file':path,'line':line,
                          'end':end,'href':source_link(doc['pr_base'],path,line),'hpg':hpg}
        else:
            nodes[ident]['hpg'] = nodes[ident]['hpg'] or hpg
            if type(line) is int and type(end) is int and end>=line:
                nodes[ident]['line']=line;nodes[ident]['end']=end
        return ident
    for graph in graphs:
        graph_count+=1;ids={}
        for node in graph['nodes']:
            if node.get('kind')!='method':continue
            v=node['data'];path=v.get('file') or v.get('filename') or ''
            ident=add(path,v.get('full_name'),v.get('line'),v.get('end'),True)
            if ident:ids[node['id']]=ident
        for row in graph['edges']:
            kind=row['kind'];a=ids.get(row['source']);b=ids.get(row['target'])
            if a and b and kind in ('METHOD_CALL','FRAMEWORK_METHOD_CALL','FRAMEWORK_CALLBACK','METHOD_REFERENCE','POTENTIAL_EVENT_TYPE_MATCH'):
                key=(a,b);edges.setdefault(key,set()).add(kind)
        graph_nodes={node['id']:node for node in graph['nodes']}
        for row in graph['edges']:
            if row['kind']!='DECLARES_STEP':continue
            saga=graph_nodes.get(row['source'],{});step=graph_nodes.get(row['target'],{})
            if saga.get('kind')!='saga' or step.get('kind')!='saga_step':continue
            definition=saga['data'].get('definition') or {};v=step['data']
            origin=add(definition.get('file',''),definition.get('full_name'),definition.get('start'),definition.get('end'),True)
            if not origin:continue
            item=sagas.setdefault(origin,{'node':origin,'name':definition.get('full_name','').split(':',1)[0].rsplit('.',2)[-2], 'steps':{}})
            outgoing=[e for e in graph['edges'] if e['source']==step['id']]
            builders=sorted({ids[e['target']] for e in outgoing if e['kind']=='BUILDS_COMMAND' and e['target'] in ids})
            handlers=sorted({ids[e['target']] for e in outgoing if e['kind']=='ROUTES_TO_HANDLER' and e['target'] in ids})
            if type(v.get('line')) is not int:continue
            key=(v['line'],v.get('kind',''),v.get('endpoint_expression',''))
            current=item['steps'].setdefault(key,{'line':v['line'],'kind':v.get('kind',''),
                'participant':v.get('endpoint_expression',''), 'source':source_link(doc['pr_base'],v['file'],v['line']),
                'builders':set(),'handlers':set()})
            current['builders'].update(builders);current['handlers'].update(handlers)
    workflows=[{'id':w['id'],'name':w['name'],'services':w.get('participating_services',[]),
                'href':workflow_link(w['id']),'nodes':[],'paths':[],'witnesses':[]} for w in spec['workflows']]
    byname={w['name']:w for w in workflows};prs=[]
    for case in doc['cases']:
        changed=[];paths=[];matches=[]
        for m in case['changed_methods']:
            ident=add(m['file'],m['method'],m.get('line'),m.get('end'),True)
            if ident:changed.append(ident)
        for d in case['downstream']:
            a=add(d['from_file'],d['from_method'],d.get('from_line'));b=add(d['file'],d['method'],d.get('line'))
            if a and b:paths.append({'from':a,'to':b,'kinds':d['path_kinds'],'slice':d['slice_id']})
        for row in case['workflows']:
            workflow=byname.get(row['workflow']);a=add(row['from_file'],row['from_method'])
            if workflow and a:
                workflow['nodes'].append(a);matches.append({'workflow':workflow['id'],'node':a,'obligation':row['obligation'],'href':spec_link(row['obligation'])})
                witnesses=sorted((n for n in nodes.values() if n['file']==row['file'] and type(n.get('line')) is int and type(n.get('end')) is int and n['line']<=row['line']<=n['end']),key=lambda n:(n['end']-n['line'],n['id']))
                if witnesses:
                    witness=witnesses[0]['id'];workflow['nodes'].append(witness)
                    path={'from':a,'to':witness,'kinds':row['path_kinds'],'obligation':row['obligation'],'slice':row['slice_id']}
                    if a!=witness and path not in workflow['paths']:workflow['paths'].append(path)
        check=case.get('rule_check')
        prs.append({'number':case['number'],'title':case['title'],'changed':sorted(set(changed)),'paths':paths,'matches':matches,
                    'check':None if not check else {'workflow':check['workflow'],'obligation':check['obligation'],'verdict':check['head_verdict'],'href':spec_link(check['obligation'])}})
    obligations={o['id']:(w['id'],o) for w in spec['workflows'] for o in w['obligations']}
    byid={w['id']:w for w in workflows}
    for mapping in manifest['mappings'] if manifest else ():
        oid=mapping['obligation_id'];entry=obligations.get(oid)
        if not entry or mapping['predicate']!=entry[1]['proof']:raise ValueError('Workflow witness does not match specification')
        workflow=byid[entry[0]]
        for witness in mapping['proof']['witness_ledger']:
            source=witness['source'];owner=source.get('owner','');path=source.get('path','');line=source.get('line')
            if source.get('anchor_status')!='exact_line' or type(line) is not int or '#' not in owner or '/' not in owner:continue
            cls,method_arity=owner.rsplit('#',1);method=method_arity.rsplit('/',1)[0];prefix=cls+'.'+method+':'
            found=[n for n in nodes.values() if n['file']==path and n.get('hpg') and n['method'].startswith(prefix) and type(n.get('line')) is int and type(n.get('end')) is int and n['line']<=line<=n['end']]
            # An ambiguous match is retained as a source witness, not guessed as an HPG method.
            hpg=len(found)==1
            ident=found[0]['id'] if hpg else add(path,cls+'.'+method,line)
            if ident is None:continue
            row={'node':ident,'obligation':oid,'href':spec_link(oid),'source':source_link(doc['pr_base'],path,line),'hpg':hpg}
            if row not in workflow['witnesses']:workflow['witnesses'].append(row)
    saga_rows=[]
    for saga in sagas.values():
        steps=[]
        for step in sorted(saga['steps'].values(),key=lambda x:(x['line'],x['participant'])):
            step['builders']=sorted(step['builders']);step['handlers']=sorted(step['handlers']);steps.append(step)
        saga_rows.append({'node':saga['node'],'name':saga['name'],'steps':steps})
    for workflow in workflows:
        workflow['nodes']=sorted(set(workflow['nodes']))
        witness_ids={x['node'] for x in workflow['witnesses']}
        workflow['sagas']=[x['node'] for x in saga_rows if x['node'] in witness_ids]
    return {'sagas':saga_rows,'services':services,'nodes':list(nodes.values()),'edges':[{'from':a,'to':b,'kinds':sorted(k)} for (a,b),k in edges.items()],
            'workflows':workflows,'prs':prs,'graph_count':graph_count}
