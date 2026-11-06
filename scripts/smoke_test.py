"""Live HTTP workflow check. Run with uv run --no-sync python scripts/smoke_test.py."""
import time
import re
import sys

import httpx


def wait_roster(client, payload):
    response = client.post('/api/rosters/generate', json=payload)
    response.raise_for_status()
    for _ in range(200):
        job = client.get('/api/jobs/' + response.json()['job_id']).json()
        if job['status'] == 'COMPLETED':
            return client.get('/api/rosters/' + job['roster_id']).json()
        assert job['status'] not in ['FAILED', 'INTERRUPTED'], job
        time.sleep(0.2)
    raise AssertionError('Job did not complete within 40 seconds')


with httpx.Client(base_url=sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8000', timeout=30) as client:
    assert client.get('/api/health').json()['status'] == 'ok'
    index = client.get('/')
    assert index.status_code == 200
    assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', index.text)
    assert any(a.endswith('.js') for a in assets) and any(a.endswith('.css') for a in assets)
    for asset in assets:
        assert client.get(asset).status_code == 200
    assert client.post('/api/demo-sessions').status_code == 200
    initial = wait_roster(client, {'month': '2026-11', 'config_revision': 1})
    assert initial['data']['validation']['valid']
    absence = {'id': 'smoke_leave', 'employee_id': 'e0', 'start': '2026-11-15', 'end': '2026-11-15', 'kind': 'LEAVE'}
    assert client.put('/api/config/absences', json={'revision': 1, 'record': absence}).status_code == 200
    assert not client.post('/api/rosters/' + initial['id'] + '/validate').json()['valid']
    repaired = wait_roster(client, {'month': '2026-11', 'config_revision': 2, 'base_id': initial['id'], 'base_revision': 1})
    assert repaired['data']['validation']['valid']
    assert repaired['data']['changes']['added']
    assert client.post('/api/rosters/' + repaired['id'] + '/publish', json={'revision': 1, 'config_revision': 2}).status_code == 200
    assert client.get('/api/rosters/' + repaired['id'] + '/export.csv').status_code == 200
    print('PASS: frontend assets, session, generation, leave impact, repair, publication, export')
    print('Engine:', repaired['data']['engine'])
    print('Baseline seconds:', initial['data']['seconds'], 'Repair seconds:', repaired['data']['seconds'])
