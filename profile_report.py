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

    impacts, edges = {}, []
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
                left = start if is_handler else by_method.get((handler['file'], handler['method']))
                right = by_method.get((method['file'], method['method']))
                if left and right:
                    pr['paths'].append({'from': left['id'], 'to': right['id'], 'kinds': [kind], 'slice': hit['graph']})
    return dict(ecosystem=eco, impact=dict(nodes=list(impacts.values()), edges=edges),
                specifications=statements)


def specification_branch(identifier, label, body):
    return (f'<details><summary id="{identifier}">{html.escape(label)}</summary>'
            + body + '</details>')


def with_downstream_effects(views, result, repo, head, event_consequence):
    """Overlay exact PR effects on the retained baseline views."""
    eco = views['ecosystem']
    if not eco['prs']:
        return views
    base = (result.get('pr_range') or {}).get('merge_base')
    effects = []

    def add(before, after, target, title, detail, *, removed=False):
        origin_url = source_url(repo, head, after['file'], after['line'])
        target_url = source_url(repo, base if removed else head, target['file'], target['line'])
        if not origin_url or not target_url:
            return
        item = dict(origin=after['file'].split('/', 1)[0], target=target['file'].split('/', 1)[0],
                    title=title, detail=detail, origin_url=origin_url, target_url=target_url,
                    before_url=source_url(repo, base, before['file'], before['line']),
                    removed=removed)
        if item not in effects:
            effects.append(item)

    def field_effects(route, message):
        for change in route.get('fields', ()):
            for target in change['consumers']:
                operation = target['code'].split('(', 1)[0]
                add(change['before'], change['after'], target, message + ' · ' + change['field'],
                    'The value changes from ' + change['before']['code'] + ' to ' + change['after']['code']
                    + ' and reaches ' + operation + '.')

    api = result.get('api_impact') or {}
    if api.get('complete') and api['changed']:
        from api_report import http_calls, public_route
        old_routes = {r['router']['method']: r for r in api['before']}
        for route in api['after']:
            old = old_routes.get(route['router']['method'])
            if not old:
                continue
            old_calls = {c['method']: (c, req) for c, req in http_calls(old)}
            for client, request in http_calls(route):
                prior = old_calls.get(client['method'])
                if not prior or not prior[1]['receiver']:
                    continue
                if not request['receiver']:
                    add(prior[0], client, prior[1]['receiver'], 'Downstream HTTP route changed',
                        'The new request no longer matches the mapped downstream handler.', removed=True)
                elif public_route(old) != public_route(route):
                    add(old['router'], route['router'], request['receiver'], 'Public API route changed',
                        public_route(route) + ' now leads to this handler through the gateway.')
    for route in (result.get('event_impact') or {}).get('routes', ()):
        event = route['event'].rsplit('.', 1)[-1]
        if route['status'] in ('removed', 'added'):
            removed = route['status'] == 'removed'
            target = route['before' if removed else 'after'][-1]
            handler = route['handler'].rsplit('.', 1)[-1]
            detail = event_consequence(route) or (
                'This path ' + ('no longer supplies ' if removed else 'now supplies ') + event + ' to ' + handler + '.')
            add(route['before'][0], route['after'][0], target,
                event + (' publication removed' if removed else ' publication added'), detail, removed=removed)
        elif route['status'] == 'payload_changed':
            field_effects(route, event)
    saga = result.get('saga_impact') or {}
    for route in saga.get('commands', ()):
        field_effects(route, route['message'].rsplit('.', 1)[-1])
    for route in saga.get('compensations', ()):
        writes = route.get('prior_state_writes', ())
        target = writes[0] if writes else route['before'][-1]
        detail = 'The ' + route['step'] + ' step no longer invokes its compensating command.'
        if writes:
            detail += ' The baseline compensation performed ' + writes[0]['code'] + '.'
        add(route['before'][0], route['after'][0], target, 'Compensation removed', detail, removed=True)

    no_law = not result['answers']
    verified_nodes, verified_edges = {}, []
    for effect in effects:
        start = anchor('effect-change', effect['origin_url'] + effect['title'])
        end = anchor('effect-consumer', effect['target_url'] + effect['title'])
        verified_nodes[start] = dict(id=start, kind='change', label=effect['title'],
                                     detail=effect['detail'], href=effect['origin_url'])
        service_label = effect['target'].removeprefix('ftgo-').replace('-', ' ').capitalize()
        verified_nodes[end] = dict(id=end, kind='downstream', label=service_label,
                                   detail=effect['detail'], href=effect['target_url'])
        verified_edges.append({'from': start, 'to': end, 'label': 'Verified source path', 'detail': effect['detail']})
    nodes = [*verified_nodes.values(), *(node for node in views['impact']['nodes']
                                        if not no_law or node['kind'] == 'change' and not effects)]
    edges = [*verified_edges, *(views['impact']['edges'] if not no_law else ())]
    services = list(dict.fromkeys([*(effect['origin'] for effect in effects),
                                   *(effect['target'] for effect in effects), *eco['services']]))
    prs = [{**eco['prs'][0], 'effects': effects}, *eco['prs'][1:]]
    return {**views, 'ecosystem': {**eco, 'services': services, 'prs': prs},
            'impact': {**views['impact'], 'nodes': nodes, 'edges': edges, 'no_law': no_law,
                       'verified_effects': bool(effects)}}


def render(views, *, extra_branches=''):
    def payload(ident, data):
        raw = json.dumps(data, ensure_ascii=True, separators=(',', ':')).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
        return f'<script type="application/json" id="{ident}">{raw}</script>'
    tabs = [('impact', 'Impact graph', 'impact-graph'),
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
        children = []
        for row in rows:
            oid = row['obligation']
            children.append(specification_branch(anchor('obligation', oid), oid,
                             '<p>' + html.escape(row['text']) + '</p>'))
        entries.append(specification_branch(anchor('workflow', wid), name, ''.join(children)))
    scripts = ''.join('<script>' + asset(name) + '</script>' for name in
                      ('impact-graph.js','ecosystem-map.js','analysis-tabs.js'))
    return ('<style>' + asset('style.css').split('\n', 1)[1] + '</style><section class="analysis-tabs" data-analysis-tabs>'
            '<div class="analysis-tab-list" role="tablist">' + buttons + '</div>' + panels + '</section>'
            + payload('impact-graph-data',views['impact'])
            + payload('ecosystem-data',views['ecosystem']) + '<section id="workflow-specifications"><h2>Workflow specifications</h2>'
            + ''.join(entries) + extra_branches + '</section>' + scripts)
