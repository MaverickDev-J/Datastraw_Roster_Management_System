from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')


class Pod(Record):
    name: str = Field(min_length=1, max_length=100)
    lead_id: str | None = None


class Employee(Record):
    name: str = Field(min_length=1, max_length=100)
    pod_id: str
    active: bool = True
    start: date = date(2020, 1, 1)
    end: date | None = None
    max_hours: int = Field(default=8, ge=4, le=8)
    min_rest: int = Field(default=1, ge=1, le=6)
    preferred_off: int = Field(default=6, ge=0, le=6)
    holiday_exempt: bool = False

    @model_validator(mode='after')
    def valid_dates(self):
        if self.end and self.end < self.start:
            raise ValueError('Employment end precedes start')
        return self


class Brand(Record):
    name: str = Field(min_length=1, max_length=100)
    calendar: Literal['EVERY_DAY', 'HOLIDAYS_OFF', 'SUNDAYS_HOLIDAYS_OFF'] = 'SUNDAYS_HOLIDAYS_OFF'
    active: bool = True


class Process(Record):
    name: str = Field(min_length=1, max_length=100)
    active: bool = True


class BrandProcess(Record):
    brand_id: str
    process_id: str
    calendar: Literal['EVERY_DAY', 'HOLIDAYS_OFF', 'SUNDAYS_HOLIDAYS_OFF'] | None = None
    minimum: int = Field(default=1, ge=0, le=32)
    blocks: list[Literal['AM', 'PM']] = Field(default_factory=lambda: ['AM', 'PM'], min_length=1, max_length=2)


class Allocation(Record):
    employee_id: str
    brand_process_id: str
    role: Literal['PRIMARY', 'BACKUP'] = 'PRIMARY'
    approved: bool = True
    start: date = date(2020, 1, 1)
    end: date | None = None

    @model_validator(mode='after')
    def valid_dates(self):
        if self.end and self.end < self.start:
            raise ValueError('Allocation end precedes start')
        return self


class Duty(Record):
    brand_id: str
    name: str = Field(min_length=1, max_length=100)
    process_ids: list[str] = Field(min_length=1)
    blocks: list[Literal['AM', 'PM']] = Field(default_factory=lambda: ['AM', 'PM'], min_length=1, max_length=2)


class Absence(Record):
    employee_id: str
    start: date
    end: date
    kind: Literal['LEAVE', 'COMP_OFF', 'UNAVAILABLE', 'FIXED_OFF'] = 'LEAVE'
    approved: bool = True

    @model_validator(mode='after')
    def valid_dates(self):
        if self.end < self.start:
            raise ValueError('Absence end precedes start')
        if (self.end - self.start).days > 366:
            raise ValueError('Absence may not exceed 367 days')
        return self


class Holiday(Record):
    date: date
    name: str = Field(min_length=1, max_length=100)


class Override(Record):
    brand_process_id: str
    date: date
    block: Literal['AM', 'PM']
    minimum: int = Field(ge=0, le=32)


SCHEMAS = {'pods': Pod, 'employees': Employee, 'brands': Brand, 'processes': Process,
           'brand_processes': BrandProcess, 'allocations': Allocation, 'duties': Duty,
           'absences': Absence, 'holidays': Holiday, 'overrides': Override}


def check_config(config: dict) -> dict:
    """Validate references and canonicalise editable records before persistence."""
    result = {}
    for group, schema in SCHEMAS.items():
        result[group] = [schema.model_validate(row).model_dump(mode='json') for row in config.get(group, [])]
        ids = [r['id'] for r in result[group]]
        if len(ids) != len(set(ids)):
            raise ValueError(f'Duplicate IDs in {group}')
    refs = {k: {r['id'] for r in v} for k, v in result.items()}
    for emp in result['employees']:
        if emp['pod_id'] not in refs['pods']:
            raise ValueError('Employee pod does not exist')
    for pod in result['pods']:
        if pod['lead_id'] and not any(e['id'] == pod['lead_id'] and e['pod_id'] == pod['id'] for e in result['employees']):
            raise ValueError('Pod lead must be an employee in that pod')
    for bp in result['brand_processes']:
        if bp['brand_id'] not in refs['brands'] or bp['process_id'] not in refs['processes']:
            raise ValueError('Brand/process reference does not exist')
        if len(set(bp['blocks'])) != len(bp['blocks']):
            raise ValueError('Duplicate demand blocks')
    pairs = [(b['brand_id'], b['process_id']) for b in result['brand_processes']]
    if len(set(pairs)) != len(pairs):
        raise ValueError('Duplicate brand/process pair')
    for a in result['allocations']:
        if a['employee_id'] not in refs['employees'] or a['brand_process_id'] not in refs['brand_processes']:
            raise ValueError('Allocation references missing employee or brand process')
    for duty in result['duties']:
        if duty['brand_id'] not in refs['brands'] or any((duty['brand_id'], p) not in pairs for p in duty['process_ids']):
            raise ValueError('Duty process is not configured for this brand')
        if len(set(duty['blocks'])) != len(duty['blocks']) or len(set(duty['process_ids'])) != len(duty['process_ids']):
            raise ValueError('Duplicate blocks or processes in duty')
    for absence in result['absences']:
        if absence['employee_id'] not in refs['employees']:
            raise ValueError('Absence employee does not exist')
    keys = []
    for override in result['overrides']:
        if override['brand_process_id'] not in refs['brand_processes']:
            raise ValueError('Override brand process does not exist')
        bp = next(b for b in result['brand_processes'] if b['id'] == override['brand_process_id'])
        if override['block'] not in bp['blocks']:
            raise ValueError('Override block is not enabled for this process')
        keys.append((override['brand_process_id'], override['date'], override['block']))
    if len(keys) != len(set(keys)):
        raise ValueError('Conflicting overrides for the same date and block')
    if len({h['date'] for h in result['holidays']}) != len(result['holidays']):
        raise ValueError('Duplicate holiday date')
    return result
