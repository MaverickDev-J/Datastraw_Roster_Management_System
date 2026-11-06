"""QA-container restart check, preserving session and saved configuration in memory."""
import subprocess
import sys
import time

import httpx

url = sys.argv[1]
container = sys.argv[2] if len(sys.argv) > 2 else 'datastraw-qa'
if container != 'datastraw-qa':
    raise SystemExit('This check restarts only the dedicated datastraw-qa container.')

with httpx.Client(base_url=url, timeout=30) as client:
    client.get('/api/health').raise_for_status()
    client.post('/api/demo-sessions').raise_for_status()
    original = client.get('/api/config').json()
    employee = original['employees'][0]
    employee['name'] = 'Restart Persistence Check'
    client.put('/api/config/employees', json={'revision': original['revision'], 'record': employee}).raise_for_status()
    before = client.get('/api/config').json()
    subprocess.run(['docker', 'restart', container], check=True, capture_output=True, text=True)
    # Docker can assign a new host port when an ephemeral port is restarted.
    mapping = subprocess.run(['docker', 'port', container, '8000/tcp'], check=True, capture_output=True, text=True).stdout.strip().splitlines()[0]
    client.base_url = 'http://' + mapping
    for _ in range(100):
        try:
            if client.get('/api/health', timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    else:
        raise AssertionError('Container did not become ready after restart')
    after = client.get('/api/config').json()
    assert after == before, 'Saved workspace or configuration changed after restart'
    print('PASS: container startup, session continuity, saved configuration and volume persistence after restart')
