"""Bind exact PR changes to verified baseline saga routes."""
import html
import re
from collections import defaultdict

from hpg_report import source_url


def changed_lines(files):
    """Group changed lines into replacement blocks with base and head coordinates."""
    changes = defaultdict(dict)
    for file in files:
        patch = file.get('patch')
        if not isinstance(patch, str):
            continue
        old_file = file.get('previous_filename') or file['filename']
        blocks = []
        block = None
        old_line = new_line = None
        for line in patch.split('\n'):
            if line.startswith('@@'):
                hunk = re.match(r'^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@', line)
                if not hunk:
                    break
                old_line, new_line = map(int, hunk.groups())
                block = None
            elif old_line is not None and line.startswith(('-', '+')):
                if block is None:
                    block = {'before': [], 'after': []}
                    blocks.append(block)
                if line[0] == '-':
                    block['before'].append(dict(file=old_file, line=old_line, text=line[1:]))
                    old_line += 1
                else:
                    block['after'].append(dict(file=file['filename'], line=new_line, text=line[1:]))
                    new_line += 1
            elif line.startswith(' ') and old_line is not None:
                old_line += 1
                new_line += 1
                block = None
        else:
            if (sum(len(b['before']) for b in blocks) != file.get('deletions', 0) or
                    sum(len(b['after']) for b in blocks) != file.get('additions', 0)):
                continue
            added = defaultdict(list)
            for block in blocks:
                for point in block['after']:
                    added[point['text'].strip()].append(point)
            for block in blocks:
                for point in block['before']:
                    moved = added[point['text'].strip()]
                    replacements = block['after']
                    # A unique exact re-addition elsewhere is quoted as a move.
                    # Ambiguous replacements are omitted rather than guessed.
                    after = moved[0] if len(moved) == 1 else (
                        replacements[0] if not moved and len(block['before']) == len(replacements) == 1 else None)
                    changes[old_file][point['line']] = dict(before=point, after=after,
                        kind='moved' if len(moved) == 1 else 'replaced' if after else
                        'removed' if not moved and not replacements else 'edited')
    return changes


def matched_saga_changes(step, builder, changes):
    declaration = changes.get(step['file'], {}).get(step['line'])
    if declaration:
        return [dict(declaration, via='Saga declaration')]
    if not builder:
        return []
    values = builder['data']
    start, end = values.get('start'), values.get('end')
    if type(start) is not int or type(end) is not int:
        return []
    file_changes = changes.get(values.get('file'), {})
    return [dict(change, via='Command builder') for line, change in sorted(file_changes.items())
            if start <= line <= end]


def links(repo, base, head, source_base, hits, fact, text):
    """Render only routes joined to both the law and the actual changed line."""
    if text is None:
        return ''
    items = []
    seen = set()

    def location(revision, point, label):
        url = source_url(repo, revision, point['file'], point['line'])
        return '<a href="' + html.escape(url, quote=True) + '">' + html.escape(label) + '</a>' if url else html.escape(label)

    for hit in hits:
        if hit['statement'] != text or not hit.get('routes'):
            continue
        for change in hit.get('changes', ()):
            if fact not in change.get('specifications', ()):
                continue
            before, after = change['before'], change['after']
            for route in hit['routes']:
                handler = route['handler']
                key = (before['file'], before['line'], handler['method'])
                if key in seen:
                    continue
                seen.add(key)
                label = {'moved': 'Moved to', 'replaced': 'Replaced with'}.get(change['kind'])
                before_link = location(base, before, before['file'].rsplit('/', 1)[-1] + ':' + str(before['line']))
                after_link = location(head, after, after['file'].rsplit('/', 1)[-1] + ':' + str(after['line'])) if after else ''
                snippet = '<p><code>' + html.escape(before['text'].strip()) + '</code> · ' + before_link + '</p>'
                if label and after:
                    snippet += '<p>' + label + ' <code>' + html.escape(after['text'].strip()) + '</code> · ' + after_link + '</p>'
                else:
                    snippet += ('<p>Removed from this location.</p>' if change['kind'] == 'removed' else
                                '<p>The changed block has no unambiguous replacement line.</p>')
                route_nodes = (handler, *route['calls'])
                route_links = ''
                for node in route_nodes:
                    method = '.'.join(node['method'].split(':', 1)[0].split('.')[-2:])
                    route_links += ('<li>' + location(source_base, node, method) + '</li>')
                command = hit.get('command_type')
                if command:
                    short_command = command.rsplit('.', 1)[-1]
                    snippet += '<p>Baseline command <code>' + html.escape(short_command) + '</code> · ' + location(
                        source_base, hit, 'Saga declaration') + '</p>'
                    constructor = r'\bnew\s+([A-Za-z_$][\w$]*)\s*\('
                    if change['via'] == 'Command builder' and after:
                        old_types = re.findall(constructor, before['text'])
                        new_types = re.findall(constructor, after['text'])
                        if old_types == [short_command] and len(new_types) == 1 and new_types != old_types:
                            snippet += ('<p>This call site now constructs <code>' + html.escape(new_types[0])
                                        + '</code> in place of the baseline command.</p>')
                items.append('<li><strong>' + html.escape(change['via']) + '</strong>' + snippet +
                             '<p><strong>Baseline downstream handler and recorded calls</strong></p><ul>' +
                             route_links + '</ul></li>')
                if len(items) == 8:
                    return '<h4>Change and downstream trace</h4><ul>' + ''.join(items) + '</ul>'
    return '<h4>Change and downstream trace</h4><ul>' + ''.join(items) + '</ul>' if items else ''
