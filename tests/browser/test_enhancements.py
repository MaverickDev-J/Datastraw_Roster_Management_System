"""Browser regression checks for coverage, saved snapshots and boundary setup."""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

expect.set_options(timeout=30000)

base = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8002'
with sync_playwright() as p:
    browser = p.chromium.launch()
    context = browser.new_context(viewport={'width': 1366, 'height': 768})
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(base)
    page.get_by_role('button', name='Generate roster', exact=True).click()
    expect(page.get_by_role('button', name='Publish', exact=True)).to_be_enabled(timeout=60000)
    page.get_by_role('button', name='Brand', exact=True).click()
    page.get_by_role('button', name='Coverage Aurora Home CS 2026-11-01', exact=True).click()
    dialog = page.get_by_role('dialog')
    expect(dialog.get_by_text('Asha Mehta: Shared CS + NDR (AM + PM)', exact=True).first).to_be_visible()
    expect(dialog.get_by_text('Leena Thomas · BACKUP', exact=True)).to_be_visible(timeout=60000)
    dialog.get_by_role('button', name='Close dialog').click()
    page.get_by_role('button', name='Publish', exact=True).click()
    expect(page.get_by_role('button', name='Check current rules', exact=True)).to_be_visible()
    cfg = context.request.get(base+'/api/config').json()
    brand = next(b for b in cfg['brands'] if b['id'] == 'b0')
    brand['name'] = 'Changed current brand'
    assert context.request.put(base+'/api/config/brands', data={'revision': cfg['revision'], 'record': brand}).status == 200
    page.reload()
    page.get_by_role('button', name='Brand', exact=True).click()
    expect(page.get_by_role('button', name='Coverage Aurora Home CS 2026-11-01', exact=True)).to_be_visible()
    page.get_by_role('button', name='Check current rules', exact=True).click()
    expect(page.get_by_role('button', name='Coverage Changed current brand CS 2026-11-01', exact=True)).to_be_visible()
    page.get_by_role('button', name='View saved version', exact=True).click()
    expect(page.get_by_role('button', name='Coverage Aurora Home CS 2026-11-01', exact=True)).to_be_visible()
    page.get_by_role('button', name='Coverage', exact=True).click()
    expect(page.get_by_role('heading', name='Coverage resilience and workload')).to_be_visible()
    expect(page.get_by_text('Workload and preference summary', exact=True)).to_be_visible()
    page.get_by_label('Select roster month').fill('2027-03')
    page.get_by_role('button', name='Previous six days', exact=True).click()
    dialog = page.get_by_role('dialog')
    expect(dialog.get_by_label('Asha Mehta worked 2027-02-23')).to_be_visible()
    dialog.get_by_label('I confirm these worked and non-working days are accurate').check()
    dialog.get_by_role('button', name='Confirm history', exact=True).click()
    expect(dialog).not_to_be_visible()
    assert context.request.get(base+'/api/history/2027-03').json()['complete']
    page.get_by_role('button', name='Team & rules', exact=True).click()
    page.get_by_role('button', name='Duties', exact=False).first.click()
    page.get_by_role('button', name='Add duty', exact=True).click()
    expect(page.get_by_label('Processes (select one or more)')).to_be_visible()
    assert page.get_by_label('Processes (select one or more)').evaluate('e => e.multiple')
    page.get_by_role('button', name='Close dialog').click()
    page.set_viewport_size({'width': 390, 'height': 700})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    Path('artifacts/enhancements').mkdir(parents=True, exist_ok=True)
    page.screenshot(path='artifacts/enhancements/mobile.png', full_page=True)
    assert not errors, errors
    print('PASS: coverage employees/backups, historical snapshots/current checks, workload, boundary confirmation, process selectors, mobile, no JS errors')
    browser.close()
