"""Show the retained WebFlux route comparison and its bounded baseline HPG projection."""
import json
from html import escape
from hpg_report import asset, source_url


def gateway_views(views, baseline, repo, api_impact=None):
    if repo != 'scottswindell-hub/governance-cicd-test':
        return views
    bundle = json.loads(asset('ftgo-gateway-hpg.json'))
    if baseline.get('bundle') != bundle['inventory'] or baseline['source_revision'] != bundle['revision']:
        return views
    eco = views['ecosystem']
    ids = {n['id'] for n in eco['nodes']}
    nodes = []
    for node in bundle['nodes']:
        if node['id'] not in ids:
            nodes.append(dict(node, href=source_url(repo, bundle['revision'], node['file'], node['line'])))
    edges = {(e['from'], e['to']): set(e['kinds']) for e in eco['edges']}
    for edge in bundle['edges']:
        edges.setdefault((edge['from'], edge['to']), set()).update(edge['kinds'])
    workflows = [dict(w, nodes=sorted(set(w['nodes']) | set(bundle['workflow_nodes'])))
                 if w['id'] == 'view_order' else w for w in eco['workflows']]
    prs = eco['prs']
    if prs and api_impact and api_impact['changed']:
        changed_methods = set()
        old_routes = {r['router']['method']: r for r in api_impact['before']}
        for r in api_impact['after']:
            old = old_routes.get(r['router']['method'])
            if not old:
                continue
            if public_route(r) != public_route(old):
                changed_methods.add(r['router']['method'])
            old_clients = {c['client']['method']: c for c in old['clients']}
            for c in r['clients']:
                previous = old_clients.get(c['client']['method'])
                if not previous:
                    continue
                if c['arguments'] != previous['arguments']:
                    changed_methods.add(r['handler']['method'])
                def targets(client):
                    return [(q['verb'], q['path'], q['arguments'], q['property']) for q in client['requests']]
                if targets(c) != targets(previous):
                    changed_methods.add(c['client']['method'])
        changed = {n['id'] for n in bundle['nodes'] if n['method'].split(':')[0] in changed_methods}
        prs = [dict(prs[0], changed=sorted(set(prs[0]['changed']) | changed)), *prs[1:]]
    return {**views, 'ecosystem': {**eco, 'nodes': eco['nodes'] + nodes,
            'edges': [dict(kinds=sorted(k), **{'from': a, 'to': b}) for (a,b),k in edges.items()],
            'workflows': workflows, 'prs': prs, 'graph_count': eco['graph_count'] + 1}}


def public_route(route):
    return route['verb'] + ' ' + route['path']


def http_calls(route):
    return [(client['client'], request) for client in route['clients'] for request in client['requests']]


def api_section(impact, repo, base, head):
    if not impact:
        return ''
    title = '<section><h2>Gateway API impact</h2>'
    if not impact['complete']:
        return title + '<p>Gateway route coverage is incomplete for this comparison.</p></section>'
    summary = []
    if not impact['changed']:
        summary.append('The mapped gateway route and client calls are preserved.')
    def link(node, revision):
        url = source_url(repo, revision, node['file'], node['line'])
        return '<a href="' + escape(url or '', quote=True) + '">' + escape(node['method'].rsplit('.', 1)[-1]) + '</a>'
    old_routes = {public_route(r) for r in impact['before']}
    new_routes = {public_route(r) for r in impact['after']}
    for path in sorted(old_routes - new_routes):
        summary.append('Clients using ' + path + ' lose this mapped gateway entry point.')
    for path in sorted(new_routes - old_routes):
        summary.append('The new mapped gateway entry point is ' + path + '.')
    rows = []
    for label, revision, routes in [('Before', base, impact['before']), ('After', head, impact['after'])]:
        for route in routes:
            for client, request in http_calls(route):
                matched = request['receiver']
                destination = request['destination'] or 'Unresolved destination'
                values = ' · ' + ', '.join(next(c['arguments'] for c in route['clients'] if c['client'] == client and request in c['requests']))
                receiver = link(matched, revision) if matched else 'No matching handler in the mapped controller'
                rows.append('<tr><td>' + label + '</td><td>' + escape(public_route(route)) + '<br>' + link(route['handler'], revision)
                            + '</td><td>' + link(client, revision) + '<br>' + escape(request['verb'] + ' ' + request['path'] + values)
                            + '</td><td>' + escape(destination) + '<br>' + receiver + '</td></tr>')
            if not http_calls(route):
                rows.append('<tr><td>' + label + '</td><td>' + escape(public_route(route)) + '</td>'
                            '<td colspan="2">No supported downstream HTTP call identified.</td></tr>')
    prior = {client['method']: req for r in impact['before'] for client, req in http_calls(r) if req['receiver']}
    for r in impact['after']:
        for client, req in http_calls(r):
            if client['method'] in prior and not req['receiver']:
                summary.append('The gateway can no longer be traced to the previous downstream handler. '
                               'A request to the new target may fail to return order details.')
    return (title + ''.join('<p>' + escape(t) + '</p>' for t in summary)
            + '<table><thead><tr><th>Revision</th><th>Public API</th><th>Client request</th>'
              '<th>Downstream handler</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></section>')
