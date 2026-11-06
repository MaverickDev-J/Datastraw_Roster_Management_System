"""Relational configuration tables with versioned immutable roster snapshots."""
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import JSON, Column, ForeignKey, Integer, MetaData, String, Table, create_engine, event

Path('data').mkdir(exist_ok=True)
metadata = MetaData()
workspace = Table('workspaces', metadata, Column('id', String, primary_key=True), Column('revision', Integer, nullable=False), Column('data', JSON, nullable=False), Column('created_at', String, nullable=False))


def entity(name, **references):
    return Table(name, metadata,
                 Column('key', String, primary_key=True),
                 Column('workspace_id', String, ForeignKey('workspaces.id'), nullable=False, index=True),
                 Column('id', String, nullable=False), Column('data', JSON, nullable=False),
                 *[Column(k, String, ForeignKey(f'{v}.key'), nullable=False) for k, v in references.items()])


tables = {}
tables['pods'] = entity('pods')
tables['employees'] = entity('employees', pod_id='pods')
tables['brands'] = entity('brands')
tables['processes'] = entity('processes')
tables['brand_processes'] = entity('brand_processes', brand_id='brands', process_id='processes')
tables['allocations'] = entity('allocations', employee_id='employees', brand_process_id='brand_processes')
tables['duties'] = entity('duties', brand_id='brands')
tables['absences'] = entity('absences', employee_id='employees')
tables['holidays'] = entity('holidays')
tables['overrides'] = entity('overrides', brand_process_id='brand_processes')
rosters = Table('rosters', metadata,
                Column('id', String, primary_key=True), Column('workspace_id', String, ForeignKey('workspaces.id'), index=True),
                Column('month', String), Column('revision', Integer), Column('config_revision', Integer),
                Column('status', String), Column('data', JSON), Column('created_at', String))
jobs = Table('jobs', metadata, Column('id', String, primary_key=True), Column('workspace_id', String, ForeignKey('workspaces.id'), index=True),
             Column('status', String), Column('data', JSON), Column('created_at', String))
audit = Table('audit_events', metadata, Column('id', Integer, primary_key=True), Column('workspace_id', String, ForeignKey('workspaces.id'), index=True),
              Column('action', String), Column('data', JSON), Column('created_at', String))


def now():
    return datetime.now(timezone.utc).isoformat()


def make_engine(url=None):
    url = url or os.getenv('DATABASE_URL', 'sqlite:///./data/roster.db')
    engine = create_engine(url, connect_args={'check_same_thread': False, 'timeout': 15} if url.startswith('sqlite') else {})
    if url.startswith('sqlite'):
        @event.listens_for(engine, 'connect')
        def sqlite_settings(connection, _):
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA journal_mode=WAL')
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(jobs.update().where(jobs.c.status.in_(['QUEUED', 'RUNNING'])).values(status='INTERRUPTED'))
    return engine


def load_config(connection, wid):
    return {name: list(connection.execute(table.select().where(table.c.workspace_id == wid)).mappings()) for name, table in tables.items()}


def config_data(connection, wid):
    return {name: [r['data'] for r in records] for name, records in load_config(connection, wid).items()}


def save_config(connection, wid, config):
    # Parent records are retained so referenced assignments are never orphaned.
    for name, table in tables.items():
        refs = {c.name for c in table.c if c.foreign_keys} - {'workspace_id'}
        for row in config[name]:
            key = f'{wid}:{row["id"]}'
            values = {'key': key, 'workspace_id': wid, 'id': row['id'], 'data': row,
                      **{ref: f'{wid}:{row[ref]}' for ref in refs}}
            exists = connection.execute(table.select().where(table.c.key == key)).first()
            if exists:
                connection.execute(table.update().where(table.c.key == key).values(**values))
            else:
                connection.execute(table.insert().values(**values))


def log(connection, wid, action, data):
    connection.execute(audit.insert().values(workspace_id=wid, action=action, data=data, created_at=now()))
