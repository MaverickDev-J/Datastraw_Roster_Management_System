import os
import time
from collections import defaultdict
from datetime import timedelta

from ortools.sat.python import cp_model

from .calendar import active_processes, days, demand, eligible, month_dates
from .validator import validate


def solve(config, month, history=None, history_complete=True, baseline=None):
    first, last = month_dates(month)
    dates = list(days(first, last + timedelta(days=6)))
    baseline = baseline or []
    original = {(a['employee_id'], a['date'], a['duty_id']) for a in baseline}
    locked = {key for key in original if any(a.get('locked') and (a['employee_id'], a['date'], a['duty_id']) == key for a in baseline)}
    history_work = {(a['employee_id'], a['date']) for a in history or []}
    started = time.monotonic()
    limit = max(0.1, min(30, float(os.getenv('SOLVER_SECONDS', '10'))))
    emps = config['employees']
    candidates = []
    day_demand = {d.isoformat(): demand(config, d) for d in dates}
    for day in dates:
        for duty in config['duties']:
            for employee in emps:
                if eligible(config, employee, duty, day, day_demand[day.isoformat()]):
                    candidates.append((employee, day, duty))

    def attempt(diagnostic=False):
        model = cp_model.CpModel()
        variables, occupied, employee_day, covers = {}, defaultdict(list), defaultdict(list), defaultdict(list)
        preference = []
        for employee, day, duty in candidates:
            eid, ds, did = employee['id'], day.isoformat(), duty['id']
            key = (eid, ds, did)
            var = model.new_bool_var(f'{eid}_{ds}_{did}')
            variables[key] = var
            employee_day[eid, ds].append((var, 4 * len(duty['blocks'])))
            for block in duty['blocks']:
                occupied[eid, ds, block].append(var)
                for p in active_processes(duty, day_demand[ds]):
                    if day_demand[ds].get((duty['brand_id'], p, block), 0):
                        covers[duty['brand_id'], p, ds, block].append(var)
            preference.append(var * (10 if day.weekday() == employee['preferred_off'] else 0))
            primary = all(any(a['employee_id'] == eid and a['brand_process_id'] == bp['id'] and a['role'] == 'PRIMARY' and a['approved'] and a['start'] <= ds and (not a['end'] or a['end'] >= ds) for a in config['allocations'])
                          for bp in config['brand_processes'] if bp['brand_id'] == duty['brand_id'] and bp['process_id'] in active_processes(duty, day_demand[ds]))
            preference.append(var * (1 if primary else 3))
            if key in original:
                model.add_hint(var, 1)
        for key in locked:
            if key not in variables:
                return 'INFEASIBLE', [], {'locked_conflict': True}
            model.add(variables[key] == 1)
        for vs in occupied.values():
            model.add(sum(vs) <= 1)
        for employee in emps:
            worked = {}
            for day in dates:
                ds = day.isoformat()
                vs = employee_day[employee['id'], ds]
                w = model.new_bool_var(f'worked_{employee["id"]}_{ds}')
                if vs:
                    model.add_max_equality(w, [v for v, _ in vs])
                    model.add(sum(v * hours for v, hours in vs) <= employee['max_hours'])
                else:
                    model.add(w == 0)
                worked[ds] = w
            for end in dates:
                terms = []
                for i in range(7):
                    ds = (end - timedelta(days=i)).isoformat()
                    terms.append(worked[ds] if ds in worked else int((employee['id'], ds) in history_work))
                model.add(sum(terms) <= 7 - employee['min_rest'])
        current_shortage, future_shortage = [], []
        for day in dates:
            ds = day.isoformat()
            for (bid, pid, block), required in day_demand[ds].items():
                if required:
                    if diagnostic:
                        slack = model.new_int_var(0, required, f'gap_{bid}_{pid}_{ds}_{block}')
                        (current_shortage if day <= last else future_shortage).append(slack)
                        model.add(sum(covers[bid, pid, ds, block]) + slack >= required)
                    else:
                        model.add(sum(covers[bid, pid, ds, block]) >= required)
        changes = [(1 - v) if key in original else v for key, v in variables.items() if key[1] <= last.isoformat()] if baseline else []
        # Current coverage > tentative future coverage > edits > preferences.
        # Mandatory constraints are NEVER expressed as weighted penalties.
        secondary_bound = 1 + 14 * len(variables)
        objective = sum(preference) + secondary_bound * sum(changes)
        if diagnostic:
            future_cost = 1 + 13 * len(variables) + secondary_bound * len(changes)
            future_demand = sum(required for ds, dem in day_demand.items() if ds > last.isoformat() for required in dem.values())
            objective += future_cost * sum(future_shortage)
            objective += future_cost * (future_demand + 1) * sum(current_shortage)
        model.minimize(objective)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = limit
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = 42
        status = solver.solve(model)
        status_name = solver.status_name(status)
        rows = []
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            rows = [{'employee_id': eid, 'date': ds, 'duty_id': did, 'locked': (eid, ds, did) in locked}
                    for (eid, ds, did), var in variables.items() if solver.value(var)]
        return status_name, rows, {'objective': solver.objective_value if rows else None, 'variables': len(variables)}

    strict_status, rows, details = attempt()
    diagnostic = False
    result_status = strict_status
    if strict_status == 'INFEASIBLE' and not details.get('locked_conflict'):
        result_status, rows, details = attempt(True)
        diagnostic = True
    report = validate(config, month, rows, history, history_complete)
    if details.get('locked_conflict'):
        report['issues'].insert(0, {'code': 'LOCK_CONFLICT', 'severity': 'ERROR', 'reason': 'A locked duty conflicts with availability, eligibility or mandatory rules. Review and unlock it explicitly.'})
        report['valid'] = False
    if result_status not in ['OPTIMAL', 'FEASIBLE']:
        report['valid'] = False
        report['issues'].insert(0, {'code': result_status, 'severity': 'ERROR', 'reason': 'No candidate established. UNKNOWN means the time limit was reached without establishing a solution; INFEASIBLE means the constraints cannot all hold.'})
    actual = {(a['employee_id'], a['date'], a['duty_id']) for a in rows if a['date'] <= last.isoformat()}
    return {'assignments': rows, 'validation': report, 'solver_status': result_status, 'strict_status': strict_status,
            'diagnostic': diagnostic, 'seconds': round(time.monotonic() - started, 3), 'details': details,
            'changes': {'added': [dict(employee_id=e, date=d, duty_id=t) for e, d, t in sorted(actual - original)],
                        'removed': [dict(employee_id=e, date=d, duty_id=t) for e, d, t in sorted(original - actual) if d <= last.isoformat()]}}
