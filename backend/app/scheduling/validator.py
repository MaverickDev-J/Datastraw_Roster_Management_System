"""Recompute coverage and constraints from assignments, independent of CP-SAT."""
from collections import defaultdict
from datetime import date, timedelta

from .calendar import active_processes, days, demand, eligible, month_dates, unavailable


def validate(config, month, assignments, history=None, history_complete=True):
    first, last = month_dates(month)
    emps = {e['id']: e for e in config['employees']}
    duties = {t['id']: t for t in config['duties']}
    brands = {b['id']: b['name'] for b in config['brands']}
    issues, coverage = [], []
    occupancy, hours, covered = defaultdict(list), defaultdict(int), defaultdict(set)
    work = defaultdict(set)
    conflict_codes = defaultdict(set)

    def conflict_day(eid, ds, code):
        for block in ['AM', 'PM']:
            conflict_codes[eid, ds, block].add(code)

    def issue(code, reason, **fields):
        issues.append({'code': code, 'severity': 'ERROR', 'reason': reason, **fields})

    if not history_complete:
        issue('BOUNDARY_HISTORY_MISSING', 'Previous six days have no confirmed history. Review or publish the preceding month before finalising.')
    for brand in config['brands']:
        if brand['active'] and not any(bp['brand_id'] == brand['id'] and any(p['id'] == bp['process_id'] and p['active'] for p in config['processes']) for bp in config['brand_processes']):
            issue('SETUP_INCOMPLETE', f'{brand["name"]} has no active process coverage rules. Configure its processes, staffing, approved allocations and duties.', brand_id=brand['id'])
    for a in history or []:
        work[a['employee_id']].add(a['date'])
    seen = set()
    for a in assignments:
        eid, ds, did = a['employee_id'], a['date'], a['duty_id']
        try:
            day = date.fromisoformat(ds)
        except ValueError:
            issue('INVALID_DATE', 'Assignment has an invalid date.', employee_id=eid)
            continue
        if eid not in emps or did not in duties:
            issue('INVALID_REFERENCE', 'Employee or duty no longer exists.', date=ds, employee_id=eid)
            continue
        if not first <= day <= last + timedelta(days=6):
            issue('OUTSIDE_HORIZON', 'Assignment is outside the planning horizon.', date=ds, employee_id=eid)
            continue
        key = (eid, ds, did)
        if key in seen:
            issue('DUPLICATE_ASSIGNMENT', 'Duplicate duty cannot create additional coverage.', date=ds, employee_id=eid)
        seen.add(key)
        employee, duty = emps[eid], duties[did]
        dem = demand(config, day)
        blocked = unavailable(config, employee, day)
        if blocked:
            issue('UNAVAILABLE_ASSIGNMENT', f'{employee["name"]} is unavailable: {blocked}.', date=ds, employee_id=eid)
        legal = eligible(config, employee, duty, day, dem)
        if not legal and not blocked:
            issue('INELIGIBLE_ASSIGNMENT', 'No approved, date-valid allocation for every active process in this duty.', date=ds, employee_id=eid)
        if not active_processes(duty, dem):
            issue('CLOSED_DUTY', 'Duty has no required open process.', date=ds, employee_id=eid)
        for block in duty['blocks']:
            occupancy[eid, ds, block].append(did)
            if legal:
                for p in active_processes(duty, dem):
                    if dem.get((duty['brand_id'], p, block), 0):
                        covered[duty['brand_id'], p, ds, block].add(eid)
        hours[eid, ds] += 4 * len(duty['blocks'])
        work[eid].add(ds)
    for (eid, ds, block), rows in occupancy.items():
        if len(rows) > 1:
            issue('OVERLAP', 'Independent duties overlap in this block.', date=ds, block=block, employee_id=eid)
            conflict_codes[eid, ds, block].add('OVERLAP')
    for (eid, ds), total in hours.items():
        if total > emps[eid]['max_hours']:
            issue('DAILY_CAP', f'{total} hours exceeds the daily cap.', date=ds, employee_id=eid)
            conflict_day(eid, ds, 'DAILY_CAP')
    for employee in emps.values():
        for end in days(first, last + timedelta(days=6)):
            window = [(end - timedelta(days=i)).isoformat() for i in range(7)]
            if sum(ds in work[employee['id']] for ds in window) > 7 - employee['min_rest']:
                issue('REST_VIOLATION', f'Fewer than {employee["min_rest"]} rest days in the seven days ending {end}.', date=end.isoformat(), employee_id=employee['id'])
                for ds in window:
                    conflict_day(employee['id'], ds, 'REST_VIOLATION')
    lookahead_coverage, lookahead_issues = [], []
    for day in days(first, last + timedelta(days=6)):
        for (bid, pid, block), required in demand(config, day).items():
            workers = sorted(covered[bid, pid, day.isoformat(), block])
            conflicted = [eid for eid in workers if conflict_codes[eid, day.isoformat(), block]]
            safe_actual = len(workers) - len(conflicted)
            codes = sorted({code for eid in conflicted for code in conflict_codes[eid, day.isoformat(), block]})
            status = 'CLOSED' if not required else 'CONFLICT' if conflicted else 'GAP' if safe_actual < required else 'COVERED'
            row = {'date': day.isoformat(), 'brand_id': bid, 'process_id': pid, 'block': block, 'required': required, 'actual': len(workers), 'employees': workers,
                   'safe_actual': safe_actual, 'conflicted_employees': conflicted, 'conflicts': codes, 'status': status}
            (coverage if day <= last else lookahead_coverage).append(row)
            if safe_actual < required:
                candidates = []
                for e in emps.values():
                    if any(t['brand_id'] == bid and pid in t['process_ids'] and block in t['blocks'] and eligible(config, e, t, day, demand(config, day)) for t in duties.values()):
                        candidates.append(e['name'])
                reason = f'{brands[bid]} / {pid} needs {required} people, has {safe_actual} without staffing conflicts. ' + ('Eligible candidates may be occupied elsewhere or need rest: ' + ', '.join(candidates) + '.' if candidates else 'No available approved candidate can perform the required duty.')
                if day > last:
                    lookahead_issues.append({'code': 'UPCOMING_COVERAGE_GAP', 'severity': 'WARNING', 'reason': reason, **row})
                    continue
                issue('COVERAGE_GAP', reason, **row)
    warnings = []
    for employee in emps.values():
        for ds in work[employee['id']]:
            if first.isoformat() <= ds <= last.isoformat() and date.fromisoformat(ds).weekday() == employee['preferred_off']:
                warnings.append({'code': 'PREFERRED_OFF', 'severity': 'WARNING', 'employee_id': employee['id'], 'date': ds, 'reason': 'Working on the preferred off weekday to support coverage.'})
    required_rows = [r for r in coverage if r['required'] > 0]
    return {'valid': not issues, 'issues': issues, 'warnings': warnings, 'coverage': coverage,
            'lookahead_issues': lookahead_issues, 'lookahead_coverage': lookahead_coverage,
            'required_blocks': len(required_rows), 'covered_blocks': sum(r['status'] == 'COVERED' for r in required_rows),
            'shortage': sum(max(0, r['required'] - r['safe_actual']) for r in required_rows)}
