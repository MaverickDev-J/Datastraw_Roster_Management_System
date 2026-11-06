"""Focused short-viewport and demo-profile browser regression checks."""
import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

expect.set_options(timeout=30000)

out = Path('artifacts/sidebar')
out.mkdir(parents=True, exist_ok=True)
url = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8000'
inspect_only = '--inspect' in sys.argv

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    for width, height in [(1366, 768), (1280, 720), (1440, 600), (1024, 500), (1600, 900)]:
        page.set_viewport_size({'width': width, 'height': height})
        page.goto(url, wait_until='domcontentloaded')
        expect(page.get_by_role('heading', name='Good plans. Great coverage.')).to_be_visible(timeout=30000)
        metrics = page.evaluate('''() => {
          const guide = document.querySelector('.sidebar-guide');
          const profile = document.querySelector('.sidebar .profile');
          const rect = profile.getBoundingClientRect();
          return {guideClient: guide.clientHeight, guideContent: guide.scrollHeight,
            profileTop: rect.top, profileBottom: rect.bottom, viewport: innerHeight};
        }''')
        print(f'{width}x{height}: {json.dumps(metrics)}', flush=True)
        if inspect_only:
            page.screenshot(path=str(out / f'before-{width}x{height}.png'))
            continue
        assert metrics['profileTop'] >= 0 and metrics['profileBottom'] <= height, 'Profile is clipped below the viewport'
        assert metrics['guideClient'] >= metrics['guideContent'], 'Guide content is clipped inside its card'
        page.locator('.sidebar .tiny-tag').scroll_into_view_if_needed()
        expect(page.locator('.sidebar .tiny-tag')).to_be_in_viewport()
        expect(page.locator('.sidebar .profile')).to_be_in_viewport()
        page.screenshot(path=str(out / f'after-{width}x{height}.png'))
        profile = page.locator('.sidebar').get_by_role('button', name='Open demo profile', exact=True)
        profile.click()
        dialog = page.get_by_role('dialog', name='Your demo workspace')
        expect(dialog).to_be_visible()
        expect(dialog.get_by_text('Operations manager', exact=True)).to_be_visible()
        page.keyboard.press('Escape')
        expect(dialog).not_to_be_visible()
        expect(profile).to_be_focused()
        profile.click()
        dialog.get_by_role('button', name='Manage team & rules', exact=True).click()
        expect(dialog).not_to_be_visible()
        expect(page.get_by_role('heading', name='The people behind the plan.')).to_be_visible()
    if not inspect_only:
        for width, height in [(390, 700), (320, 568)]:
            page.set_viewport_size({'width': width, 'height': height})
            page.goto(url, wait_until='domcontentloaded')
            page.locator('.topbar').get_by_role('button', name='Open demo profile', exact=True).click()
            dialog = page.get_by_role('dialog', name='Your demo workspace')
            expect(dialog).to_be_visible()
            page.screenshot(path=str(out / f'mobile-profile-{width}.png'))
            dialog.get_by_role('button', name='Close', exact=True).click()
            expect(dialog).not_to_be_visible()
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
        assert not errors, errors
        print('PASS: short-viewport sidebar, full guide content, desktop/mobile profile, keyboard focus and navigation', flush=True)
    browser.close()
