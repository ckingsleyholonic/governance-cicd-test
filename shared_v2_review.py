#!/usr/bin/env python3
"""Submit an exact-commit PR review to the platform shared-v2 check API."""
import argparse
from difflib import unified_diff
import hashlib
import html
import json
import math
import os
import re
import sys
import time
from urllib.parse import quote, urlsplit

from profile_report import anchor, specification_branch
import downstream_evidence
import pr_description
import standards_report
import workflow_witnesses
from hpg_review import PlatformUnavailable, ReviewError, transport, validate_origin
from hpg_report import source_url


class PollTimeout(ReviewError):
    def __init__(self, project, identifier, head, base, merge_base, spec_revision):
        super().__init__('Polling timed out for ' + identifier)
        self.receipt = dict(format='shared-v2-poll-timeout/1', outcome='inconclusive',
                            project=project, check_id=identifier, head=head, base=base,
                            merge_base=merge_base, spec_revision=spec_revision)


def run(send, project, repo, base, head, spec_revision, *, timeout=1800, interval=5, sleep=time.sleep,
        pending_reviews=None, attempt_id=None, merge_base=None, signed_traces=None, approved_laws=None,
        expected_laws_sha256=None):
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', project):
        raise ReviewError('invalid project ID')
    if not re.fullmatch(r'[^/\s]+/[^/\s]+', repo):
        raise ReviewError('invalid repository')
    if not all(re.fullmatch(r'[0-9a-f]{40}', x or '') for x in (base, head)) or base == head:
        raise ReviewError('invalid PR commits')
    if not isinstance(spec_revision, str) or not spec_revision.strip() or len(spec_revision) > 512:
        raise ReviewError('invalid approved specification revision')
    if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in (timeout, interval)):
        raise ReviewError('invalid polling settings')
    deadline = time.monotonic() + timeout

    identifier = None

    def check_deadline():
        if time.monotonic() >= deadline:
            if identifier is not None:
                raise PollTimeout(project, identifier, head, base, merge_base, spec_revision)
            raise ReviewError('shared check timed out before receiving a check ID')

    def call(method, path, body=None, op=None):
        check_deadline()
        code, reply = send(method, path, body)
        if code not in (200, 202) or reply.get('ok') is not True or (op and reply.get('op') != op):
            raise ReviewError('platform rejected the request or returned an invalid response')
        return reply

    code, projects = send('GET', '/api/projects')
    if code != 200 or not isinstance(projects, dict):
        raise ReviewError('project list is unavailable')
    entries = projects.get('projects')
    if not isinstance(projects.get('tenant_id'), str) or not projects['tenant_id'] or not isinstance(entries, list) or len([
        p for p in entries if isinstance(p, dict) and p.get('onboarding_project_id') == project
        and isinstance(p.get('repo'), str) and p['repo'].casefold() == repo.casefold()
    ]) != 1:
        raise ReviewError('repository and project do not match')
    prefix = '/api/projects/' + project
    def current_spec():
        reply = call('GET', prefix + '/laws?record_version=2', op='laws')
        if reply.get('record_version') != '2' or reply.get('revision') != spec_revision:
            raise ReviewError('approved specification revision changed or is unavailable')
        return reply.get('result')
    laws = current_spec()
    requirements = None
    if expected_laws_sha256 is not None:
        if not re.fullmatch('[0-9a-f]{64}', expected_laws_sha256) or not isinstance(laws, list):
            raise ReviewError('expected an exact approved law set')
        requirements = sorted(row['primary_fact'] for row in laws
                              if isinstance(row, dict) and row.get('approved') is True
                              and row.get('package') == spec_revision
                              and isinstance(row.get('primary_fact'), str))
        digest = hashlib.sha256(json.dumps(requirements, separators=(',', ':')).encode()).hexdigest()
        if not requirements or digest != expected_laws_sha256:
            raise ReviewError('approved project laws differ from the required baseline law set')
    key = hashlib.sha256(('github-pr-v2\0' + repo.lower() + '\0' + base + '\0' + head + '\0' + spec_revision).encode()).hexdigest()
    pr_range = None
    if merge_base is not None:
        if not re.fullmatch('[0-9a-f]{40}', merge_base):
            raise ReviewError('invalid PR merge base')
        pr_range = dict(target=base, merge_base=merge_base)
        key = hashlib.sha256((key + ':' + merge_base).encode()).hexdigest()
    if attempt_id is not None:
        if not isinstance(attempt_id, str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,128}', attempt_id):
            raise ReviewError('invalid submission attempt ID')
        key = hashlib.sha256((key + ':' + attempt_id).encode()).hexdigest()
    if requirements is not None:
        key = hashlib.sha256((key + ':' + expected_laws_sha256).encode()).hexdigest()
    request = dict(spec_revision=spec_revision, commit=head, reason='Review the pull request against approved requirements.',
                   intended_delta='Assess the pull request changes.', idempotency_key='github-pr-' + key)
    if pr_range is not None:
        request['pr_range'] = pr_range
    if requirements is not None:
        request['requirements'] = requirements
    path = prefix + '/checks'
    reply = call('POST', path + '?record_version=2', request, op='check')
    source_revision = None
    while True:
        result = reply.get('result')
        if not isinstance(result, dict):
            raise ReviewError('invalid check result')
        if result.get('pr_range') != pr_range:
            raise ReviewError('retained PR range differs from requested comparison')
        check_id = result.get('id')
        basis = result.get('basis')
        if (not isinstance(check_id, str) or not re.fullmatch(r'check:[0-9a-f]{64}', check_id)
                or identifier is not None and identifier != check_id or result.get('project') != project
                or not isinstance(basis, dict) or basis.get('commit') != head
                or basis.get('spec_revision') != spec_revision or not isinstance(basis.get('source_revision'), str)
                or not basis['source_revision'] or reply.get('record_version') != '2'):
            raise ReviewError('check identity does not match the request')
        if source_revision is not None and source_revision != basis['source_revision']:
            raise ReviewError('source revision changed during polling')
        source_revision = basis['source_revision']
        identifier = check_id
        status = result.get('status')
        if status in ('queued', 'running'):
            sleep(min(interval, max(0, deadline - time.monotonic())))
            reply = call('GET', path + '/' + identifier.replace(':', '%3A') + '?record_version=2', op='check')
            continue
        if status != 'completed' or result.get('error') is not None:
            raise ReviewError('check failed, became stale, or returned an invalid status')
        scope, answers, findings = result.get('scope'), result.get('answers'), result.get('findings')
        if not isinstance(scope, dict) or not isinstance(answers, dict) or not isinstance(findings, list):
            raise ReviewError('missing check scope or evidence')
        if requirements is not None and set(scope) != set(requirements):
            raise ReviewError('the check did not account for every required law')
        if any(v not in ('checked', 'not_affected', 'skipped_disabled_language') for v in scope.values()):
            raise ReviewError('invalid completed check scope')
        checked = {k for k, v in scope.items() if v == 'checked'}
        if checked != set(answers):
            raise ReviewError('check answers do not match evaluated requirements')
        if any(not isinstance(a, dict) or a.get('verdict') not in ('satisfied', 'violated') for a in answers.values()):
            raise ReviewError('invalid check answers')
        violated = {k for k, answer in answers.items() if answer['verdict'] == 'violated'}
        expected = 'violated' if violated else 'satisfied' if checked else None
        if 'verdict' not in result or result['verdict'] != expected:
            raise ReviewError('check verdict does not agree with its answers')
        for finding in findings:
            if (not isinstance(finding, dict) or finding.get('commit') != head
                    or finding.get('source_revision') != basis['source_revision']
                    or finding.get('spec_revision') != spec_revision or finding.get('spec') not in violated
                    or not re.fullmatch(r'd:[0-9a-f]{16}', str(finding.get('finding', '')))
                    or not isinstance(finding.get('message'), str) or not finding['message'].strip()):
                raise ReviewError('finding identity does not match the check')
        if violated != {f['spec'] for f in findings}:
            raise ReviewError('violated check has no finding')
        # Re-admission binds this same attempt to the current host policy.
        # Core returns the retained result or rejects a changed binding.
        verified = call('POST', path + '?record_version=2', request, op='check')
        if verified.get('record_version') != '2' or verified.get('result') != result:
            raise ReviewError('retained check changed before publication')
        laws = current_spec()
        if (approved_laws is not None or signed_traces is not None) and isinstance(laws, list):
            for row in laws:
                if not isinstance(row, dict) or row.get('approved') is not True or row.get('package') != spec_revision:
                    continue
                fact = row.get('primary_fact')
                if not isinstance(fact, str):
                    continue
                record = row['record']
                if approved_laws is not None:
                    approved_laws[fact] = tuple(dict.fromkeys(
                        rendering['text'] for rendering in record.get('renderings', [])
                        if rendering.get('fact') == fact and isinstance(rendering.get('text'), str) and rendering['text'].strip()
                    ))
                if signed_traces is not None and fact in checked:
                    for trace in record.get('trace', []):
                        if trace.get('fact') == fact and isinstance(trace.get('path'), str):
                            signed_traces.setdefault(trace['path'], []).append((fact, trace))
        if pending_reviews is not None and isinstance(laws, list):
            for row in laws:
                if (isinstance(row, dict) and row.get('approved') is False and row.get('change_id')
                        and not row.get('stale')):
                    for field in ('primary_fact', 'before_fact'):
                        fact = row.get(field)
                        if isinstance(fact, str) and fact in checked:
                            pending_reviews.add(fact)
        check_deadline()
        return result


def conclusion(result):
    if result['verdict'] == 'violated':
        return 'violated'
    if result['verdict'] is None or 'skipped_disabled_language' in result['scope'].values():
        return 'inconclusive'
    return 'satisfied'


SPEC_NAVIGATION = """<script>
(() => {
  function reveal(hash) {
    const target = document.getElementById(hash.slice(1));
    if (!target || !target.matches('#workflow-specifications summary')) return;
    for (let parent = target.parentElement; parent; parent = parent.parentElement) {
      if (parent.tagName === 'DETAILS') parent.open = true;
    }
    target.focus({preventScroll:true});
    target.scrollIntoView({block:'start'});
  }
  window.addEventListener('hashchange', () => reveal(location.hash));
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href^="#"]');
    if (link && link.hash === location.hash) reveal(link.hash);
  });
  reveal(location.hash);
})();
</script>"""


def render(result, repo, head, *, platform_origin=None, pending_reviews=(), baseline=None, description=None,
           description_requested=False, approved_laws=None, standards_html=''):
    """Render only native check evidence; do not infer HPG relationships."""
    esc = lambda value: html.escape(str(value), quote=True)
    def spec_link(fact, label):
        if not platform_origin:
            return esc(label)
        origin = validate_origin(platform_origin)
        local = urlsplit(origin).hostname in ('localhost', '127.0.0.1', '::1')
        route = '/local/onboarding/' if local else '/app/onboarding/'
        url = (origin + route + quote(result['project'], safe='') + '?record_version=2&fact='
               + quote(fact, safe='') + '&revision=' + quote(result['basis']['spec_revision'], safe=''))
        return '<a target="_blank" rel="noopener noreferrer" href="' + esc(url) + '">' + esc(label) + '</a>'

    def base_refs(fact):
        refs = set()
        for hit in (baseline or {}).get('witnesses', []):
            if fact in hit.get('specifications', ()):
                refs.add((hit['file'], hit['line'], hit['clause']))
        links = []
        for path, line, clause in sorted(refs):
            url = 'https://github.com/' + repo + '/blob/' + baseline['comparison_base'] + '/' + quote(path, safe='/') + '#L' + str(line)
            links.append('<a href="' + esc(url) + '">' + esc(clause + ' · ' + path.rsplit('/',1)[-1] + ':' + str(line)) + '</a>')
        return '<p><strong>Baseline source trace</strong> ' + ', '.join(links) + '</p>' if links else ''

    approved_laws = approved_laws or {}
    violated_specs = {item['spec'] for item in result['findings']}
    has_workflow_catalog = baseline is not None and 'views' in baseline
    obligations = {row['obligation']: row['text']
                   for row in (baseline or {}).get('views', {}).get('specifications', [])}
    associated = {}
    for hit in (baseline or {}).get('witnesses', []):
        for fact in hit.get('specifications', ()):
            associated.setdefault(fact, set()).add(hit['obligation'])
    extra = []
    destinations = {}
    for fact, texts in approved_laws.items():
        if has_workflow_catalog and fact not in violated_specs:
            continue
        for text in texts:
            matches = sorted(oid for oid in associated.get(fact, ()) if obligations.get(oid) == text)
            if matches:
                destinations[fact, text] = [anchor('obligation', oid) for oid in matches]
            else:
                identifier = anchor('law', fact + '\0' + text)
                destinations[fact, text] = [identifier]
                extra.append(specification_branch(identifier, text, '<p>' + spec_link(fact, 'View approved specification') + '</p>'))
    extra_branch = specification_branch('approved-specifications', 'Approved specifications', ''.join(extra)) if extra else ''
    laws_html = []
    for fact in sorted(violated_specs):
        texts = approved_laws.get(fact, ())
        statement = ''
        if texts:
            for text in texts:
                label = esc(text)
                for identifier in destinations[fact, text]:
                    statement += '<blockquote><a href="#' + identifier + '">' + label + '</a></blockquote>'
        else:
            statement = '<p>Approved law text was unavailable in the report inputs.</p>'
        trace = ''.join(downstream_evidence.links(repo, baseline['comparison_base'], head,
                        baseline['source_revision'], baseline['witnesses'], fact, text) for text in texts) if baseline else ''
        needs_source = any(item['spec'] == fact and item.get('subtype') == 'untraced-span'
                           for item in result['findings'])
        gap = ('<p><strong>Check evidence gap</strong> The checker could not locate current source evidence for this specification.</p>'
               if needs_source else '')
        if trace:
            trace += '<p>These routes are from the baseline graph. Runtime delivery and state changes remain unverified.</p>'
        else:
            trace = '<p>A linked changed line and downstream handler are unavailable for this specification.</p>' + base_refs(fact)
        laws_html.append('<article class="checked-spec">'
                         + statement + gap + trace + '</article>')
    no_findings = ('<p>No changed source was linked to an approved specification in this check.</p>'
                   if not result['answers'] and conclusion(result) == 'inconclusive'
                   else '<p>No specification violations were reported.</p>')
    law_section = ('<section><h2>Specifications affected by violations</h2>'
                   + (''.join(laws_html) or no_findings) + '</section>')

    impacts = []
    for fact, chain in result.get('dependency_evidence', {}).items():
        answer = result['answers'].get(fact)
        if answer is None:
            continue
        source_steps = []
        for step in chain:
            old, new = step['before'], step['after']
            before = ''.join(part['text'] for part in old).splitlines()
            after = ''.join(part['text'] for part in new).splitlines()
            if old and new:
                versions = ((result['pr_range']['merge_base'], old, 'Before'), (head, new, 'After'))
            elif old:
                versions = ((result['pr_range']['merge_base'], old, 'Before'),)
            else:
                versions = ((head, new, 'After'),)
            if before == after and new:
                versions = ((head, new, 'Source in this PR'),)
            links = []
            for revision, excerpts, label in versions:
                for item in excerpts:
                    url = source_url(repo, revision, item['path'], item['first_line'])
                    if url:
                        links.append('<a href="' + esc(url) + '">' + esc(label) + '</a>')
            diff = list(unified_diff(before, after, n=0, lineterm=''))[2:]
            changed = [line[:1] + ' ' + line[1:] for line in diff if line.startswith(('-', '+'))]
            patch = '<pre><code>' + esc('\n'.join(changed)) + '</code></pre>' if changed else ''
            source_steps.append('<li>' + esc(step['label']) + (' · ' + ' · '.join(links) if links else '') + patch + '</li>')
        laws = ''.join('<blockquote>' + spec_link(fact, text) + '</blockquote>'
                       for text in approved_laws.get(fact, ()))
        impacts.append('<article>' + laws + '<p>Specification check: <strong>' + esc(answer['verdict'])
                       + '</strong></p><ol>' + ''.join(source_steps) + '</ol></article>')
    impact_section = ('<section><h2>Changed inputs and downstream value flow</h2>' + ''.join(impacts)
                      + '<p>Source trace only. Runtime delivery remains unverified.</p></section>') if impacts else ''

    rows = []
    for item in result['findings']:
        source = item.get('source') or {}
        location = source.get('path', '') if isinstance(source, dict) else ''
        lines = source.get('lines') if isinstance(source, dict) else None
        if isinstance(lines, list) and lines:
            location += ':' + str(lines[0])
        approval = ''
        if platform_origin and item.get('spec') in pending_reviews:
            approval = '<br>' + spec_link(item['spec'], 'Approve in Platform')
        rows.append('<li><strong>' + esc(item.get('message', 'Finding')) + '</strong>'
                    + ('<br>Check source reference: ' + esc(location) if location else '')
                    + base_refs(item['spec']) + '<br>Finding: '
                    + esc(item.get('finding', '')) + approval + '</li>')
    findings = ''.join(rows) if rows else '<li>No findings for the requirements checked.</li>'
    evidence = ''
    if baseline is not None:
        items = ''.join('<li><strong>' + esc(hit['workflow']) + '</strong> · ' + esc(hit['obligation'])
                        + '<br>' + esc(hit['statement']) + '<br>' + esc(hit['file']) + ':' + esc(hit['line'])
                        + ' · ' + esc(hit['clause'])
                        + '</li>' for hit in baseline['witnesses'])
        route_items = []
        def location(path, line, label):
            url = 'https://github.com/' + repo + '/blob/' + baseline['source_revision'] + '/' + quote(path, safe='/')
            if type(line) is int:
                url += '#L' + str(line)
            return '<a href="' + esc(url) + '">' + esc(label) + '</a>'
        for hit in baseline['witnesses']:
            for route in hit.get('routes', []):
                start = location(hit['file'], hit['line'], hit['workflow'] + ' · ' + hit['clause'])
                def linked(node):
                    name = '.'.join(node['method'].split(':', 1)[0].split('.')[-2:])
                    return location(node['file'], node['line'], node['file'].split('/')[0] + ' · ' + name)
                calls = ''.join('<li>' + linked(node) + '</li>' for node in route['calls'])
                route_items.append('<li>' + start + ' → ' + linked(route['handler'])
                                   + ('<ul>' + calls + '</ul>' if calls else '') + '</li>')
        routes = ('<h3>Potential downstream effects</h3><ul>'
                  + ''.join(route_items) + '</ul>') if route_items else ''
        gaps = ''.join('<li>' + esc(path) + '</li>' for path in baseline['incomplete_patches'])
        evidence = ('<section id="workflow-trace"><h2>Related workflows</h2>'
                    + ('<ul>' + items + '</ul>' if items else '<p>No related workflow identified.</p>')
                    + routes
                    + ('<p>Incomplete patch data</p><ul>' + gaps + '</ul>' if gaps else '') + '</section>')
    if has_workflow_catalog:
        from profile_report import render as render_views
        checked_links = {}
        for hit in baseline['witnesses']:
            for fact in hit.get('specifications', ()):
                if fact in violated_specs:
                    checked_links.setdefault(hit['obligation'], set()).add(fact)
        links = {key: [spec_link(f, 'View violated specification') for f in sorted(facts)]
                 for key, facts in checked_links.items()}
        evidence += render_views(baseline['views'], checked_links=links, extra_branches=extra_branch)
    elif extra_branch:
        evidence += '<section id="workflow-specifications"><h2>Specifications</h2>' + extra_branch + '</section>'
    description_html = pr_description.render(description) if description else ''
    if description_requested and not description:
        description_html = '<section><h2>Behavior change</h2><p>Behavior change summary unavailable.</p></section>'
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<title>Holonic PR analysis</title><style>body{font:16px system-ui;max-width:72rem;margin:3rem auto;'
            'padding:0 1rem;color:#1a2637}section{border:1px solid #d9e2eb;border-radius:12px;padding:1rem 1.5rem;'
            'margin:1rem 0}.checked-spec{border-top:1px solid #d9e2eb;margin-top:1rem}.checked-spec blockquote{'
            'margin:1rem 0;padding:.35rem 1rem;border-left:4px solid #4566a1;font-size:1.125rem}'
            '#workflow-specifications details{margin:.55rem 0 .55rem 1rem}#workflow-specifications summary{cursor:pointer;padding:.35rem;scroll-margin:1rem}'
            '#workflow-specifications summary:target{background:#fff0bf;outline:2px solid #866300}#workflow-specifications details p{margin-left:1.5rem}'
            'li{margin:.7rem 0;overflow-wrap:anywhere}code{overflow-wrap:anywhere}</style>'
            '<h1>Shared governance check</h1><p>' + esc(repo) + ' · ' + esc(conclusion(result)) + '</p>'
            + law_section + impact_section + standards_html + description_html + evidence
            + '<details><summary>Finding details</summary><ul>' + findings + '</ul></details>' + (SPEC_NAVIGATION if has_workflow_catalog or extra_branch else '') + '</html>')


def render_report_model(result, repo, model, origin):
    advisory = standards_report.render(model['standards']) if model['standards_requested'] else ''
    return render(result, repo, result['basis']['commit'], platform_origin=origin,
                  pending_reviews=model['pending_reviews'], baseline=model['baseline'],
                  description=model['description'], description_requested=model['description_requested'],
                  approved_laws=model['approved_laws'], standards_html=advisory)


def publish_model(send, result, snapshot, pr, model):
    project, check = result['project'], result['id']
    expected = f'/reports/{project}/pr/{pr["number"]}'
    code, reply = send('POST', f'/api/projects/{project}/checks/{check}/report-model',
                       dict(basis=result['basis'], pr_range=result['pr_range'], snapshot=snapshot.rsplit('/', 1)[-1],
                            pr=pr, model=model))
    if code in (429, 500, 502, 503, 504):
        raise PlatformUnavailable('Current PR report publication temporarily unavailable')
    if code == 409:
        raise ReportConflict('Current PR report conflict: ' + conflict_reason(reply))
    if code != 200 or reply != {'ok': True, 'op': 'pr_report_model', 'result': {'path': expected}}:
        raise ReviewError(f'Platform did not confirm the current PR report (HTTP {code})')
    return expected


def publish_report(send, result, page):
    project, check = result['project'], result['id']
    digest = hashlib.sha256(page.encode()).hexdigest()
    expected = f'/reports/{project}/{check}/{digest}'
    code, reply = send('POST', f'/api/projects/{project}/checks/{check}/report',
                       dict(basis=result['basis'], pr_range=result['pr_range'], html=page))
    if code == 409:
        raise ReportConflict('PR report conflict: ' + conflict_reason(reply))
    if code in (408, 429, 500, 502, 503, 504):
        raise PlatformUnavailable(f'report endpoint temporarily unavailable (HTTP {code})')
    if code != 200:
        raise ReviewError(f'platform rejected report publication (HTTP {code})')
    if reply != {'ok': True, 'op': 'pr_report', 'result': {'path': expected}}:
        raise ReviewError('platform did not confirm the expected report identity')
    return expected


class ReportConflict(ReviewError):
    pass


def conflict_reason(reply):
    reason = reply.get('error') if isinstance(reply, dict) else None
    known = {
        'Report model does not match its completed PR check',
        'Report does not match a completed PR check',
        'Cannot verify the current report basis',
        'Specification or checking policy changed',
        'Specification or checking policy changed. Run a new check.',
        'Pull request or target branch changed',
        'A newer PR report is already available',
    }
    return reason if isinstance(reason, str) and reason in known else 'Platform returned HTTP 409'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('platform-url', 'project', 'repo', 'base-sha', 'head-sha', 'spec-revision', 'result-file'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--timeout', type=int, default=1800)
    parser.add_argument('--report-file')
    parser.add_argument('--expected-laws-sha256')
    parser.add_argument('--published-path-file')
    parser.add_argument('--latest-path-file')
    parser.add_argument('--base-ref')
    parser.add_argument('--base-tip')
    parser.add_argument('--run-number', type=int)
    parser.add_argument('--run-attempt', type=int)
    parser.add_argument('--pr-number', type=int)
    parser.add_argument('--merge-base')
    parser.add_argument('--attempt-id')
    parser.add_argument('--evidence-bundle')
    parser.add_argument('--changed-files')
    parser.add_argument('--coding-standards-port', type=int)
    parser.add_argument('--description-gateway-port', type=int)
    args = parser.parse_args()
    try:
        key = os.environ.get('HOLONIC_API_KEY', '')
        if not key:
            raise ReviewError('HOLONIC_API_KEY is required')
        started = time.monotonic()
        pending_reviews = set()
        traces = {}
        approved_laws = {}
        send = transport(validate_origin(args.platform_url), key)
        result = run(send, args.project, args.repo,
                     args.base_sha, args.head_sha, args.spec_revision, timeout=args.timeout, pending_reviews=pending_reviews,
                     attempt_id=args.attempt_id, merge_base=args.merge_base, signed_traces=traces, approved_laws=approved_laws,
                     expected_laws_sha256=args.expected_laws_sha256)
        with open(args.result_file, 'w', encoding='utf-8') as out:
            json.dump(result, out, indent=2, ensure_ascii=True)
            out.write('\n')
        outcome = conclusion(result)
        description = None
        baseline = None
        if args.evidence_bundle:
            with open(args.changed_files, encoding='utf-8') as changed:
                files = json.load(changed)
            evidence_api = transport(validate_origin(args.platform_url), key, max_response=4 * 1024 * 1024 + 1024)
            baseline = workflow_witnesses.review(evidence_api, args.project, args.evidence_bundle,
                args.merge_base or args.base_sha, files, tree_for=lambda rev: workflow_witnesses.java_tree(args.repo, rev),
                report_pr=(args.repo, args.pr_number) if args.pr_number else None)
            if args.description_gateway_port is not None:
                try:
                    description = pr_description.generate(pr_description.loopback_gateway(args.description_gateway_port),
                        result, args.repo, args.pr_number, files, baseline)
                except (OSError, ValueError, ReviewError):
                    print('Behavior description unavailable. Retaining the verified check and workflow evidence.', file=sys.stderr)
            workflow_witnesses.associate_specs(baseline, traces, lambda rev, path: workflow_witnesses.github_source(args.repo, rev, path))
            # Recheck the retained result after reading all graphs. The same attempt
            # must still bind to the same policy, signed revision and canonical job.
            refreshed = set()
            confirmed = run(send, args.project, args.repo, args.base_sha, args.head_sha, args.spec_revision,
                            timeout=args.timeout, pending_reviews=refreshed, attempt_id=args.attempt_id,
                            merge_base=args.merge_base, expected_laws_sha256=args.expected_laws_sha256)
            if confirmed != result:
                raise ReviewError('check changed while assembling report evidence')
            pending_reviews = refreshed
        if args.report_file:
            standards = None
            if args.coding_standards_port is not None and time.monotonic() - started < 810:
                standards = standards_report.evaluate(args.coding_standards_port, args.repo, args.merge_base, args.head_sha)
            model = dict(approved_laws=approved_laws, baseline=baseline, description=description,
                         description_requested=args.description_gateway_port is not None,
                         pending_reviews=sorted(pending_reviews), standards=standards,
                         standards_requested=args.coding_standards_port is not None)
            page = render_report_model(result, args.repo, model, args.platform_url)
            with open(args.report_file, 'w', encoding='utf-8') as out:
                out.write(page)
            if args.published_path_file:
                try:
                    path = publish_report(send, result, page)
                    with open(args.published_path_file, 'w', encoding='utf-8') as out:
                        out.write(path)
                    if args.latest_path_file:
                        pr = dict(number=args.pr_number, run=args.run_number, attempt=args.run_attempt,
                                  base_ref=args.base_ref, base_tip=args.base_tip)
                        latest = publish_model(send, result, path, pr, model)
                        with open(args.latest_path_file, 'w', encoding='utf-8') as out:
                            out.write(latest)
                except PlatformUnavailable:
                    print('Browser publication unavailable. Retaining the report artifact.', file=sys.stderr)
                except ReportConflict as exc:
                    print(str(exc) + '. Retaining the verified result and report artifact.', file=sys.stderr)
                    outcome = 'inconclusive'
        print('Shared governance check: ' + outcome)
        return {'satisfied': 0, 'violated': 1, 'inconclusive': 3}[outcome]
    except PollTimeout as exc:
        receipt = exc.receipt
        status_path = ('/api/projects/' + receipt['project'] + '/checks/' +
                       quote(receipt['check_id'], safe='') + '?record_version=2')
        receipt['status_url'] = validate_origin(args.platform_url) + status_path
        with open(args.result_file, 'w', encoding='utf-8') as out:
            json.dump(receipt, out, indent=2)
            out.write('\n')
        if args.report_file:
            with open(args.report_file, 'w', encoding='utf-8') as out:
                out.write('<!doctype html><html lang="en"><meta charset="utf-8">'
                          '<title>Inconclusive PR check</title><h1>Inconclusive PR check</h1>'
                          '<p>The CI polling window expired before the result could be verified.</p>'
                          '<p>Use a project credential to retrieve the retained check at '
                          '<code>' + html.escape(receipt['status_url']) + '</code>.</p></html>')
        print(str(exc), file=sys.stderr)
        return 3
    except (ReviewError, OSError, ValueError, KeyError, TypeError) as exc:
        print('Shared governance check error: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
