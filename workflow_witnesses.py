"""Project saved workflow witnesses onto removed lines in an exact PR base."""
import base64
import hashlib
import json
import re
import subprocess
from collections import defaultdict

from hpg_review import ReviewError
from downstream_evidence import changed_lines, matched_saga_changes


def removed_lines(files):
    """Return baseline line coordinates only where GitHub supplied a patch."""
    if not isinstance(files, list) or len(files) >= 300:
        raise ReviewError('complete GitHub comparison file list required')
    removed = {}
    missing = []
    for file in files:
        path = file.get('previous_filename') or file['filename']
        patch = file.get('patch')
        if not isinstance(patch, str):
            if file.get('deletions', 0):
                missing.append(path)
            continue
        lines = set()
        cursor = None
        for line in patch.split('\n'):
            if line.startswith('@@'):
                match = re.match(r'^@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@', line)
                if not match:
                    raise ReviewError('unrecognized PR patch hunk')
                cursor = int(match[1])
            elif cursor is not None and line.startswith((' ', '-')):
                if line.startswith('-'):
                    lines.add(cursor)
                cursor += 1
        if len(lines) != file['deletions']:
            missing.append(path)
        removed[path] = lines
    return removed, missing


def java_tree(repo, revision):
    if (not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo)
            or not re.fullmatch('[0-9a-f]{40}', revision)):
        raise ReviewError('invalid Git tree request')
    try:
        result = subprocess.run(['gh', 'api', f'repos/{repo}/git/trees/{revision}?recursive=1'],
                                capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise ReviewError('GitHub baseline tree request timed out') from None
    if result.returncode:
        raise ReviewError('GitHub tree unavailable for the baseline comparison')
    tree = json.loads(result.stdout)
    if tree.get('truncated') is not False:
        raise ReviewError('incomplete GitHub tree')
    files = {}
    for item in tree['tree']:
        path = item['path']
        if path.endswith('.java'):
            if (item['type'] != 'blob' or item['mode'] not in ('100644', '100755') or
                    not re.fullmatch('[0-9a-f]{40}', item['sha']) or path in files):
                raise ReviewError('invalid Java tree entry')
            files[path] = item['sha']
    return files


def saga_routes(graph, hits, changes=None):
    nodes = {n['id']: n for n in graph['nodes']}
    edges = defaultdict(list)
    for edge in graph['edges']:
        edges[edge['source']].append(edge)
    for hit in hits:
        step = nodes[hit['node']]
        if step['kind'] != 'saga_step':
            continue
        declaration = step['data']
        if (hit['file'], hit['line']) != (declaration['file'], declaration['line']):
            raise ReviewError('saga declaration location differs from workflow witness')
        routes = []
        if changes:
            builders = [nodes[e['target']] for e in edges[step['id']]
                        if e['kind'] == 'BUILDS_COMMAND' and nodes[e['target']]['kind'] == 'method']
            builder = builders[0] if len(builders) == 1 else None
            selected = matched_saga_changes(declaration, builder, changes)
            if selected:
                hit['changes'] = selected
        for edge in edges[step['id']]:
            if edge['kind'] != 'ROUTES_TO_HANDLER':
                continue
            handler = nodes[edge['target']]
            calls = [nodes[e['target']] for e in edges[handler['id']] if e['kind'] == 'FRAMEWORK_METHOD_CALL']
            def method(node):
                if node['kind'] != 'method':
                    raise ReviewError('invalid method on recorded saga route')
                v = node['data']
                return dict(method=v['full_name'], file=v['file'], line=v.get('line'))
            routes.append(dict(handler=method(handler), calls=[method(n) for n in calls]))
        hit['routes'] = routes
        command = declaration.get('endpoint', {}).get('command_type')
        if isinstance(command, str) and command:
            hit['command_type'] = command


def review(send, project, bundle, base, files, *, tree_for=None, report_pr=None):
    if not re.fullmatch('[0-9a-f]{64}', bundle):
        raise ReviewError('invalid evidence inventory digest')
    path = '/api/projects/' + project + '/baseline-evidence/' + bundle

    def get(suffix):
        status, reply = send('GET', path + suffix)
        if status != 200 or reply.get('ok') is not True or reply.get('op') != 'baseline_evidence':
            raise ReviewError('baseline evidence is unavailable')
        return reply['result']

    inventory = get('')
    canonical = json.dumps(inventory, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if hashlib.sha256(canonical).hexdigest() != bundle:
        raise ReviewError('evidence inventory differs from the pinned PR baseline')
    revision = inventory['source_revision']
    fingerprint = inventory['java_source_fingerprint']
    if revision != base:
        if tree_for is None or fingerprint.get('algorithm') != 'sha256-java-paths-and-content-v1':
            raise ReviewError('evidence inventory differs from the pinned PR baseline')
        accepted_tree = tree_for(revision)
        if not accepted_tree or len(accepted_tree) != fingerprint['files'] or accepted_tree != tree_for(base):
            raise ReviewError('Java sources differ from the workflow baseline')
    entries = inventory['sha256']
    digest = entries['accepted-workflow-hpg-candidates.json']
    if not re.fullmatch('[0-9a-f]{64}', digest):
        raise ReviewError('invalid witness index digest')
    def document(digest):
        artifact = get('/' + digest)
        data = base64.b64decode(artifact['content'], validate=True)
        if artifact['sha256'] != digest or hashlib.sha256(data).hexdigest() != digest:
            raise ReviewError('baseline artifact digest mismatch')
        return json.loads(data)
    index = document(digest)
    if (index['accepted_source_commit'] != revision or index['source_fingerprint'] != inventory['java_source_fingerprint']
            or index['accepted_workflow_sha256'] != entries['accepted-baseline/workflow-definition.json']):
        raise ReviewError('workflow witness index differs from baseline')
    lines, missing = removed_lines(files)
    changes = changed_lines(files)
    hits = []
    routed_candidates = defaultdict(list)
    for obligation in index['obligations']:
        for clause in obligation['clauses']:
            for match in clause['matches']:
                direct = match['line'] in lines.get(match['file'], ())
                saga = clause.get('form') in ('saga_withCompensation', 'saga_invokeParticipant')
                if not direct and not (report_pr and changes and saga):
                    continue
                if entries.get('hpg/' + match['slice_id'] + '/hpg.json') != match['hpg_sha256']:
                    raise ReviewError('workflow witness references a different graph')
                hit = dict(workflow=obligation['workflow_name'], obligation=obligation['obligation'],
                           statement=obligation['statement'], clause=clause['clause_id'],
                           file=match['file'], line=match['line'], graph=match['hpg_sha256'], node=match['node'])
                if direct:
                    hits.append(hit)
                else:
                    routed_candidates[hit['graph']].append(hit)
    groups = defaultdict(list)
    for hit in hits:
        groups[hit['graph']].append(hit)
    def verified_graphs(digests):
        for digest in digests:
            graph = document(digest)
            if graph['provenance']['source_fingerprint'] != fingerprint:
                raise ReviewError('workflow graph source fingerprint differs')
            candidates = routed_candidates.pop(digest, [])
            saga_routes(graph, [*groups.get(digest, ()), *candidates], changes)
            hits.extend(hit for hit in candidates if hit.get('changes') and hit.get('routes'))
            yield graph
    result = dict(bundle=bundle, source_revision=revision, comparison_base=base, witnesses=hits, incomplete_patches=missing)
    if report_pr:
        from profile_report import project as project_views
        spec = document(entries['accepted-baseline/workflow-definition.json'])
        if spec['baseline_commit'] != revision:
            raise ReviewError('workflow definition revision differs')
        digests = [digest for name,digest in entries.items() if re.fullmatch(r'hpg/[0-9a-f]{32}/hpg\.json', name)]
        if len(digests) != inventory['graphs'] or not set(groups).issubset(digests):
            raise ReviewError('incomplete ecosystem graph inventory')
        result['views'] = project_views(*report_pr, revision, verified_graphs(digests), spec, index, hits, lines)
    else:
        for _ in verified_graphs(groups):
            pass
    return result


def github_source(repo, revision, path):
    from urllib.parse import quote
    if (not re.fullmatch(r'[\w.-]+/[\w.-]+', repo, re.ASCII) or not re.fullmatch('[0-9a-f]{40}', revision)
            or any(part in ('', '.', '..') for part in path.split('/'))):
        raise ReviewError('Invalid GitHub source reference')
    url = f'repos/{repo}/contents/{quote(path, safe="/")}?ref={revision}'
    try:
        reply = subprocess.run(['gh', 'api', url], capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise ReviewError('GitHub source request timed out') from None
    if reply.returncode or len(reply.stdout) > 2 * 1024 * 1024:
        raise ReviewError('GitHub source unavailable for the workflow comparison')
    doc = json.loads(reply.stdout)
    if doc.get('path') != path or doc.get('type') != 'file' or doc.get('encoding') != 'base64':
        raise ReviewError('Missing comparison source bytes')
    return base64.b64decode(doc['content'].replace('\n', ''), validate=True)


def associate_specs(baseline, traces, read_source):
    """Join verified signed spans to witnesses and to their actual edited lines."""
    matches = defaultdict(list)
    for hit in baseline['witnesses']:
        matches[hit['file']].append((hit, hit['line']))
        for change in hit.get('changes', ()):
            before = change['before']
            matches[before['file']].append((change, before['line']))
    for path, hits in matches.items():
        if path not in traces:
            continue
        raw = read_source(baseline['comparison_base'], path)
        digest = hashlib.sha256(raw).hexdigest()
        starts = [0] + [m.end() for m in re.finditer(b'\n', raw)]
        for hit, line in hits:
            if not 1 <= line <= len(starts):
                raise ReviewError('Workflow witness line outside comparison source')
            left = starts[line - 1]
            right = starts[line] if line < len(starts) else len(raw)
            if 'before' in hit and raw[left:right].rstrip(b'\r\n').decode('utf-8') != hit['before']['text']:
                raise ReviewError('Changed PR line differs from verified baseline source')
            facts = set()
            for fact, trace in traces[path]:
                span = re.fullmatch(r'bytes:(\d+):(\d+)', str(trace.get('span')))
                if span and digest == trace.get('content_hash'):
                    start, end = map(int, span.groups())
                    if 0 <= start < end <= len(raw) and left < end and start < right:
                        facts.add(fact)
            if facts:
                hit['specifications'] = sorted(facts)
