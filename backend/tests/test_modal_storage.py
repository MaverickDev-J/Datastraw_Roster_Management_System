import sqlite3
from contextlib import closing

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.modal_storage import SnapshotStore, enable_persistence


def test_snapshot_includes_wal_and_survives_fresh_local_disk(tmp_path):
    local = tmp_path / 'live.sqlite3'
    persisted = tmp_path / 'state' / 'snapshot.sqlite3'
    commits = []
    with closing(sqlite3.connect(local)) as source:
        source.execute('PRAGMA journal_mode=WAL')
        source.execute('CREATE TABLE example (value TEXT)')
        source.execute("INSERT INTO example VALUES ('first')")
        source.commit()
        store = SnapshotStore(local, persisted, lambda: commits.append(True))
        store.persist()
        store.persist()
        assert len(commits) == 1  # Read-only requests do not rewrite storage.
        source.execute("INSERT INTO example VALUES ('second')")
        source.commit()
        store.persist()
        assert len(commits) == 2
        store.close()
    restored = tmp_path / 'fresh' / 'live.sqlite3'
    replacement = SnapshotStore(restored, persisted, lambda: None)
    replacement.restore()
    with closing(sqlite3.connect(restored)) as connection:
        assert connection.execute('SELECT value FROM example').fetchall() == [('first',), ('second',)]
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert connection.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'


def test_workspace_cookie_and_configuration_survive_container_replacement(tmp_path):
    snapshot = tmp_path / 'state' / 'snapshot.sqlite3'
    cookies = None
    for index in range(2):
        local = tmp_path / f'container{index}' / 'live.sqlite3'
        store = SnapshotStore(local, snapshot, lambda: None)
        store.restore()
        app = enable_persistence(create_app('sqlite:///' + local.as_posix()), store)
        with TestClient(app) as client:
            if cookies is None:
                assert client.post('/api/demo-sessions').status_code == 200
                config = client.get('/api/config').json()
                employee = dict(config['employees'][0], name='Persisted Reviewer')
                assert client.put('/api/config/employees', json={'revision': config['revision'], 'record': employee}).status_code == 200
                cookies = dict(client.cookies)
            else:
                client.cookies.update(cookies)
                config = client.get('/api/config').json()
                assert any(e['name'] == 'Persisted Reviewer' for e in config['employees'])
        app.state.engine.dispose()


def test_persistence_failure_is_not_acknowledged_as_success(tmp_path):
    unavailable = False

    def commit():
        if unavailable:
            raise OSError('Simulated storage outage')

    local = tmp_path / 'live.sqlite3'
    store = SnapshotStore(local, tmp_path / 'state' / 'snapshot.sqlite3', commit)
    app = enable_persistence(create_app('sqlite:///' + local.as_posix()), store)
    with TestClient(app) as client:
        assert client.post('/api/demo-sessions').status_code == 200
        config = client.get('/api/config').json()
        unavailable = True
        response = client.put('/api/config/employees', json={'revision': config['revision'], 'record': dict(config['employees'][0], name='Pending persistence')})
        assert response.status_code == 503
        unavailable = False
        assert client.get('/api/config').status_code == 200
    app.state.engine.dispose()
