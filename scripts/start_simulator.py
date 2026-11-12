"""Start the test-only review service for this GitHub Actions job."""
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

env = os.environ
owner, repo = env['OWNER'], env['REPO']
number = int(env['PR_NUMBER'])
diff = subprocess.check_output(
    ['gh', 'api', f'repos/{owner}/{repo}/pulls/{number}',
     '-H', 'Accept: application/vnd.github.v3.diff'], text=True)
fixture = {
    'owner': owner, 'repo': repo, 'pr_number': number, 'title': env['PR_TITLE'],
    'base_sha': env['BASE_SHA'], 'head_sha': env['HEAD_SHA'], 'diff': diff,
    'api_key': 'simulation-only-key', 'github_token': 'unused-synthetic-token',
    'baseline_revision': 'simulated-baseline',
}
fixture_path = Path(env['RUNNER_TEMP']) / 'simulated-review-fixture.json'
fixture_path.write_text(json.dumps(fixture))
scenario = 'block' if '[simulate block]' in env['PR_TITLE'].lower() else 'pass'
subprocess.Popen(['python3', 'tests/support/fake_ci_http.py', '--fixture', str(fixture_path),
                  '--scenario', scenario, '--port', '18765', '--require-onboarding'],
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
url = 'http://127.0.0.1:18765/_test/onboard'
payload = json.dumps({k: fixture[k] for k in ('owner', 'repo', 'base_sha', 'api_key')}).encode()
for attempt in range(30):
    try:
        request = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=2) as response:
            if response.status != 201:
                raise RuntimeError('Simulated onboarding failed')
        break
    except OSError:
        if attempt == 29:
            raise
        time.sleep(0.2)
print(f'Simulator onboarded; scripted outcome: {scenario.upper()}')
