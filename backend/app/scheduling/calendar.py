import calendar
from datetime import date, timedelta


def days(start, end):
    while start <= end:
        yield start
        start += timedelta(days=1)


def month_dates(month):
    year, m = map(int, month.split('-'))
    return date(year, m, 1), date(year, m, calendar.monthrange(year, m)[1])


def demand(config, day):
    holiday = any(h['date'] == day.isoformat() for h in config['holidays'])
    brands = {b['id']: b for b in config['brands']}
    processes = {p['id']: p for p in config['processes']}
    overrides = {(o['brand_process_id'], o['date'], o['block']): o['minimum'] for o in config['overrides']}
    result = {}
    for bp in config['brand_processes']:
        brand = brands[bp['brand_id']]
        rule = bp['calendar'] or brand['calendar']
        opened = brand['active'] and processes[bp['process_id']]['active'] and not (
            rule != 'EVERY_DAY' and holiday or rule == 'SUNDAYS_HOLIDAYS_OFF' and day.weekday() == 6)
        for block in bp['blocks']:
            required = overrides.get((bp['id'], day.isoformat(), block), bp['minimum'] if opened else 0)
            if not brand['active'] or not processes[bp['process_id']]['active']:
                required = 0
            result[(bp['brand_id'], bp['process_id'], block)] = required
    return result


def active_processes(duty, dem):
    return [p for p in duty['process_ids'] if any(dem.get((duty['brand_id'], p, b), 0) > 0 for b in duty['blocks'])]


def unavailable(config, employee, day):
    ds = day.isoformat()
    if not employee['active'] or ds < employee['start'] or employee['end'] and ds > employee['end']:
        return 'Outside active employment'
    for a in config['absences']:
        if a['employee_id'] == employee['id'] and a['approved'] and a['start'] <= ds <= a['end']:
            return a['kind']
    if employee['holiday_exempt'] and any(h['date'] == ds for h in config['holidays']):
        return 'HOLIDAY_OFF'
    return None


def eligible(config, employee, duty, day, dem):
    ps = active_processes(duty, dem)
    if not ps or unavailable(config, employee, day):
        return False
    bpids = {b['process_id']: b['id'] for b in config['brand_processes'] if b['brand_id'] == duty['brand_id']}
    return all(any(a['employee_id'] == employee['id'] and a['brand_process_id'] == bpids[p] and a['approved']
                   and a['start'] <= day.isoformat() and (not a['end'] or a['end'] >= day.isoformat()) for a in config['allocations']) for p in ps)
