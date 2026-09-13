#!/usr/bin/env python3
"""Authenticated desktop/mobile visual and overflow audit for the WMS portals."""
import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

from playwright.async_api import async_playwright

OUT = Path("/tmp/bbx-visual-audit")
OUT.mkdir(parents=True, exist_ok=True)
PORTALS = ["bbx.rplwms.com", "requests.rplwms.com"]
VIEWPORTS = {"mobile": {"width": 390, "height": 844}, "desktop": {"width": 1440, "height": 1000}}
SKIP = re.compile(r"/(?:static/|logout/?$|push/|service-worker|export|download|print/|status/|archive-now|delete-item|camera-scan)")
WMS_START_PATHS = ["/", "/inventory/", "/tickets/", "/inventory/receiving/", "/material-requests/", "/transactions/", "/locations/", "/scanner/", "/settings/"]
REQUEST_START_PATHS = ["/", "/inventory/", "/material-requests/", "/material-requests/archive/", "/material-requests/new/"]


def canonical(url):
    p = urlsplit(url)
    query = p.query if p.path in {"/settings/", "/requests/"} else ""
    return urlunsplit((p.scheme, p.netloc, p.path, query, ""))


async def audit_context(browser, host, viewport_name, viewport):
    context = await browser.new_context(viewport=viewport, ignore_https_errors=True)
    page = await context.new_page()
    errors = []
    page.on("console", lambda msg: errors.append(f"console:{msg.type}:{msg.text}") if msg.type == "error" else None)
    page.on("pageerror", lambda exc: errors.append(f"pageerror:{exc}"))
    base = f"http://{host}:8099"
    login_path = "/login/" if host.startswith("requests.") else "/accounts/login/"
    await page.goto(base + login_path, wait_until="domcontentloaded")
    await page.locator('input[name="username"]').fill("visual_auditor")
    await page.locator('input[name="password"]').fill("VisualAuditOnly-2026!")
    await page.locator('button[type="submit"], input[type="submit"]').first.click()
    await page.wait_for_load_state("networkidle")

    starts = REQUEST_START_PATHS if host.startswith("requests.") else WMS_START_PATHS
    queue = [base + path for path in starts]
    seen, seen_patterns, results = set(), set(), []
    while queue and len(seen) < 90:
        url = canonical(queue.pop(0))
        pattern = re.sub(r"/\d+(?=/|$)", "/{id}", urlsplit(url).path)
        pattern = re.sub(r"(?<=/locations/)[^/]+", "{slug}", pattern)
        pattern += ("?" + urlsplit(url).query if urlsplit(url).query else "")
        if url in seen or pattern in seen_patterns or urlsplit(url).netloc != f"{host}:8099" or SKIP.search(urlsplit(url).path):
            continue
        seen.add(url)
        seen_patterns.add(pattern)
        response = await page.goto(url, wait_until="networkidle")
        status = response.status if response else 0
        if page.url.startswith(base + login_path):
            results.append({"url": url, "status": status, "error": "redirected to login"})
            continue
        await page.wait_for_timeout(80)
        metrics = await page.evaluate("""() => {
          const vw = document.documentElement.clientWidth;
          const ignored = el => el.closest('.table-responsive,.inventory-table-wrap,.settings-tabs,.nav-wrap,[data-mobile-nav-panel],[hidden]') || getComputedStyle(el).display === 'none' || getComputedStyle(el).opacity === '0';
          const offenders = [...document.body.querySelectorAll('*')].filter(el => {
            if (ignored(el)) return false;
            const r = el.getBoundingClientRect();
            return r.width > 1 && (r.left < -1 || r.right > vw + 1);
          }).slice(0, 12).map(el => ({tag:el.tagName, cls:el.className?.toString().slice(0,100), left:Math.round(el.getBoundingClientRect().left), right:Math.round(el.getBoundingClientRect().right)}));
          const roleClasses = ['btn-primary','btn-secondary','btn-success','btn-warning','btn-danger','btn-info'];
          const badButtons = [...document.querySelectorAll('.btn,.action-chip,.quick-action-btn')].filter(el => roleClasses.filter(c => el.classList.contains(c)).length !== 1).map(el => el.textContent.trim().slice(0,60));
          const clippedText = [...document.querySelectorAll('button,a,label,td,th')].filter(el => !ignored(el) && el.scrollWidth > el.clientWidth + 2 && getComputedStyle(el).overflowX === 'visible').slice(0,12).map(el => el.textContent.trim().slice(0,60));
          return {viewport:vw, documentWidth:document.body.scrollWidth, offenders, badButtons, clippedText};
        }""")
        nav_ok = True
        if viewport_name == "mobile":
            toggle = page.locator("[data-mobile-nav-toggle]")
            if await toggle.count():
                await toggle.click()
                nav_ok = await toggle.get_attribute("aria-expanded") == "true" and await page.locator("[data-mobile-nav-panel]").evaluate("el => el.classList.contains('is-open')")
                await page.locator("[data-mobile-nav-close]").click()
                nav_ok = nav_ok and await toggle.get_attribute("aria-expanded") == "false"
        slug = (urlsplit(url).path.strip("/") or "dashboard").replace("/", "--")
        if urlsplit(url).query:
            slug += "--" + re.sub(r"[^a-zA-Z0-9]+", "-", urlsplit(url).query).strip("-")
        shot = f"{host.split('.')[0]}--{viewport_name}--{slug}.png"
        await page.screenshot(path=str(OUT / shot), full_page=True)
        results.append({"url": url, "status": status, "nav_ok": nav_ok, "screenshot": shot, **metrics})
        for href in await page.locator("a[href]").evaluate_all("els => els.map(e => e.href)"):
            candidate = canonical(href)
            if urlsplit(candidate).netloc == f"{host}:8099" and candidate not in seen and not SKIP.search(urlsplit(candidate).path):
                queue.append(candidate)
    await context.close()
    errors = [error for error in errors if "Cross-Origin-Opener-Policy header has been ignored" not in error]
    return results, errors


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--host-resolver-rules=MAP bbx.rplwms.com 127.0.0.1, MAP requests.rplwms.com 127.0.0.1"])
        report = {}
        for host in PORTALS:
            for name, viewport in VIEWPORTS.items():
                report[f"{host}:{name}"] = await audit_context(browser, host, name, viewport)
        await browser.close()
    (OUT / "report.json").write_text(json.dumps(report, indent=2))
    failures = []
    total = 0
    for key, (pages, errors) in report.items():
        for page in pages:
            total += 1
            if page.get("status", 500) >= 400 or page.get("documentWidth", 0) > page.get("viewport", 0) + 1 or page.get("offenders") or page.get("badButtons") or not page.get("nav_ok", True):
                failures.append({"context": key, **page})
        if errors:
            failures.append({"context": key, "browser_errors": errors})
    print(json.dumps({"pages_checked": total, "failures": failures, "report": str(OUT / 'report.json')}, indent=2))
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    asyncio.run(main())
