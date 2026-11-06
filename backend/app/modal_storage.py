"""Keep live SQLite on local disk and persist consistent snapshots on Modal."""
import asyncio
import logging
import os
import shutil
import sqlite3
import threading
from contextlib import asynccontextmanager, closing
from pathlib import Path

from fastapi.responses import JSONResponse


class SnapshotStore:
    def __init__(self, local: Path, snapshot: Path, commit):
        self.local = Path(local)
        self.snapshot = Path(snapshot)
        self.commit = commit
        self.lock = threading.Lock()
        self.connection = None
        self.saved_version = None

    def restore(self):
        self.local.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot.parent.mkdir(parents=True, exist_ok=True)
        if self.local.exists():
            raise RuntimeError('Restore requires a fresh local database path')
        if self.snapshot.exists():
            shutil.copyfile(self.snapshot, self.local)

    def persist(self):
        with self.lock:
            self.snapshot.parent.mkdir(parents=True, exist_ok=True)
            if self.connection is None:
                self.connection = sqlite3.connect(self.local, timeout=30, check_same_thread=False)
            version = self.connection.execute('PRAGMA data_version').fetchone()[0]
            if version == self.saved_version:
                return
            temporary = self.local.with_name('roster-backup.sqlite3')
            staged = self.snapshot.with_name('roster-staged.sqlite3')
            try:
                # SQLite's backup API includes committed WAL data. Copy only a
                # closed, standalone database file onto the distributed volume.
                with closing(sqlite3.connect(temporary)) as target:
                    self.connection.backup(target)
                    target.execute('PRAGMA journal_mode=DELETE')
                shutil.copyfile(temporary, staged)
                os.replace(staged, self.snapshot)
                self.commit()
                self.saved_version = version
            finally:
                temporary.unlink(missing_ok=True)

    def close(self):
        with self.lock:
            if self.connection is not None:
                self.connection.close()
                self.connection = None
                self.saved_version = None


def enable_persistence(app, store):
    """Acknowledge API results only after their committed state is durable."""
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with original_lifespan(application):
            await asyncio.to_thread(store.persist)
            try:
                yield
            finally:
                executor = getattr(application.state, 'solver_executor', None)
                if executor is not None:
                    await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)
                try:
                    await asyncio.to_thread(store.persist)
                finally:
                    store.close()

    app.router.lifespan_context = lifespan

    @app.middleware('http')
    async def persist_response(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith('/api/') and request.url.path != '/api/health' and response.status_code < 400:
            try:
                await asyncio.to_thread(store.persist)
            except Exception:
                logging.exception('Could not persist roster snapshot')
                return JSONResponse(status_code=503, content={'detail': 'Persistent storage is temporarily unavailable. Reload the workspace before retrying.'})
        return response

    return app
