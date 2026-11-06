"""Exercise real configuration changes that create conflicting coverage."""
import json
import sys
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

expect.set_options(timeout=30000)


base = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8003'
with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    context = browser.new_context(viewport={'width': 1440, 'height': 900})
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(base)
    expect(page.get_by_role('button', name='Generate roster', exact=True)).to_be_enabled()
    config = context.request.get(base + '/api/config').json()
    revision = config['revision']

    def save(group, record):
        global revision
        response = context.request.put(base + '/api/config/' + group, data={'revision': revision, 'record': record})
        assert response.ok, response.text()
        revision = response.json()['revision']

    for bp in config['brand_processes']:
        if bp['brand_id'] in ['b8', 'b9']:
            save('brand_processes', dict(bp, blocks=['AM'] if bp['brand_id'] == 'b8' else ['PM']))
    for duty in config['duties']:
        if duty['id'] in ['d8', 'd9']:
            save('duties', dict(duty, blocks=['AM'] if duty['id'] == 'd8' else ['PM']))
    for process in ['CS', 'NDR']:
        save('allocations', {'id': 'regression_' + process, 'employee_id': 'e8', 'brand_process_id': 'b9_' + process, 'role': 'BACKUP', 'approved': True})
    save('absences', {'id': 'regression_leave', 'employee_id': 'e9', 'start': '2026-11-16', 'end': '2026-11-16', 'kind': 'LEAVE', 'approved': True})
    page.reload()
    page.get_by_role('button', name='Generate roster', exact=True).click()
    expect(page.get_by_role('button', name='Publish', exact=True)).to_be_enabled(timeout=60000)
    page.get_by_role('button', name='Brand', exact=True).click()
    first = page.get_by_role('button', name='Coverage Olive Studio CS 2026-11-16', exact=True)
    second = page.get_by_role('button', name='Coverage Daily Dose CS 2026-11-16', exact=True)
    expect(first).to_have_attribute('data-coverage-state', 'COVERED')
    expect(second).to_have_attribute('data-coverage-state', 'COVERED')

    # Make a permitted PM duty full-day: the existing roster is now double-booked.
    save('duties', dict(next(d for d in config['duties'] if d['id'] == 'd9'), blocks=['AM', 'PM']))
    page.reload()
    page.get_by_role('button', name='Brand', exact=True).click()
    expect(first).to_have_attribute('data-coverage-state', 'CONFLICT')
    expect(second).to_have_attribute('data-coverage-state', 'CONFLICT')
    expect(first).to_have_class('coverage-cell conflicted')
    expect(page.get_by_role('button', name='Publish', exact=True)).to_be_disabled()
    coverage = page.locator('.stat-card').filter(has_text='Coverage').locator('.stat-value')
    assert coverage.inner_text() != '100%'
    first.click()
    dialog = page.get_by_role('dialog')
    expect(dialog.get_by_text('Conflicted coverage:', exact=False).first).to_be_visible()
    expect(dialog.get_by_text('OVERLAP', exact=False).first).to_be_visible()
    Path('artifacts/coverage-regressions').mkdir(parents=True, exist_ok=True)
    page.screenshot(path='artifacts/coverage-regressions/conflicted-desktop.png', full_page=True)
    page.set_viewport_size({'width': 390, 'height': 740})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path='artifacts/coverage-regressions/conflicted-mobile.png', full_page=True)
    dialog.get_by_role('button', name='Close dialog').click()

    # Configuration selectors show readable names and clear incompatible processes.
    page.get_by_role('button', name='Team & rules', exact=True).click()
    page.locator('.config-tabs').get_by_role('button', name='Allocations', exact=False).click()
    page.get_by_role('button', name='Add allocation', exact=True).click()
    dialog = page.get_by_role('dialog')
    options = dialog.get_by_label('Brand / process', exact=True).locator('option').all_text_contents()
    assert 'Aurora Home / Customer Support' in options
    assert not any('b0 /' in label for label in options)
    dialog.get_by_role('button', name='Close dialog').click()
    page.locator('.config-tabs').get_by_role('button', name='Duties', exact=False).click()
    page.get_by_role('button', name='Add duty', exact=True).click()
    dialog = page.get_by_role('dialog')
    dialog.get_by_label('Brand', exact=True).select_option('b20')
    assert dialog.get_by_label('Processes (select one or more)', exact=True).evaluate('e => Array.from(e.selectedOptions, o => o.value)') == ['CS']
    assert not errors, errors
    print(json.dumps({'passed': ['disjoint duties retain covered state', 'overlap flags both brands', 'verified coverage falls below 100%', 'publication blocked', 'conflict details desktop/mobile', 'readable brand/process selectors', 'brand changes clear incompatible processes'], 'errors': errors}))
    browser.close()
