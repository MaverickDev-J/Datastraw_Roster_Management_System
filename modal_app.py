"""Deploy: uv run --locked --group deploy modal deploy --strategy recreate modal_app.py"""
from pathlib import Path

import modal

app = modal.App('datastraw-roster')
volume = modal.Volume.from_name('datastraw-roster-state-v2', create_if_missing=True, version=2)
image = modal.Image.debian_slim(python_version='3.12')

if modal.is_local():
    root = Path(__file__).resolve().parent
    if not (root / 'frontend/dist/index.html').is_file():
        raise RuntimeError('Build the frontend first: cd frontend; npm ci --legacy-peer-deps; npm run build')
    # Explicit source allowlist: private documents, credentials and the local
    # development database are never uploaded with the application.
    image = (
        image.uv_pip_install(requirements=[str(root / 'requirements.lock')], uv_version='0.11.16')
        .workdir('/app')
        .env({
            'DATABASE_URL': 'sqlite:////tmp/datastraw-roster/roster.sqlite3',
            'COOKIE_SECURE': 'true',
            'ROSTER_SOLVER': 'highs',
            'SOLVER_SECONDS': '10',
            'PYTHONPATH': '/app',
        })
        .add_local_file(root / 'backend/__init__.py', '/app/backend/__init__.py')
        .add_local_dir(root / 'backend/app', '/app/backend/app', ignore=['**/__pycache__/**', '**/*.pyc'])
        .add_local_dir(root / 'frontend/dist', '/app/frontend/dist')
        .add_local_file(root / 'modal_app.py', '/app/modal_app.py')
    )


@app.function(
    image=image,
    volumes={'/state': volume},
    cpu=2,
    memory=2048,
    min_containers=0,
    max_containers=1,
    buffer_containers=0,
    scaledown_window=120,
    timeout=180,
    include_source=False,
)
@modal.concurrent(max_inputs=32)
@modal.asgi_app(label='datastraw-roster')
def web():
    from backend.app.modal_storage import SnapshotStore, enable_persistence

    store = SnapshotStore(
        Path('/tmp/datastraw-roster/roster.sqlite3'),
        Path('/state/roster.sqlite3'),
        volume.commit,
    )
    store.restore()
    # Import only after restoring history, before the backend opens SQLite.
    from backend.app.main import app as api

    return enable_persistence(api, store)
