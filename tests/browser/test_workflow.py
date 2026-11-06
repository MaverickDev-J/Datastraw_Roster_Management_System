"""Real Chromium UI QA. Uses the running local app and isolated synthetic workspaces."""
import json
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

expect.set_options(timeout=30000)

OUT = Path('artifacts/browser')
OUT.mkdir(parents=True, exist_ok=True)
results = []
errors = []


def passed(name):
    results.append(name)
    print('PASS:', name, flush=True)


def check_pending_config_refresh(route):
    # A slow response must not let the manager start a repair with an old
    # configuration revision. Escape must not dismiss an approved save midway.
    time.sleep(0.4)
    dialog = page.get_by_role('dialog', name='Plan time away')
    expect(dialog).to_be_visible()
    page.keyboard.press('Escape')
    expect(dialog).to_be_visible()
    route.continue_()


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
    page = context.new_page()
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.on('response', lambda r: errors.append(f'{r.status}: {r.url}') if r.status >= 500 else None)
    try:
        page.goto(sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8000', wait_until='domcontentloaded')
        expect(page.get_by_role('heading', name='Good plans. Great coverage.')).to_be_visible(timeout=30000)
        page.screenshot(path=str(OUT / '01-desktop-initial.png'), full_page=True)
        passed('Initial page renders without JavaScript errors')
        page.get_by_role('button', name='Generate roster', exact=True).click()
        expect(page.get_by_text('Roster ready. All mandatory checks passed.')).to_be_visible(timeout=60000)
        expect(page.locator('.roster-table tbody tr')).to_have_count(32)
        page.screenshot(path=str(OUT / '02-desktop-generated.png'), full_page=True)
        passed('Generate button produces a validated 32-person roster')

        page.get_by_role('button', name='Pod', exact=True).click()
        page.get_by_label('Filter by Pod', exact=True).select_option('pod0')
        expect(page.locator('.roster-table tbody tr')).to_have_count(8)
        page.get_by_role('button', name='Brand', exact=True).click()
        page.get_by_label('Filter by Brand', exact=True).select_option('b0')
        expect(page.locator('.coverage-table tbody tr')).to_have_count(2)
        page.get_by_role('button', name='Process', exact=True).click()
        page.get_by_label('Filter by Process', exact=True).select_option('CS')
        expect(page.locator('.coverage-table tbody tr')).to_have_count(25)
        page.get_by_role('button', name='Employee', exact=True).click()
        page.get_by_label('Filter by Employee', exact=True).select_option('e0')
        expect(page.locator('.roster-table tbody tr')).to_have_count(1)
        page.get_by_role('button', name='Company', exact=True).click()
        page.get_by_label('Find a teammate', exact=True).fill('Asha')
        expect(page.locator('.roster-table tbody tr')).to_have_count(1)
        page.get_by_label('Find a teammate', exact=True).fill('')
        passed('All five views, filters and teammate search work')

        page.get_by_role('button', name='Asha Mehta, 2026-11-15, working', exact=True).click()
        dialog = page.get_by_role('dialog')
        expect(dialog).to_be_visible()
        dialog.get_by_role('button', name='Lock duty', exact=True).click()
        dialog.get_by_role('button', name='Preview impact', exact=True).click()
        expect(dialog.get_by_text('All mandatory checks passed', exact=True)).to_be_visible(timeout=30000)
        dialog.get_by_role('button', name='Save draft changes', exact=True).click()
        expect(dialog).not_to_be_visible()
        expect(page.get_by_text('Draft updated and independently validated.')).to_be_visible()
        page.get_by_role('button', name='Asha Mehta, 2026-11-15, working', exact=True).click()
        dialog = page.get_by_role('dialog')
        dialog.get_by_role('button', name='Unlock duty', exact=True).click()
        dialog.get_by_role('button', name='Preview impact', exact=True).click()
        expect(dialog.get_by_text('All mandatory checks passed', exact=True)).to_be_visible(timeout=30000)
        dialog.get_by_role('button', name='Save draft changes', exact=True).click()
        expect(dialog).not_to_be_visible()
        passed('Duty locking, impact preview and manual save work')

        page.get_by_role('button', name='Add time off', exact=True).click()
        dialog = page.get_by_role('dialog')
        dialog.get_by_label('Team member', exact=True).select_option('e0')
        dialog.get_by_label('From', exact=True).fill('2026-11-15')
        dialog.get_by_label('Through', exact=True).fill('2026-11-15')
        page.route('**/api/config', check_pending_config_refresh, times=1)
        dialog.get_by_role('button', name='Approve time off', exact=True).click()
        expect(dialog).not_to_be_visible(timeout=30000)
        expect(page.get_by_text('Availability, rules or preceding-month work changed.', exact=False)).to_be_visible()
        expect(page.get_by_role('button', name='Publish', exact=True)).to_be_disabled()
        page.get_by_role('button', name='Conflicts', exact=False).first.click()
        expect(page.get_by_text('UNAVAILABLE ASSIGNMENT', exact=True)).to_be_visible()
        page.screenshot(path=str(OUT / '03-leave-conflicts.png'), full_page=True)
        passed('Leave approval exposes real conflicts and disables publication')
        page.get_by_role('button', name='Find a repair', exact=True).click()
        dialog = page.get_by_role('dialog', name='Review your repair')
        expect(dialog).to_be_visible(timeout=60000)
        expect(dialog.get_by_text('All mandatory checks pass.', exact=False)).to_be_visible()
        page.screenshot(path=str(OUT / '04-repair-comparison.png'), full_page=True)
        dialog.get_by_role('button', name='Review draft', exact=False).click()
        page.get_by_role('button', name='Monthly roster', exact=True).click()
        page.get_by_role('button', name='Publish', exact=True).click()
        expect(page.get_by_text('Published. This version is preserved in roster history.')).to_be_visible(timeout=30000)
        passed('Repair comparison and validated publication work end to end')

        with page.expect_download() as download:
            page.get_by_role('link', name='Download CSV', exact=True).click()
        download.value.save_as(OUT / 'roster.csv')
        assert 'Date,Employee,Brand' in (OUT / 'roster.csv').read_text()
        page.get_by_role('button', name='Version history', exact=True).click()
        expect(page.locator('.history-list > button')).to_have_count(2)
        page.get_by_role('button', name='Team & rules', exact=True).click()
        for title in ['Employees', 'Brands', 'Pods', 'Processes', 'Brand Processes', 'Allocations', 'Duties', 'Absences', 'Holidays', 'Overrides']:
            page.locator('.config-tabs').get_by_role('button', name=re.compile('^'+re.escape(title)+r'\s')).click()
            expect(page.locator('.config-heading h2')).to_have_text(title)
        page.locator('.config-tabs').get_by_role('button', name=re.compile(r'^Brands\s')).click()
        page.locator('.config-records').get_by_role('button').first.click()
        dialog = page.get_by_role('dialog')
        expect(dialog.get_by_label('Operating calendar', exact=True)).to_be_visible()
        dialog.get_by_role('button', name='Close dialog', exact=True).click()
        passed('CSV download, version history and configuration navigation work')

        page.get_by_role('button', name='Monthly roster', exact=True).click()
        page.get_by_role('button', name='Previous month', exact=True).click()
        expect(page.get_by_text('October 2026', exact=True)).to_be_visible()
        page.get_by_role('button', name='Next month', exact=True).click()
        expect(page.locator('.roster-table tbody tr')).to_have_count(32)
        page.reload(wait_until='domcontentloaded')
        expect(page.locator('.roster-table tbody tr')).to_have_count(32, timeout=30000)
        passed('Month navigation and saved-roster reload work')

        page.set_viewport_size({'width': 390, 'height': 844})
        expect(page.get_by_role('heading', name='Good plans. Great coverage.')).to_be_visible()
        page.screenshot(path=str(OUT / '05-mobile-roster.png'), full_page=True)
        overflow = page.evaluate('document.documentElement.scrollWidth > window.innerWidth')
        assert not overflow, 'Page-level horizontal overflow on mobile'
        page.get_by_role('button', name='Add time off', exact=True).click()
        expect(page.get_by_role('dialog')).to_be_visible()
        page.screenshot(path=str(OUT / '06-mobile-form.png'), full_page=True)
        page.keyboard.press('Escape')
        expect(page.get_by_role('dialog')).not_to_be_visible()
        passed('Mobile layout, form access and keyboard dismissal work')
        for width in [320, 768, 1440]:
            page.set_viewport_size({'width': width, 'height': 900})
            assert not page.evaluate('document.documentElement.scrollWidth > window.innerWidth'), f'Page overflow at width {width}'
        passed('320px, tablet and desktop layouts avoid page overflow')

        page.get_by_role('button', name='Team & rules', exact=True).click()
        page.locator('.config-tabs').get_by_role('button', name=re.compile(r'^Employees\s')).click()
        page.get_by_role('button', name='Add employee', exact=True).click()
        dialog = page.get_by_role('dialog')
        dialog.get_by_label('Full name', exact=True).fill('QA Test Employee')
        dialog.get_by_role('button', name='Save changes', exact=True).click()
        expect(dialog).not_to_be_visible(timeout=30000)
        expect(page.locator('.config-records').get_by_text('QA Test Employee', exact=True)).to_be_visible()
        page.locator('.config-tabs').get_by_role('button', name=re.compile(r'^Brands\s')).click()
        page.get_by_role('button', name='Add brand', exact=True).click()
        dialog = page.get_by_role('dialog')
        dialog.get_by_label('Brand name', exact=True).fill('QA Studio')
        dialog.get_by_role('button', name='Save changes', exact=True).click()
        expect(dialog).not_to_be_visible(timeout=30000)
        expect(page.get_by_role('heading', name='Finish setting up QA Studio')).to_be_visible()
        page.locator('.config-tabs').get_by_role('button', name=re.compile(r'^Brands\s')).click()
        expect(page.locator('.config-records').get_by_text('QA Studio', exact=True)).to_be_visible()
        passed('Employee and brand creation save through real configuration forms')

        page.get_by_role('button', name='Add time off', exact=True).click()
        dialog = page.get_by_role('dialog')
        dialog.get_by_label('Team member', exact=True).select_option('e8')
        dialog.get_by_label('From', exact=True).fill('2026-11-16')
        dialog.get_by_label('Through', exact=True).fill('2026-11-16')
        page.route('**/api/config', check_pending_config_refresh, times=1)
        dialog.get_by_role('button', name='Approve time off', exact=True).click()
        expect(dialog).not_to_be_visible(timeout=30000)
        page.get_by_role('button', name='Monthly roster', exact=True).click()
        page.get_by_role('button', name='Repair', exact=True).click()
        dialog = page.get_by_role('dialog', name='Review your repair')
        expect(dialog).to_be_visible(timeout=60000)
        expect(dialog.get_by_text('Mandatory conflicts remain.', exact=False)).to_be_visible()
        dialog.get_by_role('button', name='Review draft', exact=False).click()
        expect(page.get_by_role('button', name='Publish', exact=True)).to_be_disabled()
        page.get_by_role('button', name='Conflicts', exact=False).first.click()
        expect(page.get_by_text('COVERAGE GAP', exact=True).first).to_be_visible()
        page.screenshot(path=str(OUT / '07-impossible-staffing.png'), full_page=True)
        passed('An impossible staffing change produces visible gaps and blocks publication')
        assert not errors, errors
        passed('No browser JavaScript exceptions or HTTP 5xx errors')
    except Exception as exc:
        page.screenshot(path=str(OUT / 'failure.png'), full_page=True)
        (OUT / 'failure.html').write_text(page.content(), encoding='utf-8')
        (OUT / 'failure.txt').write_text(str(exc), encoding='utf-8')
        print('FAIL:', str(exc).split('Aria snapshot:')[0][:1500], flush=True)
        sys.exit(1)
    finally:
        (OUT / 'results.json').write_text(json.dumps({'passed': results, 'errors': errors}, indent=2), encoding='utf-8')
        browser.close()
