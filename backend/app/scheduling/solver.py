"""Tested default: sparse HiGHS MILP; CP-SAT requires explicit selection."""
import os
import time
from collections import defaultdict
from datetime import timedelta

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_array

from .calendar import active_processes, days, demand, eligible, month_dates
from .validator import validate


def solve(config, month, history=None, history_complete=True, baseline=None):
    engine = os.getenv('ROSTER_SOLVER', 'highs')
    if engine not in ['highs', 'cp-sat']:
        raise ValueError('ROSTER_SOLVER must be highs or cp-sat')
    if engine == 'cp-sat':
        from .cp_sat import solve as cp_solve
        result = cp_solve(config, month, history, history_complete, baseline)
        result['engine'] = 'OR-Tools CP-SAT'
        return result
    return solve_highs(config, month, history, history_complete, baseline)


def solve_highs(config, month, history=None, history_complete=True, baseline=None):
    first, last = month_dates(month)
    horizon = list(days(first, last + timedelta(days=6)))
    original = {(a['employee_id'], a['date'], a['duty_id']) for a in baseline or []}
    locks = {(a['employee_id'], a['date'], a['duty_id']) for a in baseline or [] if a.get('locked')}
    hist = {(a['employee_id'], a['date']) for a in history or []}
    candidates = []
    demands = {d.isoformat(): demand(config, d) for d in horizon}
    for day in horizon:
        for duty in config['duties']:
            for e in config['employees']:
                if eligible(config, e, duty, day, demands[day.isoformat()]):
                    candidates.append((e, day, duty))
    started = time.monotonic()
    limit = max(0.1, min(30, float(os.getenv('SOLVER_SECONDS', '10'))))

    def attempt(diagnostic=False):
        costs, upper = [], []
        rows, lower, bounds = [], [], []
        indexes, occupy, worked, cover = {}, defaultdict(list), defaultdict(list), defaultdict(list)

        def var(cost=0, cap=1):
            i = len(costs)
            costs.append(cost)
            upper.append(cap)
            return i

        def constraint(terms, lo=-np.inf, hi=np.inf):
            rows.append(terms)
            lower.append(lo)
            bounds.append(hi)

        preference_bound = 14 * len(candidates) + 1
        for employee, day, duty in candidates:
            eid, ds, did = employee['id'], day.isoformat(), duty['id']
            key = eid, ds, did
            pids = active_processes(duty, demands[ds])
            bpids = {b['id'] for b in config['brand_processes'] if b['brand_id'] == duty['brand_id'] and b['process_id'] in pids}
            primary_ids = {a['brand_process_id'] for a in config['allocations'] if a['employee_id'] == eid and a['role'] == 'PRIMARY' and a['approved'] and a['start'] <= ds and (not a['end'] or a['end'] >= ds)}
            cost = 1 if bpids <= primary_ids else 3
            cost += 10 if day.weekday() == employee['preferred_off'] else 0
            if baseline and ds <= last.isoformat():
                cost += -preference_bound if key in original else preference_bound
            i = var(cost)
            indexes[key] = i
            worked[eid, ds].append((i, 4 * len(duty['blocks'])))
            for block in duty['blocks']:
                occupy[eid, ds, block].append(i)
                for p in pids:
                    if demands[ds].get((duty['brand_id'], p, block), 0):
                        cover[duty['brand_id'], p, ds, block].append(i)
        for key in locks:
            if key not in indexes:
                return 'INFEASIBLE', [], {'locked_conflict': True}
            constraint({indexes[key]: 1}, lo=1, hi=1)
        for ids in occupy.values():
            constraint({i: 1 for i in ids}, hi=1)
        for e in config['employees']:
            dayvars = {}
            for day in horizon:
                ds = day.isoformat()
                w = var()
                dayvars[ds] = w
                vs = worked[e['id'], ds]
                constraint({i: h for i, h in vs}, hi=e['max_hours'])
                # w iff at least one duty selected; max two disjoint half-day duties.
                constraint({**{i: 1 for i, _ in vs}, w: -2}, hi=0)
                constraint({**{i: 1 for i, _ in vs}, w: -1}, lo=0)
            for day in horizon:
                terms, past = {}, 0
                for n in range(7):
                    ds = (day - timedelta(days=n)).isoformat()
                    if ds in dayvars:
                        terms[dayvars[ds]] = 1
                    elif (e['id'], ds) in hist:
                        past += 1
                constraint(terms, hi=7 - e['min_rest'] - past)
        # A unit of current-month coverage dominates every possible future gap
        # and secondary cost. Future coverage in turn dominates edits/preferences.
        # Bounds are derived from this model, rather than arbitrary big weights.
        future_shortage_cost = 1 + sum(abs(cost) for cost in costs)
        future_demand = sum(required for ds, dem in demands.items() if ds > last.isoformat() for required in dem.values())
        current_shortage_cost = future_shortage_cost * (future_demand + 1)
        for day in horizon:
            ds = day.isoformat()
            for (bid, pid, block), required in demands[ds].items():
                if required:
                    terms = {i: 1 for i in cover[bid, pid, ds, block]}
                    if diagnostic:
                        terms[var(current_shortage_cost if day <= last else future_shortage_cost, required)] = 1
                    constraint(terms, lo=required)
        ri, ci, values = [], [], []
        for r, terms in enumerate(rows):
            for col, value in terms.items():
                ri.append(r)
                ci.append(col)
                values.append(value)
        matrix = coo_array((np.array(values, dtype=float), (np.array(ri, dtype=np.int32), np.array(ci, dtype=np.int32))), shape=(len(rows), len(costs))).tocsc()
        result = milp(np.array(costs, dtype=float), integrality=np.ones(len(costs)), bounds=Bounds(np.zeros(len(costs)), upper),
                      constraints=LinearConstraint(matrix, lower, bounds), options={'time_limit': limit, 'mip_rel_gap': 0})
        status = 'OPTIMAL' if result.status == 0 else 'INFEASIBLE' if result.status == 2 else 'FEASIBLE' if result.x is not None else 'UNKNOWN'
        assignments = []
        if result.x is not None:
            assignments = [{'employee_id': e, 'date': d, 'duty_id': t, 'locked': (e, d, t) in locks} for (e, d, t), i in indexes.items() if result.x[i] > 0.5]
        return status, assignments, {'variables': len(costs), 'message': str(result.message)}

    strict, assignments, details = attempt()
    status, diagnostic = strict, False
    if strict == 'INFEASIBLE' and not details.get('locked_conflict'):
        status, assignments, details = attempt(True)
        diagnostic = True
    report = validate(config, month, assignments, history, history_complete)
    if status not in ['OPTIMAL', 'FEASIBLE'] or details.get('locked_conflict'):
        report['valid'] = False
        report['issues'].insert(0, {'code': 'LOCK_CONFLICT' if locks else status, 'severity': 'ERROR',
                                   'reason': 'No candidate established. Review locks and mandatory constraints; a timeout does not prove impossibility.'})
    actual = {(a['employee_id'], a['date'], a['duty_id']) for a in assignments if a['date'] <= last.isoformat()}
    return {'engine': 'SciPy / HiGHS MILP', 'assignments': assignments, 'validation': report,
            'solver_status': status, 'strict_status': strict, 'diagnostic': diagnostic, 'details': details,
            'seconds': round(time.monotonic() - started, 3),
            'changes': {'added': [dict(employee_id=e, date=d, duty_id=t) for e, d, t in sorted(actual - original)],
                        'removed': [dict(employee_id=e, date=d, duty_id=t) for e, d, t in sorted(original - actual) if d <= last.isoformat()]}}
