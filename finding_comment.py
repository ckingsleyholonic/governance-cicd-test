"""Build the PR comment from the exact check receipt and publish one page per finding."""
from html import escape
import re

from hpg_report import asset, source_url


def label(text):
    # Source-derived text is plain text inside a Markdown link, including on GitHub.
    text = ' '.join(text.split())
    text = text if len(text) <= 240 else text[:239] + '…'
    return re.sub(r'([\\\[\]*_<>!|&@\x60~])', r'\\\1', text)


def source(repo, head, path, line):
    url = source_url(repo, head, path, line)
    if not url:
        return ''
    location = path + (f':{line}' if type(line) is int else '')
    return '<p><a href="' + escape(url, quote=True) + '">' + escape(location) + '</a></p>'


def summary(result, model, repo, pr, origin, publish, render_workflow):
    lines = ['### Holonic PR analysis', '']
    head = result['basis']['commit']
    pr_url = f'https://github.com/{repo}/pull/{pr}'

    def finding(group, title, detail, identity):
        page = ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" '
                'content="width=device-width, initial-scale=1"><title>' + escape(title) + '</title>'
                '<style>body{font:1rem/1.55 system-ui;max-width:56rem;margin:2rem auto;padding:0 1rem}'
                'pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><style>' + asset('pr-theme.css')
                + '</style><header class="report-heading"><strong>' + escape(group) + '</strong>'
                '<div class="report-actions"><a href="' + escape(pr_url) + '">PR #' + str(pr)
                + '</a><button id="report-theme" hidden type="button" aria-label="Use dark theme">☾</button>'
                '</div></header><script>' + asset('pr-theme.js') + '</script><main><h1>' + escape(title)
                + '</h1>' + detail + '<p><small>' + escape(identity) + ' · ' + escape(head[:12])
                + '</small></p></main></html>')
        lines.append(f'❌ **{group}** · [{label(title)}]({origin}{publish(page)})')

    group = 'Workflow specifications'
    for item in result['findings']:
        fact = item['spec']
        laws = model['approved_laws'].get(fact, [])
        title = laws[0] if laws else item['message']
        path = publish(render_workflow(item))
        lines.append(f'❌ **{group}** · [{label(title)}]({origin}{path})')
    if not result['findings']:
        complete = result['verdict'] == 'satisfied' and 'skipped_disabled_language' not in result['scope'].values()
        lines.append('✅ ' + group if complete else '⚪ ' + group + ' · Inconclusive')
    if model['standards_requested']:
        standards = model['standards'] or {}
        for group, row in [('Coding standards', standards), ('Test quality', standards.get('test_quality'))]:
            if not row:
                lines.append('⚪ ' + group + ' · Unavailable')
                continue
            findings = [item for item in row['findings'] if item['band'] == 'finding']
            for item in findings:
                title = item['name'] + ' · ' + item['file'].rsplit('/', 1)[-1] + ':' + str(item['line'])
                detail = '<p>' + escape(item.get('guidance', '')) + '</p>' + source(repo, head, item['file'], item['line'])
                finding(group, title, detail, item['rule_id'])
            if any(row['gaps'].values()) or row['state'] not in ('passed', 'failed'):
                lines.append('⚪ ' + group + ' · Partial coverage')
            elif not findings:
                if row['state'] == 'passed':
                    suffix = ' · No changes in scope' if row['counts'].get('checks') == 0 else ''
                    lines.append('✅ ' + group + suffix)
                else:
                    lines.append('⚪ ' + group + ' · Inconclusive')
    return '\n\n'.join([lines[0], '  \n'.join(lines[2:])])
