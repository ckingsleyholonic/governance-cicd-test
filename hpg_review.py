#!/usr/bin/env python3
"""Run one explicitly bound HPG rule through the platform's experimental API.

A pass covers only the selected rule, not all repository governance. Requires an
owner-approved binding for the exact PR base and a platform with the requested
head commit available. Does not execute repository code or publish a CI status.
"""
import argparse
import json
import math
import os
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ReviewError(Exception):
    pass


class PlatformUnavailable(ReviewError):
    """The platform could not be reached or was temporarily unavailable."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_origin(value):
    try:
        u = urlsplit(value)
        port = u.port
    except ValueError:
        raise ReviewError('invalid platform URL') from None
    if (not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in ('', '/')
            or port is not None and not 1 <= port <= 65535
            or not (u.scheme == 'https' or u.scheme == 'http' and u.hostname in ('127.0.0.1', 'localhost', '::1'))):
        raise ReviewError('platform must be an HTTPS origin, or a loopback HTTP origin')
    return value.rstrip('/')


def transport(base, key, *, max_response=2 * 1024 * 1024):
    opener = build_opener(NoRedirect())
    def send(method, path, body=None):
        req = Request(base + path, method=method, headers={'Authorization': 'Bearer ' + key,
                      'Content-Type': 'application/json', 'Accept': 'application/json'},
                      data=None if body is None else json.dumps(body).encode())
        try:
            with opener.open(req, timeout=30) as response:
                raw = response.read(max_response + 1)
                if len(raw) > max_response:
                    raise ReviewError('platform response exceeds limit')
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ReviewError('invalid platform response')
                return response.status, value
        except HTTPError as exc:
            with exc:
                if exc.code == 409 and exc.headers.get_content_type() == 'application/json':
                    try:
                        value = json.loads(exc.read(4097))
                        if isinstance(value, dict):
                            return exc.code, value
                    except (OSError, ValueError):
                        pass
                return exc.code, {}
        except (URLError, OSError):
            raise PlatformUnavailable('platform request failed') from None
        except ValueError:
            raise ReviewError('invalid platform response') from None
    return send


def run(send, project, repo, binding_id, base_sha, head_sha, *, attempts=60, interval=15, sleep=time.sleep,
        return_details=False, expected_binding=None, deadline=None):
    """Return pass/violation/unknown, after validating repository and review identity."""
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', project) or project in ('.', '..'):
        raise ReviewError('invalid project ID')
    if not re.fullmatch(r'[^/\s]+/[^/\s]+', repo):
        raise ReviewError('repository must be owner/name')
    if not re.fullmatch(r'hpg-[0-9a-f]{64}', binding_id):
        raise ReviewError('invalid binding ID')
    if not all(re.fullmatch(r'[0-9a-f]{40}', x or '') for x in (base_sha, head_sha)):
        raise ReviewError('base and head must be full lowercase commit SHAs')
    if base_sha == head_sha or attempts < 1 or not math.isfinite(interval) or interval < 0:
        raise ReviewError('invalid revisions or polling configuration')
    if deadline is not None and time.monotonic() >= deadline:
        raise ReviewError('review timed out')

    def call(method, path, body=None, expected=200, op=None):
        code, data = send(method, path, body)
        if code != expected or data.get('ok') is not True or (op and data.get('op') != op):
            raise ReviewError('platform rejected or returned an invalid response')
        value = data.get('result')
        if not isinstance(value, dict):
            raise ReviewError('platform returned an invalid result')
        return value

    code, data = send('GET', '/api/projects', None)
    projects = data.get('projects') if code == 200 else None
    tenant = data.get('tenant_id') if code == 200 else None
    if not isinstance(tenant, str) or not tenant or not isinstance(projects, list) or len([p for p in projects if isinstance(p, dict)
            and p.get('onboarding_project_id') == project and isinstance(p.get('repo'), str)
            and p['repo'].casefold() == repo.casefold()]) != 1:
        raise ReviewError('repository and project do not match')
    prefix = '/api/projects/' + project + '/hpg/'
    binding = call('GET', prefix + 'bindings/' + binding_id, op='hpg_binding')
    value = binding.get('binding')
    revision, identity = binding.get('intent_revision'), binding.get('project')
    if (not isinstance(value, dict) or value.get('base_revision') != base_sha
            or not isinstance(revision, str) or not revision or not isinstance(identity, str)
            or identity != tenant + '/' + project):
        raise ReviewError('binding does not match the requested base and project')
    if expected_binding is not None and (revision != expected_binding.get('intent_revision')
            or binding.get('intent_handle') != expected_binding.get('intent_handle')
            or value.get('entry_method') != expected_binding.get('entry_method')
            or value.get('rule') != expected_binding.get('rule')):
        raise ReviewError('binding details differ from the selected list')
    started = call('POST', prefix + 'reviews', {'binding_id': binding_id, 'base_revision': base_sha, 'head_revision': head_sha},
                   expected=202, op='hpg_review')
    review_id = started.get('review_id')
    if not isinstance(review_id, str) or not re.fullmatch(r'hpg-review-[0-9a-f]{32}', review_id):
        raise ReviewError('invalid review ID')
    for attempt in range(attempts):
        if deadline is not None and time.monotonic() >= deadline:
            raise ReviewError('review timed out')
        job = call('GET', prefix + 'reviews/' + review_id, op='hpg_review')
        if (job.get('id') != review_id or job.get('project') != identity or job.get('binding_id') != binding_id
                or job.get('base_revision') != base_sha or job.get('head_revision') != head_sha
                or job.get('intent_revision') != revision):
            raise ReviewError('review identity mismatch')
        if job.get('status') == 'done':
            verdict = job.get('verdict')
            result = job.get('result')
            if (verdict not in ('pass', 'violation', 'unknown') or not isinstance(result, dict)
                    or result.get('format') != 'hpg-change-review-v1' or result.get('verdict') != verdict
                    or result.get('base_revision') != base_sha or result.get('head_revision') != head_sha):
                raise ReviewError('invalid review verdict or evidence identity')
            # The server checks freshness on read; verify it again before reporting.
            if call('GET', prefix + 'bindings/' + binding_id, op='hpg_binding') != binding:
                raise ReviewError('binding changed during review')
            return job if return_details else verdict
        if job.get('status') != 'running':
            raise ReviewError('review failed or has an invalid status')
        if attempt + 1 < attempts:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0: raise ReviewError('review timed out')
                sleep(min(interval, remaining))
            else:
                sleep(interval)
    raise ReviewError('review timed out')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('platform-url', 'project', 'repo', 'binding-id', 'base-sha', 'head-sha'):
        parser.add_argument('--' + option, required=True)
    parser.add_argument('--poll-attempts', type=int, default=60)
    parser.add_argument('--poll-interval', type=float, default=15)
    args = parser.parse_args()
    key = os.environ.get('HOLONIC_API_KEY', '')
    if not key:
        parser.error('HOLONIC_API_KEY must be set')
    try:
        base = validate_origin(args.platform_url)
        verdict = run(transport(base, key), args.project, args.repo, args.binding_id, args.base_sha, args.head_sha,
                      attempts=args.poll_attempts, interval=args.poll_interval)
    except ReviewError as exc:
        print('HPG rule review error: ' + str(exc), file=sys.stderr)
        return 2
    print('HPG rule review: ' + verdict + ' (one selected rule only)')
    return {'pass': 0, 'violation': 1, 'unknown': 2}[verdict]


if __name__ == '__main__':
    sys.exit(main())
