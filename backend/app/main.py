import copy
import csv
import io
import secrets
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from . import db
from .schemas import SCHEMAS, check_config
from .scheduling.calendar import month_dates
from .scheduling.calendar import demand, eligible, unavailable
from .scheduling.solver import solve
from .scheduling.validator import validate
from .seed import seed_config, witness


class Generate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    month: str = Field(pattern=r'^20\d{2}-(0[1-9]|1[0-2])$')
    config_revision: int
    base_id: str | None = None
    base_revision: int | None = None


class ConfigEdit(BaseModel):
    revision: int
    record: dict


class Assignment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    employee_id: str
    date: date
    duty_id: str
    locked: bool = False


class Edit(BaseModel):
    revision: int
    config_revision: int
    assignments: list[Assignment] = Field(max_length=5000)


class Publish(BaseModel):
    revision: int
    config_revision: int


class HistoryEntry(BaseModel):
    employee_id: str
    date: date


class HistoryEdit(BaseModel):
    revision: int
    confirmed: bool
    worked: list[HistoryEntry] = Field(max_length=5000)


def create_app(database_url=None):
    app = FastAPI(title='Roster — Datastraw', version='0.1.0')
    engine = db.make_engine(database_url)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='roster-solver')
    slots = threading.BoundedSemaphore(4)
    app.state.engine = engine
    app.state.solver_executor = executor

    @app.middleware('http')
    async def origin_guard(request: Request, call_next):
        origin = request.headers.get('origin')
        if request.method not in ['GET', 'HEAD', 'OPTIONS'] and origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
            return Response('Cross-origin mutation rejected', status_code=403)
        return await call_next(request)

    def wid(request):
        token = request.cookies.get('roster_workspace')
        with engine.connect() as conn:
            row = conn.execute(select(db.workspace).where(db.workspace.c.id == token)).mappings().first()
        if not row:
            raise HTTPException(401, 'Start a demo workspace first')
        return row['id']

    def get_roster(conn, workspace_id, roster_id):
        row = conn.execute(select(db.rosters).where(db.rosters.c.id == roster_id, db.rosters.c.workspace_id == workspace_id)).mappings().first()
        if not row:
            raise HTTPException(404, 'Roster not found')
        return dict(row)

    def get_workspace(conn, workspace_id):
        return conn.execute(select(db.workspace).where(db.workspace.c.id == workspace_id)).mappings().one()

    def get_history(conn, workspace_id, month):
        first, _ = month_dates(month)
        prior = first - timedelta(days=6)
        meta = get_workspace(conn, workspace_id)['data']
        previous_month = (first - timedelta(days=1)).strftime('%Y-%m')
        published = conn.execute(select(db.rosters).where(db.rosters.c.workspace_id == workspace_id, db.rosters.c.month == previous_month, db.rosters.c.status == 'PUBLISHED')).mappings().first()
        if published:
            return [a for a in published['data']['assignments'] if prior.isoformat() <= a['date'] < first.isoformat()], True
        if month in meta.get('boundary_history', {}):
            return meta['boundary_history'][month], True
        if month == '2026-11':
            return meta['history'], True
        return [], False

    def history_changed(conn, row):
        current, complete = get_history(conn, row['workspace_id'], row['month'])
        fingerprint = lambda rows: sorted({(a['employee_id'], a['date']) for a in rows})
        return complete != row['data'].get('history_complete', False) or fingerprint(current) != fingerprint(row['data'].get('history', []))

    def serialise(conn, row):
        result = {k: v for k, v in row.items() if k != 'workspace_id'}
        result['history_changed'] = history_changed(conn, row)
        result['stale'] = row['config_revision'] != get_workspace(conn, row['workspace_id'])['revision'] or result['history_changed']
        return result

    @app.get('/api/history/{month}')
    def boundary_history(month: str, request: Request):
        try:
            first, _ = month_dates(month)
        except (ValueError, TypeError):
            raise HTTPException(422, 'Invalid month')
        workspace_id = wid(request)
        with engine.connect() as conn:
            history, complete = get_history(conn, workspace_id, month)
        return {'complete': complete, 'worked': history, 'dates': [(first - timedelta(days=n)).isoformat() for n in range(6, 0, -1)]}

    @app.put('/api/history/{month}')
    def confirm_history(month: str, body: HistoryEdit, request: Request):
        try:
            first, _ = month_dates(month)
        except (ValueError, TypeError):
            raise HTTPException(422, 'Invalid month')
        if not body.confirmed:
            raise HTTPException(422, 'Confirm that unchecked employee dates were non-working days')
        workspace_id = wid(request)
        with engine.begin() as conn:
            guard = conn.execute(db.workspace.update().where(db.workspace.c.id == workspace_id, db.workspace.c.revision == body.revision).values(revision=body.revision + 1))
            if guard.rowcount != 1:
                raise HTTPException(409, 'Configuration changed. Reload before saving.')
            previous = (first - timedelta(days=1)).strftime('%Y-%m')
            if conn.execute(select(db.rosters.c.id).where(db.rosters.c.workspace_id == workspace_id, db.rosters.c.month == previous, db.rosters.c.status == 'PUBLISHED')).first():
                raise HTTPException(409, 'History comes from the published preceding month. Repair that roster instead.')
            employees = {e['id'] for e in db.config_data(conn, workspace_id)['employees']}
            keys = {(a.employee_id, a.date) for a in body.worked}
            if len(keys) != len(body.worked) or any(e not in employees or not first - timedelta(days=6) <= d < first for e, d in keys):
                raise HTTPException(422, 'History must contain unique employee dates within the preceding six days')
            meta = copy.deepcopy(get_workspace(conn, workspace_id)['data'])
            meta.setdefault('boundary_history', {})[month] = [a.model_dump(mode='json') for a in body.worked]
            conn.execute(db.workspace.update().where(db.workspace.c.id == workspace_id).values(data=meta))
            db.log(conn, workspace_id, 'HISTORY_CONFIRMED', {'month': month})
        return {'revision': body.revision + 1}

    @app.get('/api/health')
    def health():
        with engine.connect() as conn:
            conn.execute(select(1))
        return {'status': 'ok'}

    @app.post('/api/demo-sessions')
    def session(request: Request, response: Response):
        token = request.cookies.get('roster_workspace')
        with engine.begin() as conn:
            exists = conn.execute(select(db.workspace).where(db.workspace.c.id == token)).first() if token else None
            if not exists:
                token = secrets.token_urlsafe(32)
                config = seed_config()
                history = witness(config, date(2026, 10, 26), date(2026, 10, 31))
                conn.execute(db.workspace.insert().values(id=token, revision=1, data={'history': history}, created_at=db.now()))
                db.save_config(conn, token, config)
        import os
        response.set_cookie('roster_workspace', token, httponly=True, samesite='lax', secure=os.getenv('COOKIE_SECURE', 'false').lower() == 'true', max_age=7 * 86400)
        return {'ready': True, 'demo_month': '2026-11'}

    @app.get('/api/config')
    def config(request: Request):
        workspace_id = wid(request)
        with engine.connect() as conn:
            return {'revision': get_workspace(conn, workspace_id)['revision'], **db.config_data(conn, workspace_id)}

    @app.put('/api/config/{group}')
    def save_record(group: str, body: ConfigEdit, request: Request):
        workspace_id = wid(request)
        if group not in SCHEMAS:
            raise HTTPException(404, 'Unknown configuration group')
        try:
            record = SCHEMAS[group].model_validate(body.record).model_dump(mode='json')
            with engine.begin() as conn:
                updated = conn.execute(db.workspace.update().where(db.workspace.c.id == workspace_id, db.workspace.c.revision == body.revision).values(revision=body.revision + 1))
                if updated.rowcount != 1:
                    raise HTTPException(409, 'Configuration changed. Reload before saving.')
                data = db.config_data(conn, workspace_id)
                data[group] = [r for r in data[group] if r['id'] != record['id']] + [record]
                data = check_config(data)
                db.save_config(conn, workspace_id, data)
                db.log(conn, workspace_id, 'CONFIG_EDIT', {'group': group, 'record': record})
        except (ValueError, ValidationError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return {'revision': body.revision + 1}

    def run_job(job_id, workspace_id, body, snapshot, history, complete, baseline):
        try:
            with engine.begin() as conn:
                conn.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(status='RUNNING'))
            result = solve(snapshot, body.month, history, complete, baseline)
            result.update({'config': snapshot, 'history': history, 'history_complete': complete, 'parent_id': body.base_id})
            roster_id = uuid.uuid4().hex
            with engine.begin() as conn:
                conn.execute(db.rosters.insert().values(id=roster_id, workspace_id=workspace_id, month=body.month, revision=1, config_revision=body.config_revision, status='DRAFT', data=result, created_at=db.now()))
                conn.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(status='COMPLETED', data={'roster_id': roster_id}))
                db.log(conn, workspace_id, 'GENERATED', {'roster_id': roster_id, 'parent_id': body.base_id, 'seconds': result['seconds']})
        except Exception:
            traceback.print_exc()
            with engine.begin() as conn:
                conn.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(status='FAILED', data={'error': 'Scheduling failed. Existing rosters are unchanged; check server logs.'}))
        finally:
            slots.release()

    @app.post('/api/rosters/generate', status_code=202)
    def generate(body: Generate, request: Request):
        workspace_id = wid(request)
        with engine.begin() as conn:
            if get_workspace(conn, workspace_id)['revision'] != body.config_revision:
                raise HTTPException(409, 'Configuration changed. Reload before generating.')
            existing = conn.execute(select(db.jobs).where(db.jobs.c.workspace_id == workspace_id, db.jobs.c.status.in_(['QUEUED', 'RUNNING']))).mappings().first()
            if existing:
                return {'job_id': existing['id']}
            baseline = []
            if body.base_id:
                base = get_roster(conn, workspace_id, body.base_id)
                if base['month'] != body.month or base['revision'] != body.base_revision:
                    raise HTTPException(409, 'Repair base changed or belongs to a different month')
                baseline = base['data']['assignments']
            snapshot = db.config_data(conn, workspace_id)
            history, complete = get_history(conn, workspace_id, body.month)
            if not slots.acquire(blocking=False):
                raise HTTPException(429, 'Scheduling queue is full. Try again shortly.')
            job_id = uuid.uuid4().hex
            conn.execute(db.jobs.insert().values(id=job_id, workspace_id=workspace_id, status='QUEUED', data=body.model_dump(), created_at=db.now()))
        executor.submit(run_job, job_id, workspace_id, body, snapshot, history, complete, baseline)
        return {'job_id': job_id}

    @app.get('/api/jobs/{job_id}')
    def job(job_id: str, request: Request):
        workspace_id = wid(request)
        with engine.connect() as conn:
            row = conn.execute(select(db.jobs).where(db.jobs.c.id == job_id, db.jobs.c.workspace_id == workspace_id)).mappings().first()
            if not row:
                raise HTTPException(404, 'Job not found')
            return {'id': row['id'], 'status': row['status'], **row['data']}

    @app.get('/api/rosters')
    def list_rosters(request: Request):
        workspace_id = wid(request)
        with engine.connect() as conn:
            rows = conn.execute(select(db.rosters).where(db.rosters.c.workspace_id == workspace_id).order_by(db.rosters.c.created_at.desc())).mappings()
            return [{'id': r['id'], 'month': r['month'], 'status': r['status'], 'created_at': r['created_at']} for r in rows]

    @app.get('/api/rosters/{roster_id}')
    def roster(roster_id: str, request: Request):
        workspace_id = wid(request)
        with engine.connect() as conn:
            return serialise(conn, get_roster(conn, workspace_id, roster_id))

    @app.post('/api/rosters/{roster_id}/validate')
    def revalidate(roster_id: str, request: Request):
        workspace_id = wid(request)
        with engine.connect() as conn:
            row = get_roster(conn, workspace_id, roster_id)
            current = db.config_data(conn, workspace_id)
            history, complete = get_history(conn, workspace_id, row['month'])
            return {'config_revision': get_workspace(conn, workspace_id)['revision'], **validate(current, row['month'], row['data']['assignments'], history, complete)}

    @app.get('/api/rosters/{roster_id}/coverage-help')
    def coverage_help(roster_id: str, day: date, brand_id: str, process_id: str, request: Request):
        workspace_id = wid(request)
        with engine.connect() as conn:
            row = get_roster(conn, workspace_id, roster_id)
            config = db.config_data(conn, workspace_id)
            history, complete = get_history(conn, workspace_id, row['month'])
        first, last = month_dates(row['month'])
        if not first <= day <= last:
            raise HTTPException(422, 'Select a date in this roster month')
        bp = next((b for b in config['brand_processes'] if b['brand_id'] == brand_id and b['process_id'] == process_id), None)
        if not bp:
            raise HTTPException(404, 'Brand process not found')
        assignments = row['data']['assignments']
        dem = demand(config, day)
        candidates = []
        templates = [t for t in config['duties'] if t['brand_id'] == brand_id and process_id in t['process_ids']]
        for employee in config['employees']:
            allocations = [a for a in config['allocations'] if a['employee_id'] == employee['id'] and a['brand_process_id'] == bp['id'] and a['approved'] and a['start'] <= day.isoformat() and (not a['end'] or a['end'] >= day.isoformat())]
            if not allocations:
                continue
            blocked = unavailable(config, employee, day)
            options = []
            for template in templates:
                if not eligible(config, employee, template, day, dem):
                    continue
                proposal = [a for a in assignments if not (a['employee_id'] == employee['id'] and a['date'] == day.isoformat() and a['duty_id'] == template['id'])]
                proposal.append({'employee_id': employee['id'], 'date': day.isoformat(), 'duty_id': template['id']})
                report = validate(config, row['month'], proposal, history, complete)
                blockers = [i['reason'] for i in report['issues'] if i.get('employee_id') == employee['id']]
                options.append({'duty_id': template['id'], 'blockers': sorted(set(blockers)), 'whole_roster_valid': report['valid']})
            candidates.append({'employee_id': employee['id'], 'name': employee['name'], 'role': 'PRIMARY' if any(a['role'] == 'PRIMARY' for a in allocations) else 'BACKUP', 'unavailable': blocked, 'options': options})
        return {'candidates': candidates, 'history_complete': complete, 'note': 'Options check adding one duty without moving other work. Find a repair checks changes across the full roster.'}

    def edited(conn, workspace_id, roster_id, body):
        row = get_roster(conn, workspace_id, roster_id)
        if row['revision'] != body.revision or get_workspace(conn, workspace_id)['revision'] != body.config_revision:
            raise HTTPException(409, 'Roster or configuration changed. Reload before editing.')
        config = db.config_data(conn, workspace_id)
        history, complete = get_history(conn, workspace_id, row['month'])
        assignments = [a.model_dump(mode='json') for a in body.assignments]
        report = validate(config, row['month'], assignments, history, complete)
        return row, config, assignments, report

    @app.post('/api/rosters/{roster_id}/edit-preview')
    def preview(roster_id: str, body: Edit, request: Request):
        workspace_id = wid(request)
        with engine.connect() as conn:
            return edited(conn, workspace_id, roster_id, body)[3]

    @app.patch('/api/rosters/{roster_id}/assignments')
    def edit(roster_id: str, body: Edit, request: Request):
        workspace_id = wid(request)
        with engine.begin() as conn:
            row, config, assignments, report = edited(conn, workspace_id, roster_id, body)
            if row['status'] != 'DRAFT':
                raise HTTPException(409, 'Published versions are immutable. Create a repair draft first.')
            prohibited = [i for i in report['issues'] if i['code'] not in ['COVERAGE_GAP', 'BOUNDARY_HISTORY_MISSING', 'REST_VIOLATION']]
            if prohibited:
                raise HTTPException(422, {'message': 'Assignment violates mandatory rules.', 'issues': prohibited})
            data = copy.deepcopy(row['data'])
            history, complete = get_history(conn, workspace_id, row['month'])
            data.update(assignments=assignments, validation=report, config=config, history=history, history_complete=complete)
            update = conn.execute(db.rosters.update().where(db.rosters.c.id == roster_id, db.rosters.c.revision == body.revision).values(data=data, revision=body.revision + 1, config_revision=body.config_revision))
            if update.rowcount != 1:
                raise HTTPException(409, 'Roster changed')
            db.log(conn, workspace_id, 'MANUAL_EDIT', {'roster_id': roster_id, 'revision': body.revision + 1})
        return {'revision': body.revision + 1, 'validation': report}

    @app.post('/api/rosters/{roster_id}/publish')
    def publish(roster_id: str, body: Publish, request: Request):
        workspace_id = wid(request)
        with engine.begin() as conn:
            # Compare-and-set workspace revision also serialises publication/config changes.
            guard = conn.execute(db.workspace.update().where(db.workspace.c.id == workspace_id, db.workspace.c.revision == body.config_revision).values(revision=body.config_revision))
            if guard.rowcount != 1:
                raise HTTPException(409, 'Configuration changed')
            row = get_roster(conn, workspace_id, roster_id)
            if row['revision'] != body.revision or row['config_revision'] != body.config_revision or row['status'] != 'DRAFT':
                raise HTTPException(409, 'Only a current draft can be published')
            history, complete = get_history(conn, workspace_id, row['month'])
            report = validate(db.config_data(conn, workspace_id), row['month'], row['data']['assignments'], history, complete)
            if not report['valid']:
                raise HTTPException(422, {'message': 'Resolve mandatory conflicts before publication.', 'issues': report['issues']})
            conn.execute(db.rosters.update().where(db.rosters.c.workspace_id == workspace_id, db.rosters.c.month == row['month'], db.rosters.c.status == 'PUBLISHED').values(status='SUPERSEDED'))
            data = copy.deepcopy(row['data'])
            data.update(validation=report, history=history, history_complete=complete)
            conn.execute(db.rosters.update().where(db.rosters.c.id == roster_id).values(status='PUBLISHED', revision=body.revision + 1, data=data))
            db.log(conn, workspace_id, 'PUBLISHED', {'roster_id': roster_id})
        return {'published': True}

    @app.get('/api/rosters/{roster_id}/export.csv')
    def export(roster_id: str, request: Request):
        workspace_id = wid(request)
        with engine.connect() as conn:
            row = get_roster(conn, workspace_id, roster_id)
        cfg = row['data']['config']
        employees = {e['id']: e['name'] for e in cfg['employees']}
        duties = {t['id']: t for t in cfg['duties']}
        brands = {b['id']: b['name'] for b in cfg['brands']}
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(['Date', 'Employee', 'Brand', 'Processes', 'Blocks', 'Locked'])
        def safe(value):
            return "'" + value if value.startswith(('=', '+', '-', '@')) else value
        for a in row['data']['assignments']:
            if a['date'][:7] == row['month']:
                duty = duties[a['duty_id']]
                writer.writerow([a['date'], safe(employees[a['employee_id']]), safe(brands[duty['brand_id']]), ' + '.join(duty['process_ids']), ' + '.join(duty['blocks']), a.get('locked', False)])
        return StreamingResponse(iter([stream.getvalue()]), media_type='text/csv', headers={'Content-Disposition': f'attachment; filename="roster-{row["month"]}.csv"'})

    dist = Path(__file__).resolve().parents[2] / 'frontend' / 'dist'
    if dist.exists():
        app.mount('/assets', StaticFiles(directory=dist / 'assets'), name='assets')

        @app.get('/{path:path}')
        def frontend(path: str):
            if path.startswith('api/'):
                raise HTTPException(404, 'API route not found')
            return FileResponse(dist / 'index.html')

    return app


app = create_app()
