"""Render the per-PR service and workflow projections with the Pages assets."""
from collections import defaultdict
import hashlib
import html
import json

from ecosystem_projection import ecosystem_data
from hpg_report import asset, source_url


def anchor(kind, value):
    return kind + '-' + hashlib.sha256(value.encode()).hexdigest()[:20]


def project(repo, number, revision, graphs, spec, index, hits, lines):
    def source(rev, path, line):
        return source_url(repo, rev, path, line)

    eco = ecosystem_data({'cases': [], 'pr_base': revision}, spec, graphs, None, source,
                         lambda oid: '#' + anchor('obligation', oid), lambda wid: '#' + anchor('workflow', wid))
    by_file = defaultdict(list)
    by_method = {}
    for node in eco['nodes']:
        by_file[node['file']].append(node)
        by_method[node['file'], node['method']] = node
    def enclosing(path, line):
        found = [n for n in by_file[path] if type(n['line']) is int and type(n['end']) is int and
                 n['line'] <= line <= n['end']]
        return found[0] if len(found) == 1 else None

    changed = {node['id'] for path, numbers in lines.items() for line in numbers
               if (node := enclosing(path, line))}
    pr = dict(number=number, title='Change analysis', changed=sorted(changed), matches=[], paths=[], check=None)
    eco['prs'] = [pr]
    workflows = {w['id']: w for w in eco['workflows']}
    obligations = {}
    for w in spec['workflows']:
        obligations.update({o['id']: w['id'] for o in w['obligations']})
    statements = []
    for row in index['obligations']:
        wid, oid = row['workflow'], row['obligation']
        if obligations.get(oid) != wid:
            raise ValueError('Workflow obligation differs from baseline specification')
        workflow = workflows[wid]
        statements.append(dict(workflow=wid, name=workflow['name'], obligation=oid, text=row['statement']))
        for clause in row['clauses']:
            for match in clause['matches']:
                path, line = match['file'], match['line']
                node = by_method.get((path, match.get('method'))) or enclosing(path, line)
                if node is None:
                    continue
                ref = dict(node=node['id'], obligation=oid, href='#' + anchor('obligation', oid),
                           source=source(revision, path, line), hpg=True)
                if ref not in workflow['witnesses']:
                    workflow['witnesses'].append(ref)
        witnessed = {r['node'] for r in workflow['witnesses']}
        workflow['sagas'] = [s['node'] for s in eco['sagas'] if s['node'] in witnessed]

    impacts, edges, paths = {}, [], []
    def impact(kind, key, label, detail, href):
        ident = anchor(kind, key)
        impacts[ident] = dict(id=ident, kind=kind, label=label, detail=detail, href=href)
        return ident
    def connect(a, b, label, detail):
        edge = {'from': a, 'to': b, 'label': label, 'detail': detail}
        if edge not in edges:
            edges.append(edge)
    def method_label(method):
        return '.'.join(method.split(':', 1)[0].split('.')[-2:])

    for node in eco['nodes']:
        if node['id'] in changed:
            impact('change', node['id'], method_label(node['method']), node['method'], node['href'])
    for hit in hits:
        path, line, oid = hit['file'], hit['line'], hit['obligation']
        start = enclosing(path, line)
        label = method_label(start['method']) if start else path.rsplit('/', 1)[-1]
        origin = impact('change', start['id'] if start else path + '#' + str(line), label, hit['clause'], source(revision, path, line))
        rule = impact('specification', oid, oid, hit['statement'], '#' + anchor('obligation', oid))
        connect(origin, rule, 'Workflow clause', hit['clause'])
        workflow = workflows[obligations[oid]]
        if start:
            workflow['nodes'] = sorted(set(workflow['nodes']) | {start['id']})
            pr['matches'].append(dict(workflow=workflow['id'], obligation=oid, node=start['id']))
        for route in hit.get('routes', []):
            handler = route['handler']
            previous = origin
            for method in [handler, *route['calls']]:
                is_handler = method is handler
                kind = 'ROUTES_TO_HANDLER' if is_handler else 'FRAMEWORK_METHOD_CALL'
                target = impact('downstream', method['method'] + method['file'], method_label(method['method']),
                                method['method'], source(revision, method['file'], method['line']))
                connect(origin if is_handler else previous, target, kind, hit['graph'])
                if is_handler:
                    previous = target
                    paths.append({'from': path.split('/')[0], 'to': method['file'].split('/')[0], 'step': hit['clause'],
                                  'handler': method_label(method['method']), 'kind': kind, 'source_href': source(revision,path,line),
                                  'target_href': source(revision,method['file'],method['line'])})
                left = start if is_handler else by_method.get((handler['file'], handler['method']))
                right = by_method.get((method['file'], method['method']))
                if left and right:
                    pr['paths'].append({'from': left['id'], 'to': right['id'], 'kinds': [kind], 'slice': hit['graph']})
    return dict(ecosystem=eco, impact=dict(nodes=list(impacts.values()), edges=edges),
                boundary=dict(paths=paths, services=sorted({s for p in paths for s in (p['from'],p['to'])}),
                              ecosystem_services=[], ecosystem_paths=[]), specifications=statements)


def render(views, *, checked_links=None):
    def payload(ident, data):
        raw = json.dumps(data, ensure_ascii=True, separators=(',', ':')).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
        return f'<script type="application/json" id="{ident}">{raw}</script>'
    tabs = [('boundary', 'Service boundary', 'service-map'), ('impact', 'Impact graph', 'impact-graph'),
            ('ecosystem', 'Service ecosystem', 'ecosystem-map')]
    buttons = ''.join(f'<button type="button" role="tab" id="tab-{key}" aria-controls="panel-{key}">{label}</button>'
                      for key, label, _ in tabs)
    panels = ''.join(f'<div id="panel-{key}" role="tabpanel" aria-labelledby="tab-{key}"><div id="{host}" data-per-pr></div></div>'
                     for key, _, host in tabs)
    specs = defaultdict(list)
    for row in views['specifications']:
        specs[row['workflow'],row['name']].append(row)
    entries = []
    for (wid, name), rows in specs.items():
        entries.append(f'<h3 id="{anchor("workflow",wid)}">{html.escape(name)}</h3>')
        for row in rows:
            oid = row['obligation']
            links = (checked_links or {}).get(oid, ())
            checked = '<br>Checked specification: ' + ', '.join(links) if links else ''
            entries.append(f'<p id="{anchor("obligation",oid)}"><strong>{html.escape(oid)}</strong> · {html.escape(row["text"])}{checked}</p>')
    scripts = ''.join('<script>' + asset(name) + '</script>' for name in
                      ('test-visualizations.js','impact-graph.js','ecosystem-map.js','analysis-tabs.js'))
    return ('<style>' + asset('style.css').split('\n', 1)[1] + '</style><section class="analysis-tabs" data-analysis-tabs>'
            '<div class="analysis-tab-list" role="tablist">' + buttons + '</div>' + panels + '</section>'
            + payload('test-map-data',views['boundary']) + payload('impact-graph-data',views['impact'])
            + payload('ecosystem-data',views['ecosystem']) + '<section><h2>Workflow specifications</h2>'
            + ''.join(entries) + '</section>' + scripts)
