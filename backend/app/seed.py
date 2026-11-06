"""Fictional assessment fixture with a hand-constructible coverage witness."""
from datetime import date, timedelta

from .schemas import check_config

NAMES = ['Asha Mehta', 'Rohan Shah', 'Mira Kapoor', 'Kabir Rao', 'Tara Singh', 'Dev Patel',
         'Anaya Das', 'Arjun Nair', 'Isha Verma', 'Neel Joshi', 'Sana Khan', 'Vihaan Roy',
         'Diya Bose', 'Aarav Jain', 'Nisha Sethi', 'Reyansh Sen', 'Pooja Iyer', 'Aditya Bhat',
         'Riya Malhotra', 'Kunal Suri', 'Meera Menon', 'Yash Arora', 'Kavya Rao', 'Nikhil Gupta',
         'Zoya Ali', 'Ishaan Desai', 'Anika Paul', 'Sahil Ahuja', 'Priya Anand', 'Vivek Sinha',
         'Leena Thomas', 'Om Kulkarni']
BRANDS = ['Aurora Home', 'Sunday Supply', 'Cedar & Co', 'Bloom Ritual', 'Field Notes', 'Northstar Gear',
          'Kindred Kitchen', 'Paper Moon', 'Olive Studio', 'Daily Dose', 'Terra Threads', 'Little Fern',
          'Cove Essentials', 'Mellow Market', 'Copper Lane', 'Luna Living', 'Wildflower', 'Good Grain',
          'Cloud Nine', 'Mango Grove', 'Studio Form', 'Evergreen', 'Salt & Stone', 'Bright Basket', 'Orbit Goods']


def seed_config():
    c = {k: [] for k in ['pods', 'employees', 'brands', 'processes', 'brand_processes', 'allocations', 'duties', 'absences', 'holidays', 'overrides']}
    for i, name in enumerate(['Atlas', 'Bloom', 'Cedar', 'Delta']):
        c['pods'].append({'id': f'pod{i}', 'name': name, 'lead_id': f'e{i * 8}'})
    for i, name in enumerate(NAMES):
        c['employees'].append({'id': f'e{i}', 'name': name, 'pod_id': f'pod{i // 8}', 'preferred_off': i % 6 if i < 8 else 6})
    for pid, name in [('CS', 'Customer Support'), ('ACC', 'Cart Conversions'), ('NDR', 'Non-Delivery Reports'), ('RET', 'Retention')]:
        c['processes'].append({'id': pid, 'name': name})
    for i, name in enumerate(BRANDS):
        bid = f'b{i}'
        calendar = 'EVERY_DAY' if i < 4 else 'HOLIDAYS_OFF' if i < 8 else 'SUNDAYS_HOLIDAYS_OFF'
        c['brands'].append({'id': bid, 'name': name, 'calendar': calendar})
        process_ids = ['CS', 'NDR'] if i < 20 else ['CS', 'ACC' if i != 23 else 'RET']
        for p in process_ids:
            c['brand_processes'].append({'id': f'{bid}_{p}', 'brand_id': bid, 'process_id': p, 'minimum': 2 if i == 24 and p == 'CS' else 1})
        if i < 20:
            c['duties'].append({'id': f'd{i}', 'brand_id': bid, 'name': 'Shared CS + NDR', 'process_ids': process_ids})
            for p in process_ids:
                c['allocations'].append({'id': f'a{i}_{p}', 'employee_id': f'e{i}', 'brand_process_id': f'{bid}_{p}'})
            if i < 8:
                for e in [30, 31]:
                    for p in process_ids:
                        c['allocations'].append({'id': f'a{e}_{i}_{p}', 'employee_id': f'e{e}', 'brand_process_id': f'{bid}_{p}', 'role': 'BACKUP'})
        else:
            for j in range(2):
                e = 20 + (i - 20) * 2 + j
                ps = ['CS'] if j == 0 else (process_ids if i == 24 else [process_ids[1]])
                c['duties'].append({'id': f'd{i}_{j}', 'brand_id': bid, 'name': ' + '.join(ps), 'process_ids': ps})
                for p in ps:
                    c['allocations'].append({'id': f'a{e}_{i}_{p}', 'employee_id': f'e{e}', 'brand_process_id': f'{bid}_{p}'})
            # Shared CS qualifications; the solver still respects separate duty capacity.
            if i == 20:
                c['allocations'].append({'id': 'shared_cs', 'employee_id': 'e21', 'brand_process_id': 'b20_CS', 'role': 'BACKUP'})
    # Mixed calendars: this low-volume CS process alone remains open on Sunday.
    # Use a relief-eligible single-owner brand so it remains genuinely feasible.
    next(bp for bp in c['brand_processes'] if bp['id'] == 'b0_NDR')['calendar'] = 'SUNDAYS_HOLIDAYS_OFF'
    c['holidays'] = [{'id': 'h1', 'date': '2026-11-12', 'name': 'Demo Founders Day (fictional)'}]
    c['absences'] = [
        {'id': 'leave1', 'employee_id': 'e8', 'start': '2026-11-08', 'end': '2026-11-08', 'kind': 'LEAVE'},
        {'id': 'comp1', 'employee_id': 'e9', 'start': '2026-11-15', 'end': '2026-11-15', 'kind': 'COMP_OFF'},
    ]
    return check_config(c)


def witness(config, start, end):
    """Explicit fixture schedule: eight daily owners stagger rest, two relievers."""
    from .scheduling.calendar import demand, active_processes
    duties = {d['id']: d for d in config['duties']}
    rows = []
    day = start
    while day <= end:
        dem = demand(config, day)
        relief = 0
        for i in range(25):
            if i < 20:
                did = f'd{i}'
                if not active_processes(duties[did], dem):
                    continue
                emp = f'e{i}'
                if i < 8 and day.weekday() == i % 6:
                    emp = f'e{30 + relief}'
                    relief += 1
                rows.append({'employee_id': emp, 'date': day.isoformat(), 'duty_id': did, 'locked': False})
            else:
                for j in range(2):
                    did = f'd{i}_{j}'
                    if active_processes(duties[did], dem):
                        rows.append({'employee_id': f'e{20 + (i - 20) * 2 + j}', 'date': day.isoformat(), 'duty_id': did, 'locked': False})
        day += timedelta(days=1)
    return rows
