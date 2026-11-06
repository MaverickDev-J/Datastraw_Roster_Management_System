import copy
from datetime import date, timedelta

import pytest

from backend.app.schemas import check_config
from backend.app.seed import seed_config, witness
from backend.app.scheduling.calendar import demand, month_dates
from backend.app.scheduling.solver import solve_highs
from backend.app.scheduling.validator import validate


@pytest.fixture
def fixture():
    c = seed_config()
    h = witness(c, date(2026, 10, 26), date(2026, 10, 31))
    return c, h


def test_seed_distribution_and_witness(fixture):
    config, history = fixture
    owners = {b['id']: set() for b in config['brands']}
    bps = {b['id']: b for b in config['brand_processes']}
    for a in config['allocations']:
        if a['role'] == 'PRIMARY':
            owners[bps[a['brand_process_id']]['brand_id']].add(a['employee_id'])
    assert len(config['employees']) == 32
    assert sum(len(v) == 1 for v in owners.values()) == 20
    assert sum(len(v) == 2 for v in owners.values()) == 5
    rows = witness(config, date(2026, 11, 1), date(2026, 12, 6))
    assert validate(config, '2026-11', rows, history)['valid']


def test_solver_baseline_and_repair(fixture):
    config, history = fixture
    baseline = solve_highs(config, '2026-11', history)
    assert baseline['validation']['valid']
    assert baseline['strict_status'] == 'OPTIMAL'
    changed = copy.deepcopy(config)
    changed['absences'].append({'id': 'new_leave', 'employee_id': 'e0', 'start': '2026-11-15', 'end': '2026-11-15', 'kind': 'LEAVE', 'approved': True})
    broken = validate(changed, '2026-11', baseline['assignments'], history)
    assert any(i['code'] == 'UNAVAILABLE_ASSIGNMENT' for i in broken['issues'])
    repaired = solve_highs(changed, '2026-11', history, baseline=baseline['assignments'])
    assert repaired['validation']['valid'], repaired['validation']['issues'][:3]
    assert not any(a['employee_id'] == 'e0' and a['date'] == '2026-11-15' for a in repaired['assignments'])
    assert repaired['changes']['added']


def test_no_backup_is_honestly_infeasible(fixture):
    config, history = fixture
    config['absences'].append({'id': 'no_cover', 'employee_id': 'e8', 'start': '2026-11-16', 'end': '2026-11-16', 'kind': 'LEAVE', 'approved': True})
    result = solve_highs(config, '2026-11', history)
    assert result['strict_status'] == 'INFEASIBLE'
    assert result['diagnostic']
    assert not result['validation']['valid']
    assert any(i['code'] == 'COVERAGE_GAP' and i['brand_id'] == 'b8' for i in result['validation']['issues'])


def test_calendars_override_and_leap_year(fixture):
    c, _ = fixture
    sunday = demand(c, date(2026, 11, 15))
    assert sunday['b0', 'CS', 'AM'] == 1
    assert sunday['b0', 'NDR', 'AM'] == 0
    assert sunday['b8', 'CS', 'AM'] == 0
    holiday = demand(c, date(2026, 11, 12))
    assert holiday['b0', 'CS', 'AM'] == 1
    assert holiday['b4', 'CS', 'AM'] == 0
    c['overrides'].append({'id': 'open', 'brand_process_id': 'b8_CS', 'date': '2026-11-15', 'block': 'AM', 'minimum': 2})
    assert demand(c, date(2026, 11, 15))['b8', 'CS', 'AM'] == 2
    assert month_dates('2028-02')[1].day == 29


def test_duplicate_cannot_create_distinct_coverage(fixture):
    c, h = fixture
    rows = witness(c, date(2026, 11, 1), date(2026, 12, 6))
    victim = next(a for a in rows if a['employee_id'] == 'e29' and a['date'] == '2026-11-16')
    rows.remove(victim)
    same = next(a for a in rows if a['employee_id'] == 'e28' and a['date'] == '2026-11-16')
    rows.append(copy.deepcopy(same))
    report = validate(c, '2026-11', rows, h)
    assert {'DUPLICATE_ASSIGNMENT', 'OVERLAP', 'DAILY_CAP', 'COVERAGE_GAP'} <= {i['code'] for i in report['issues']}
    cs = next(r for r in report['coverage'] if r['brand_id'] == 'b24' and r['process_id'] == 'CS' and r['date'] == '2026-11-16' and r['block'] == 'AM')
    assert cs['actual'] == 1 and cs['required'] == 2


def test_cross_boundary_rest_and_missing_history(fixture):
    c, h = fixture
    rows = witness(c, date(2026, 11, 1), date(2026, 12, 6))
    rows.append({'employee_id': 'e8', 'date': '2026-11-01', 'duty_id': 'd8'})
    report = validate(c, '2026-11', rows, h, False)
    assert any(i['code'] == 'BOUNDARY_HISTORY_MISSING' for i in report['issues'])
    assert any(i['code'] == 'REST_VIOLATION' and i['employee_id'] == 'e8' for i in report['issues'])


def test_config_rejects_bad_references_and_overrides(fixture):
    c, _ = fixture
    c['employees'][0]['pod_id'] = 'missing'
    with pytest.raises(ValueError, match='pod'):
        check_config(c)


def test_absent_lock_cannot_be_silently_unlocked(fixture):
    c, h = fixture
    baseline = witness(c, date(2026, 11, 1), date(2026, 12, 6))
    next(a for a in baseline if a['employee_id'] == 'e0' and a['date'] == '2026-11-15')['locked'] = True
    c['absences'].append({'id': 'locked_leave', 'employee_id': 'e0', 'start': '2026-11-15', 'end': '2026-11-15', 'kind': 'LEAVE', 'approved': True})
    result = solve_highs(c, '2026-11', h, baseline=baseline)
    assert not result['validation']['valid']
    assert result['validation']['issues'][0]['code'] == 'LOCK_CONFLICT'


def test_approved_cross_brand_split_and_overlap(fixture):
    c, h = fixture
    # Replace two low-volume full-day brands with disjoint block demand.
    for bp in c['brand_processes']:
        if bp['brand_id'] in ['b8', 'b9']:
            bp['blocks'] = ['AM'] if bp['brand_id'] == 'b8' else ['PM']
    for t in c['duties']:
        if t['brand_id'] in ['b8', 'b9']:
            t['blocks'] = ['AM'] if t['brand_id'] == 'b8' else ['PM']
    for p in ['CS', 'NDR']:
        c['allocations'].append({'id': 'split_'+p, 'employee_id': 'e8', 'brand_process_id': 'b9_'+p, 'role': 'BACKUP', 'approved': True, 'start': '2020-01-01', 'end': None})
    rows = witness(c, date(2026, 11, 1), date(2026, 12, 6))
    next(a for a in rows if a['employee_id'] == 'e9' and a['date'] == '2026-11-16')['employee_id'] = 'e8'
    assert validate(c, '2026-11', rows, h)['valid']
    next(t for t in c['duties'] if t['id'] == 'd9')['blocks'] = ['AM', 'PM']
    assert any(i['code'] == 'OVERLAP' for i in validate(c, '2026-11', rows, h)['issues'])


def test_holiday_exemption_blocks_otherwise_open_brand(fixture):
    c, h = fixture
    rows = witness(c, date(2026, 11, 1), date(2026, 12, 6))
    c['employees'][0]['holiday_exempt'] = True
    report = validate(c, '2026-11', rows, h)
    assert any(i['code'] == 'UNAVAILABLE_ASSIGNMENT' and i['date'] == '2026-11-12' and i['employee_id'] == 'e0' for i in report['issues'])


def test_future_shortage_is_explicit_without_invalidating_current_month(fixture):
    c, h = fixture
    c['absences'].append({'id': 'future_leave', 'employee_id': 'e8', 'start': '2026-12-01', 'end': '2026-12-01', 'kind': 'LEAVE', 'approved': True})
    result = solve_highs(c, '2026-11', h)
    assert result['strict_status'] == 'INFEASIBLE'
    assert result['validation']['valid']
    assert any(i['date'] == '2026-12-01' and i['brand_id'] == 'b8' for i in result['validation']['lookahead_issues'])


def boundary_competition_config():
    return check_config({
        'pods': [{'id': 'pod', 'name': 'Pod'}],
        'employees': [{'id': 'e', 'name': 'Employee', 'pod_id': 'pod', 'preferred_off': 0}],
        'brands': [{'id': 'b', 'name': 'Brand', 'calendar': 'EVERY_DAY'}],
        'processes': [{'id': 'p', 'name': 'Support'}],
        'brand_processes': [{'id': 'bp', 'brand_id': 'b', 'process_id': 'p', 'minimum': 0}],
        'allocations': [{'id': 'a', 'employee_id': 'e', 'brand_process_id': 'bp'}],
        'duties': [{'id': 'd', 'brand_id': 'b', 'name': 'Duty', 'process_ids': ['p']}],
        'overrides': [{'id': f'o{n}{block}', 'brand_process_id': 'bp',
                       'date': (date(2026, 11, 30) + timedelta(days=n)).isoformat(),
                       'block': block, 'minimum': 1} for n in range(7) for block in ['AM', 'PM']],
    })


@pytest.mark.parametrize('repair', [False, True])
def test_current_month_coverage_dominates_lookahead_and_preferences(repair):
    config = boundary_competition_config()
    baseline = [{'employee_id': 'e', 'date': f'2026-12-0{n}', 'duty_id': 'd'} for n in range(1, 7)] if repair else None
    result = solve_highs(config, '2026-11', [], True, baseline)
    assert result['strict_status'] == 'INFEASIBLE'
    assert result['diagnostic'] and result['validation']['valid']
    assert any(a['date'] == '2026-11-30' for a in result['assignments'])
    assert len(result['validation']['lookahead_issues']) == 2
    assert any(w['date'] == '2026-11-30' for w in result['validation']['warnings'])


def test_current_month_priority_never_overrides_approved_leave():
    config = boundary_competition_config()
    config['absences'].append({'id': 'leave', 'employee_id': 'e', 'start': '2026-11-30', 'end': '2026-11-30', 'kind': 'LEAVE', 'approved': True})
    result = solve_highs(config, '2026-11', [], True)
    assert not result['validation']['valid']
    assert not any(a['date'] == '2026-11-30' for a in result['assignments'])


def test_overlapping_worker_cannot_produce_green_coverage(fixture):
    config, history = fixture
    for process in ['CS', 'NDR']:
        config['allocations'].append({'id': 'audit_' + process, 'employee_id': 'e8', 'brand_process_id': 'b9_' + process, 'role': 'BACKUP', 'approved': True, 'start': '2020-01-01', 'end': None})
    rows = witness(config, date(2026, 11, 1), date(2026, 12, 6))
    next(a for a in rows if a['employee_id'] == 'e9' and a['date'] == '2026-11-16')['employee_id'] = 'e8'
    report = validate(config, '2026-11', rows, history)
    assert not report['valid']
    assert report['covered_blocks'] < report['required_blocks']
    for brand in ['b8', 'b9']:
        cell = next(r for r in report['coverage'] if r['brand_id'] == brand and r['process_id'] == 'CS' and r['date'] == '2026-11-16' and r['block'] == 'AM')
        assert cell['actual'] == 1 and cell['safe_actual'] == 0
        assert cell['status'] == 'CONFLICT' and cell['conflicted_employees'] == ['e8']
        assert {'OVERLAP', 'DAILY_CAP'} <= set(cell['conflicts'])


def test_valid_baseline_retains_full_safe_coverage(fixture):
    config, history = fixture
    rows = witness(config, date(2026, 11, 1), date(2026, 12, 6))
    report = validate(config, '2026-11', rows, history)
    assert report['valid']
    assert report['covered_blocks'] == report['required_blocks']
    assert all(r['safe_actual'] == r['actual'] and not r['conflicts'] for r in report['coverage'])
