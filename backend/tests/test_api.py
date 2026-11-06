import time

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app('sqlite:///' + (tmp_path / 'test.db').as_posix())
    with TestClient(app) as c:
        c.post('/api/demo-sessions')
        yield c


def generated(client):
    revision = client.get('/api/config').json()['revision']
    r = client.post('/api/rosters/generate', json={'month': '2026-11', 'config_revision': revision})
    assert r.status_code == 202, r.text
    jid = r.json()['job_id']
    for _ in range(200):
        state = client.get('/api/jobs/' + jid).json()
        if state['status'] == 'COMPLETED':
            return client.get('/api/rosters/' + state['roster_id']).json()
        assert state['status'] not in ['FAILED', 'INTERRUPTED'], state
        time.sleep(0.05)
    pytest.fail('Generation did not complete')


def test_generation_publication_and_immutable_history(client):
    r = generated(client)
    assert r['data']['validation']['valid']
    published = client.post(f'/api/rosters/{r["id"]}/publish', json={'revision': 1, 'config_revision': 1})
    assert published.status_code == 200, published.text
    edit = client.patch(f'/api/rosters/{r["id"]}/assignments', json={'revision': 2, 'config_revision': 1, 'assignments': []})
    assert edit.status_code == 409
    csv = client.get(f'/api/rosters/{r["id"]}/export.csv')
    assert csv.status_code == 200 and 'Date,Employee,Brand' in csv.text


def test_workspace_isolation(client):
    roster = generated(client)
    with TestClient(client.app) as other:
        other.post('/api/demo-sessions')
        assert other.get('/api/rosters/'+roster['id']).status_code == 404
        assert other.get('/api/rosters').json() == []


def test_stale_config_and_invalid_draft_block_publication(client):
    roster = generated(client)
    absence = {'id': 'new_absence', 'employee_id': 'e8', 'start': '2026-11-16', 'end': '2026-11-16', 'kind': 'LEAVE'}
    r = client.put('/api/config/absences', json={'revision': 1, 'record': absence})
    assert r.status_code == 200, r.text
    assert client.put('/api/config/absences', json={'revision': 1, 'record': absence}).status_code == 409
    assert client.post(f'/api/rosters/{roster["id"]}/publish', json={'revision': 1, 'config_revision': 1}).status_code == 409
    validation = client.post(f'/api/rosters/{roster["id"]}/validate').json()
    assert not validation['valid']
    r = client.patch(f'/api/rosters/{roster["id"]}/assignments', json={'revision': 1, 'config_revision': 2, 'assignments': []})
    assert r.status_code == 200  # Saving a visibly invalid draft is allowed.
    assert client.post(f'/api/rosters/{roster["id"]}/publish', json={'revision': 2, 'config_revision': 2}).status_code == 422


def test_bad_references_origin_and_unknown_group(client):
    r = client.put('/api/config/allocations', json={'revision': 1, 'record': {'id': 'bad', 'employee_id': 'missing', 'brand_process_id': 'b0_CS'}})
    assert r.status_code == 422
    assert client.get('/api/config').json()['revision'] == 1
    assert client.post('/api/demo-sessions', headers={'origin': 'https://wrong.example'}).status_code == 403
    assert client.put('/api/config/unknown', json={'revision': 1, 'record': {}}).status_code == 404


def test_confirm_boundary_history_and_validate_input(client):
    assert not client.get('/api/history/2027-03').json()['complete']
    assert client.put('/api/history/2027-03', json={'revision': 1, 'confirmed': True, 'worked': [{'employee_id': 'e0', 'date': '2027-03-01'}]}).status_code == 422
    assert client.get('/api/config').json()['revision'] == 1
    assert client.put('/api/history/2027-03', json={'revision': 1, 'confirmed': True, 'worked': []}).status_code == 200
    assert client.get('/api/history/2027-03').json()['complete']


def test_coverage_resolution_options_respect_leave_and_workspace(client):
    roster = generated(client)
    query = '?day=2026-11-15&brand_id=b0&process_id=CS'
    result = client.get(f'/api/rosters/{roster["id"]}/coverage-help'+query)
    assert result.status_code == 200
    assert {'e0', 'e30', 'e31'} <= {e['employee_id'] for e in result.json()['candidates']}
    assert client.put('/api/config/absences', json={'revision': 1, 'record': {'id': 'help_leave', 'employee_id': 'e0', 'start': '2026-11-15', 'end': '2026-11-15', 'kind': 'LEAVE'}}).status_code == 200
    options = client.get(f'/api/rosters/{roster["id"]}/coverage-help'+query).json()['candidates']
    assert next(e for e in options if e['employee_id'] == 'e0')['unavailable'] == 'LEAVE'
    assert client.get(f'/api/rosters/{roster["id"]}/coverage-help?day=2026-12-01&brand_id=b0&process_id=CS').status_code == 422


def test_republishing_previous_month_marks_dependent_roster_stale(client):
    import uuid
    from datetime import date
    from backend.app import db
    from backend.app.seed import witness
    from backend.app.scheduling.solver import solve_highs

    config = client.get('/api/config').json()
    config.pop('revision')
    history = witness(config, date(2026, 10, 26), date(2026, 10, 31))

    def store(month, result, prior):
        rid = uuid.uuid4().hex
        result.update(config=config, history=prior, history_complete=True)
        with client.app.state.engine.begin() as conn:
            conn.execute(db.rosters.insert().values(id=rid, workspace_id=client.cookies.get('roster_workspace'), month=month, revision=1, config_revision=1, status='DRAFT', data=result, created_at=db.now()))
        response = client.post(f'/api/rosters/{rid}/publish', json={'revision': 1, 'config_revision': 1})
        assert response.status_code == 200, response.text
        return rid

    nov = solve_highs(config, '2026-11', history)
    store('2026-11', nov, history)
    prior = [a for a in nov['assignments'] if '2026-11-25' <= a['date'] <= '2026-11-30']
    dec = solve_highs(config, '2026-12', prior)
    dec_id = store('2026-12', dec, prior)
    baseline = [a for a in nov['assignments'] if a['date'] <= '2026-11-30']
    baseline.append({'employee_id': 'e0', 'date': '2026-11-30', 'duty_id': 'd0', 'locked': True})
    repaired = solve_highs(config, '2026-11', history, baseline=baseline)
    store('2026-11', repaired, history)
    dependent = client.get(f'/api/rosters/{dec_id}').json()
    assert dependent['stale'] and dependent['history_changed']
    assert not client.post(f'/api/rosters/{dec_id}/validate').json()['valid']
    assert client.put('/api/history/2026-12', json={'revision': 1, 'confirmed': True, 'worked': []}).status_code == 409
