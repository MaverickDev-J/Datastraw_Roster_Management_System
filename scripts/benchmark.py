"""Reproducible doubled-fixture solve; measures local runtime, not production SLA."""
import copy
from datetime import date

from backend.app.seed import seed_config, witness
from backend.app.scheduling.solver import solve_highs

config = seed_config()
history = witness(config, date(2026, 10, 26), date(2026, 10, 31))
other = copy.deepcopy(config)
for group, records in other.items():
    if group in ['processes', 'holidays']:
        continue
    for record in records:
        record['id'] = 'copy_' + record['id']
        for field in ['pod_id', 'lead_id', 'brand_id', 'employee_id', 'brand_process_id']:
            if record.get(field):
                record[field] = 'copy_' + record[field]
        if 'name' in record:
            record['name'] += ' II'
    config[group].extend(records)
history += [{**a, 'employee_id': 'copy_' + a['employee_id'], 'duty_id': 'copy_' + a['duty_id']} for a in history[:]]
result = solve_highs(config, '2026-11', history)
assert result['validation']['valid'], result['validation']['issues'][:3]
print({'employees': len(config['employees']), 'brands': len(config['brands']), 'seconds': result['seconds'],
       'status': result['solver_status'], 'valid': result['validation']['valid'], 'variables': result['details']['variables']})
