"""Regression tests: scanner library must be self-hosted, not a CDN.

These tests guard against a regression where templates load html5-qrcode from
an external CDN (e.g. https://unpkg.com/html5-qrcode). The CSP
``script-src 'self' NONCE`` policy silently blocks external scripts, which
caused every camera/scanner button across the WMS to fail.
"""
from pathlib import Path

from django.test import TestCase


TEMPLATES_WITH_SCANNER = [
    "barcode_generator.html",
    "inventory_list.html",
    "item_upc_assign.html",
    "scanner.html",
    "ticket_form.html",
]


class ScannerScriptSelfHostedTests(TestCase):
    """Every template that depends on html5-qrcode must load it from the
    self-hosted vendor path so the strict CSP ``script-src 'self' NONCE``
    policy accepts it. Loading from unpkg/jsdelivr/etc. causes the browser
    to silently drop the script and every camera button to do nothing."""

    template_dir = Path(__file__).resolve().parent / "templates" / "inventory"

    def test_vendor_library_is_self_hosted(self):
        vendor = Path(__file__).resolve().parent / "static" / "inventory" / "js" / "vendor" / "html5-qrcode.min.js"
        self.assertTrue(
            vendor.exists(),
            f"Vendor library not found at {vendor}. Download html5-qrcode@2.3.8 from unpkg.com and commit it.",
        )
        # Library must be non-trivial in size (real minified bundle is ~370 KB).
        self.assertGreater(vendor.stat().st_size, 50_000, "html5-qrcode.min.js looks too small to be real.")

    def test_templates_do_not_reference_external_cdns(self):
        """No template should reference an external CDN for the scanner library."""
        offenders = []
        for template_name in TEMPLATES_WITH_SCANNER:
            path = self.template_dir / template_name
            content = path.read_text(encoding="utf-8")
            for cdn in ("unpkg.com", "cdn.jsdelivr.net", "cdnjs.cloudflare.com", "cdn.skypack.dev"):
                if cdn in content:
                    offenders.append((template_name, cdn))
        self.assertEqual(
            offenders,
            [],
            f"Templates still reference external CDNs (breaks CSP): {offenders}",
        )

    def test_templates_reference_self_hosted_vendor_path(self):
        """Templates that need the scanner must point at the self-hosted vendor file."""
        for template_name in TEMPLATES_WITH_SCANNER:
            path = self.template_dir / template_name
            content = path.read_text(encoding="utf-8")
            self.assertIn(
                "inventory/js/vendor/html5-qrcode.min.js",
                content,
                f"{template_name} does not reference the self-hosted html5-qrcode bundle.",
            )

    def test_scanner_template_has_no_duplicate_script_tag(self):
        """scanner.html used to load html5-qrcode twice (line 3 and inside the
        inline script block). Only one external script tag should remain."""
        content = (self.template_dir / "scanner.html").read_text(encoding="utf-8")
        vendor_refs = content.count("inventory/js/vendor/html5-qrcode.min.js")
        self.assertEqual(vendor_refs, 1, f"Expected exactly 1 vendor script tag in scanner.html, found {vendor_refs}.")
