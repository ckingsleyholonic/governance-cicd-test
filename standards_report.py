"""Fetch and present the independent, revision-bound pilot advisory."""
from html import escape
from http.client import HTTPException
import json
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener

from hpg_review import NoRedirect

REVISION = '31e773e91065650ef5fa94c20c485556ba3d81af'


def fetch(port, repository, merge_base, head):
    request = dict(repository=repository, merge_base=merge_base, head=head)
    if not 1 <= port <= 65535 or not merge_base:
        raise ValueError('Invalid advisory configuration')
    opener = build_opener(ProxyHandler({}), NoRedirect())
    url = f'http://127.0.0.1:{port}/coding-standards'
    req = Request(url, data=json.dumps(request).encode(), headers={'Content-Type': 'application/json'})
    with opener.open(req, timeout=90) as response:
        raw = response.read(524289)
    if len(raw) > 524288:
        raise ValueError('Oversized advisory response')
    row = json.loads(raw)
    if (not isinstance(row, dict) or any(row.get(k) != v for k, v in request.items())
            or row.get('forge_revision') != REVISION):
        raise ValueError('Standards comparison identity changed')
    return row


def render(row):
    title = '<section><h2>Check coding standards</h2>'
    if row is None:
        return title + '<p>Advisory unavailable for this revision.</p></section>'
    if row['state'] not in ('passed', 'failed', 'stopped', 'error'):
        raise ValueError('Unknown standards outcome')
    gaps = row['gaps']
    if not isinstance(gaps, dict) or any(type(n) is not int or n < 0 for n in gaps.values()):
        raise ValueError('Invalid coverage')
    incomplete = any(gaps.values()) or row['state'] in ('stopped', 'error')
    status = 'Partial coverage' if incomplete else 'Completed'
    groups = []
    for key, label in [('findings', 'Active rules'), ('watch_only', 'Watch-only rules')]:
        items = []
        for finding in row[key]:
            location = escape(finding['file']) + ':' + escape(str(finding['line']))
            if row.get('repository') and row.get('head') and str(finding['line']).isdigit():
                url = ('https://github.com/' + quote(row['repository'], safe='/') + '/blob/'
                       + quote(row['head'], safe='') + '/' + quote(finding['file'], safe='/')
                       + '#L' + str(finding['line']))
                location = '<a href="' + escape(url, quote=True) + '">' + location + '</a>'
            items.append('<li><strong>' + escape(finding['name']) + '</strong> · ' + escape(finding['band'])
                         + ' · <code>' + escape(finding['rule_id']) + '</code><br>'
                         + location
                         + '<br>' + escape(finding.get('guidance', '')) + '</li>')
        if items:
            groups.append('<h3>' + label + '</h3><ul>' + ''.join(items) + '</ul>')
    counts = row.get('counts', {})
    coverage = ''
    if type(counts.get('checks')) is int and type(counts.get('not_judged')) is int:
        coverage = ('<p>' + str(counts['checks'] - counts['not_judged']) + ' of ' + str(counts['checks'])
                    + ' rule evaluations completed.</p>')
    if row['findings']:
        detail = 'Active coding-standard findings are listed below.'
    elif row['state'] == 'passed':
        detail = 'No active coding-standard issues found in the completed checks.'
    else:
        detail = 'No conclusive active-rule result is available.'
    return (title + '<p>' + detail + '</p><p>Advisory · ' + status + '</p>'
            + coverage + ''.join(groups) + '</section>')



def evaluate(port, repository, merge_base, head):
    try:
        row = fetch(port, repository, merge_base, head)
        render(row)
        return row
    except (OSError, HTTPException, ValueError, KeyError, TypeError, AttributeError, RecursionError):
        return None


def advisory(port, repository, merge_base, head):
    return render(evaluate(port, repository, merge_base, head))
