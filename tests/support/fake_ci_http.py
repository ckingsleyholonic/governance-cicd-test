#!/usr/bin/env python3
"""Loopback-only fake GitHub and review APIs backed by a synthetic fixture.

This server returns scripted outcomes. It does not evaluate governance.
"""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def make_handler(fixture, scenario, require_onboarding=False):
    class Handler(BaseHTTPRequestHandler):
        records = {'statuses': [], 'comments': [], 'review_requests': 0, 'onboarded': not require_onboarding}
        def log_message(self, *args):
            pass
        def send(self, code, body, content_type='application/json'):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def authorized(self):
            return self.headers.get('Authorization') == 'Bearer ' + fixture['github_token']
        def do_GET(self):
            if self.path == '/_test/state':
                self.send(200, self.records)
                return
            base = f"/repos/{fixture['owner']}/{fixture['repo']}/pulls/{fixture['pr_number']}"
            if self.path != base or not self.authorized():
                self.send(404, {'error': 'not found'})
                return
            if self.headers.get('Accept') == 'application/vnd.github.v3.diff':
                if scenario == 'diff-error':
                    self.send(503, {'error': 'simulated diff failure'})
                else:
                    self.send(200, fixture['diff'].encode(), 'text/plain')
            else:
                head = 'c' * 40 if scenario == 'stale' else fixture['head_sha']
                self.send(200, {'head': {'sha': head}, 'base': {'sha': fixture['base_sha']}})
        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
                if not isinstance(body, dict):
                    raise ValueError('expected object')
            except (ValueError, TypeError):
                self.send(400, {'error': 'invalid JSON'})
                return
            if self.path == '/_test/onboard':
                if (body.get('owner') != fixture['owner'] or body.get('repo') != fixture['repo']
                        or body.get('base_sha') != fixture['base_sha'] or body.get('api_key') != fixture['api_key']):
                    self.send(400, {'error': 'fixture mismatch'})
                    return
                self.records['onboarded'] = True
                self.send(201, {'status': 'ready', 'baseline_revision': fixture['baseline_revision']})
                return
            if self.path == '/verify-checks':
                if not self.records['onboarded']:
                    self.send(409, {'error': 'repository is not onboarded'})
                    return
                if body.get('api_key') != fixture['api_key']:
                    self.send(401, {'error': 'invalid test key'})
                    return
                if body.get('mode') == 'start':
                    valid = (body.get('owner') == fixture['owner'] and body.get('repo') == fixture['repo']
                             and body.get('sha') == fixture['head_sha'] and body.get('base_sha') == fixture['base_sha']
                             and body.get('pull_request_number') == fixture['pr_number']
                             and body.get('pull_request_title') == fixture['title']
                             and isinstance(body.get('diff'), str)
                             and body['diff'].rstrip('\n') == fixture['diff'].rstrip('\n'))
                    if not valid:
                        self.send(400, {'error': 'fixture mismatch'})
                        return
                    self.records['review_requests'] += 1
                    self.send(202, {'review_id': 'synthetic-review'})
                elif body.get('mode') == 'status' and body.get('review_id') == 'synthetic-review':
                    outcomes = {'pass': (200, {'status': 'DONE', 'decision': 'PASS'}),
                                'block': (200, {'status': 'DONE', 'decision': 'BLOCK'}),
                                'error': (200, {'status': 'ERROR', 'reason': 'simulated error'}),
                                'timeout': (200, {'status': 'RUNNING'}),
                                'http-error': (503, {'status': 'DONE', 'decision': 'PASS'})}
                    code, response = outcomes.get(scenario, outcomes['pass'])
                    self.send(code, response)
                else:
                    self.send(400, {'error': 'invalid review request'})
                return
            repo = f"/repos/{fixture['owner']}/{fixture['repo']}"
            if not self.authorized():
                self.send(401, {'error': 'invalid test token'})
            elif self.path == f"{repo}/statuses/{fixture['head_sha']}":
                self.records['statuses'].append(body)
                self.send(201, {'ok': True})
            elif self.path == f"{repo}/issues/{fixture['pr_number']}/comments":
                self.records['comments'].append(body)
                self.send(201, {'ok': True})
            else:
                self.send(404, {'error': 'not found'})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, default=ROOT / 'tests/data/onboarded.json')
    parser.add_argument('--scenario', choices=['pass', 'block', 'error', 'timeout', 'http-error', 'diff-error', 'stale'], default='pass')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--require-onboarding', action='store_true')
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text())
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(fixture, args.scenario, args.require_onboarding))
    print(f'http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
