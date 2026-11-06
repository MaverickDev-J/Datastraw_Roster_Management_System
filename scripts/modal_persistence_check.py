"""Verify this app's saved state after restarting only its own Modal container.

Run with the deploy dependency group. This deliberately interrupts the roster
app briefly; run when nobody is editing or recording a demonstration.
"""
import json
import subprocess
import sys
import time
from urllib.parse import urlparse

import httpx


APP_NAME = 'datastraw-roster'
url = sys.argv[1].rstrip('/')
profile = sys.argv[2] if len(sys.argv) > 2 else 'yjoy1572'
host = urlparse(url)
if host.scheme != 'https' or not host.hostname or '--datastraw-roster.' not in host.hostname:
    raise SystemExit('This check supports only the public datastraw-roster Modal endpoint.')


def cli(*arguments, json_output=False):
    command = [sys.executable, '-X', 'utf8', '-m', 'modal', *arguments, '--profile', profile]
    if json_output:
        command.append('--json')
    result = subprocess.run(command, check=True, capture_output=True, text=True, encoding='utf-8')
    return json.loads(result.stdout) if json_output else result.stdout


apps = [a for a in cli('app', 'list', json_output=True) if a['description'] == APP_NAME and a['state'] == 'deployed']
if len(apps) != 1:
    raise SystemExit('Expected exactly one deployed datastraw-roster app; refusing to stop any container.')
app_id = apps[0]['app_id']

with httpx.Client(base_url=url, timeout=120) as client:
    client.post('/api/demo-sessions').raise_for_status()
    config = client.get('/api/config').json()
    change = {'revision': config['revision'], 'record': dict(config['employees'][0], name='Modal Persistence QA')}
    client.put('/api/config/employees', json=change).raise_for_status()
    before = client.get('/api/config').json()
    response = client.post('/api/rosters/generate', json={'month': '2026-11', 'config_revision': before['revision']})
    response.raise_for_status()
    job_id = response.json()['job_id']
    for _ in range(90):
        state = client.get('/api/jobs/' + job_id).json()
        if state['status'] == 'COMPLETED':
            break
        assert state['status'] not in ['FAILED', 'INTERRUPTED'], state
        time.sleep(0.5)
    else:
        raise AssertionError('Generation did not complete')
    roster_id = state['roster_id']
    client.post('/api/rosters/' + roster_id + '/publish', json={'revision': 1, 'config_revision': before['revision']}).raise_for_status()
    publication = client.get('/api/rosters/' + roster_id).json()
    assert publication['status'] == 'PUBLISHED'

    containers = cli('container', 'list', '--app-id', app_id, json_output=True)
    assert len(containers) == 1, 'Expected the single-container deployment'
    container = containers[0]
    assert container['app_id'] == app_id and container['app_name'] == APP_NAME
    old_id = container['container_id']
    print('Restarting only the datastraw-roster web container; other apps are untouched.', flush=True)
    cli('container', 'stop', old_id, '--graceful', '--yes')
    for _ in range(30):
        current = cli('container', 'list', '--app-id', app_id, json_output=True)
        if all(c['container_id'] != old_id for c in current):
            break
        time.sleep(1)
    else:
        raise AssertionError('The original container did not exit')

    restored = client.get('/api/config')
    restored.raise_for_status()
    assert restored.json() == before, 'Saved configuration or workspace cookie was lost'
    result = client.get('/api/rosters/' + roster_id)
    result.raise_for_status()
    assert result.json() == publication, 'Published roster changed after container replacement'
    current = cli('container', 'list', '--app-id', app_id, json_output=True)
    assert current and all(c['container_id'] != old_id for c in current)
    print('PASS: workspace cookie, exact configuration and published roster survive a fresh Modal container.', flush=True)
