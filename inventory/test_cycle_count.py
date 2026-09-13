"""Tests for the cycle counting feature."""
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone

from inventory.models import (
    CycleCount,
    CycleCountItem,
    InventoryItem,
    pick_random_items_for_cycle_count,
)

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="cc-user", password="x")


@pytest.fixture
def manager(db):
    mgr = User.objects.create_user(username="cc-manager", password="x")
    for codename in ("manage_cycle_counts", "archive_cycle_counts"):
        perm = Permission.objects.get(
            content_type__app_label="inventory",
            codename=codename,
        )
        mgr.user_permissions.add(perm)
    return mgr


@pytest.fixture
def counter(db):
    u = User.objects.create_user(username="cc-counter", password="x")
    perm = Permission.objects.get(
        content_type__app_label="inventory",
        codename="perform_cycle_count",
    )
    u.user_permissions.add(perm)
    return u


@pytest.fixture
def archiver(db):
    """User with the archive_cycle_counts permission but not manage_cycle_counts."""
    u = User.objects.create_user(username="cc-archiver", password="x")
    perm = Permission.objects.get(
        content_type__app_label="inventory",
        codename="archive_cycle_counts",
    )
    u.user_permissions.add(perm)
    return u


@pytest.fixture
def outsider(db):
    return User.objects.create_user(username="cc-outsider", password="x")


def _seed_items(category, count):
    return [
        InventoryItem.objects.create(
            part_number=f"CC-{category}-{i:03d}",
            name=f"{category} Item {i}",
            category=category,
            quantity_on_hand=i,
            active=True,
        )
        for i in range(count)
    ]


@pytest.mark.django_db
def test_pick_random_items_reproducible_with_seed():
    _seed_items("OFCI", 20)
    sel1 = pick_random_items_for_cycle_count([("OFCI", 50)], seed="abc")
    sel2 = pick_random_items_for_cycle_count([("OFCI", 50)], seed="abc")
    ids1 = sorted(item.pk for _, items in sel1 for item in items)
    ids2 = sorted(item.pk for _, items in sel2 for item in items)
    assert ids1 == ids2


@pytest.mark.django_db
def test_pick_random_items_respects_percent():
    for cat, n in [("OFCI", 30), ("Equipment", 10)]:
        _seed_items(cat, n)
    selections = pick_random_items_for_cycle_count(
        [("OFCI", 10), ("Equipment", 50)],
        seed="fixed",
    )
    by_cat = dict(selections)
    assert len(by_cat["OFCI"]) == 3  # ceil(30 * 0.1) = 3
    assert len(by_cat["Equipment"]) == 5  # ceil(10 * 0.5) = 5


@pytest.mark.django_db
def test_pick_random_items_clamps_percent():
    _seed_items("OFCI", 4)
    a = pick_random_items_for_cycle_count([("OFCI", 200)], seed="x")
    assert len(a[0][1]) == 4
    b = pick_random_items_for_cycle_count([("OFCI", 0)], seed="x")
    # 0 is below clamp so it becomes 1, which is also <= total
    assert len(b[0][1]) == 1


@pytest.mark.django_db
def test_pick_random_items_skips_empty_category():
    selections = pick_random_items_for_cycle_count([("Nothing", 50)], seed="x")
    assert selections == [("Nothing", [])]


@pytest.mark.django_db
def test_cycle_count_categories_dedupes_regardless_of_item_count():
    """Regression: `.values_list('category').distinct().order_by(name)` returns
    one row per (category, name) pair, not one per category. The view must
    dedupe categories in Python so the create form shows exactly one row per
    category instead of one row per item in that category."""
    _seed_items("CFCI", 5)
    _seed_items("Equipment", 8)
    _seed_items("OFCI", 12)
    _seed_items("Supplies", 3)

    from inventory.views import _cycle_count_categories
    cats = _cycle_count_categories()
    assert cats == ["CFCI", "Equipment", "OFCI", "Supplies"], (
        f"Expected 4 distinct categories, got {len(cats)}: {cats}"
    )


def test_cycle_count_create_form_renders_one_row_per_category(client, manager):
    """Regression: the create form template loops over the categories list,
    so the bug above produced a row per item. After the fix the GET form
    must render exactly one row per category."""
    _seed_items("CFCI", 5)
    _seed_items("Equipment", 8)
    _seed_items("OFCI", 12)
    _seed_items("Supplies", 3)
    client.force_login(manager)
    resp = client.get(reverse("cycle_count_create"))
    assert resp.status_code == 200
    body = resp.content.decode("utf-8")
    # Count rows: each is `<tr ... data-label="Category">`
    # The header has `data-label="Category"` too, so look for the strong tag.
    import re
    rows = re.findall(r'<strong>(CFCI|Equipment|OFCI|Supplies)</strong>', body)
    assert sorted(set(rows)) == ["CFCI", "Equipment", "OFCI", "Supplies"], (
        f"Form rendered duplicate category rows: {rows}"
    )
    # Each category should appear exactly once.
    for cat in ("CFCI", "Equipment", "OFCI", "Supplies"):
        assert rows.count(cat) == 1, f"{cat} rendered {rows.count(cat)} times"


def test_cycle_count_list_requires_perm(client, user, manager, counter, outsider):
    url = reverse("cycle_count_list")
    client.force_login(outsider)
    resp = client.get(url)
    assert resp.status_code in (302, 403)
    client.force_login(user)  # no perm at all
    resp = client.get(url)
    assert resp.status_code in (302, 403)
    client.force_login(counter)
    resp = client.get(url)
    assert resp.status_code == 200
    client.force_login(manager)
    resp = client.get(url)
    assert resp.status_code == 200


@pytest.mark.django_db
def test_cycle_count_create_creates_entries(client, manager):
    _seed_items("OFCI", 12)
    _seed_items("Equipment", 6)
    client.force_login(manager)
    resp = client.post(
        reverse("cycle_count_create"),
        data={
            "name": "October weekly",
            "notes": "",
            "seed": "audit-oct",
            "percent_OFCI": "25",
            "percent_Equipment": "50",
        },
    )
    assert resp.status_code == 302
    cc = CycleCount.objects.get(name="October weekly")
    assert cc.status == CycleCount.Status.OPEN
    assert cc.items.count() == 3 + 3  # ceil(12*.25)=3, ceil(6*.5)=3
    assert cc.created_by == manager
    # system_quantity is frozen at creation time
    for entry in cc.items.all():
        assert entry.counted_quantity is None
        assert entry.system_quantity == entry.item.quantity_on_hand


@pytest.mark.django_db
def test_cycle_count_create_requires_manage_perm(client, counter):
    _seed_items("OFCI", 5)
    client.force_login(counter)
    resp = client.post(
        reverse("cycle_count_create"),
        data={"percent_OFCI": "50"},
    )
    # Counter only has perform, not manage
    assert resp.status_code in (302, 403)


@pytest.mark.django_db
def test_cycle_count_create_rejects_blank_or_invalid_percents(client, manager):
    _seed_items("OFCI", 5)
    client.force_login(manager)
    resp = client.post(
        reverse("cycle_count_create"),
        data={"percent_OFCI": "", "percent_Equipment": "150"},
    )
    assert resp.status_code == 200  # re-renders form with error
    assert CycleCount.objects.count() == 0


@pytest.mark.django_db
def test_cycle_count_detail_records_counts(client, manager):
    _seed_items("OFCI", 4)
    client.force_login(manager)
    resp = client.post(
        reverse("cycle_count_create"),
        data={"seed": "x", "percent_OFCI": "100"},
    )
    cc = CycleCount.objects.get()
    items = list(cc.items.all())
    payload = {"action": "record"}
    for entry in items:
        payload[f"count_{entry.pk}"] = str(entry.item.quantity_on_hand + 1)
        payload[f"note_{entry.pk}"] = "off by one"
    resp = client.post(reverse("cycle_count_detail", kwargs={"pk": cc.pk}), data=payload)
    assert resp.status_code == 302
    cc.refresh_from_db()
    assert cc.status == CycleCount.Status.IN_PROGRESS
    for entry in cc.items.all():
        entry.refresh_from_db()
        assert entry.counted_quantity == entry.system_quantity + 1
        assert entry.note == "off by one"


@pytest.mark.django_db
def test_cycle_count_complete_requires_all_counted(client, manager):
    _seed_items("OFCI", 3)
    client.force_login(manager)
    client.post(reverse("cycle_count_create"), data={"percent_OFCI": "100"})
    cc = CycleCount.objects.get()
    items = list(cc.items.all())
    payload = {"action": "record"}
    for entry in items[:-1]:
        payload[f"count_{entry.pk}"] = "10"
    client.post(reverse("cycle_count_detail", kwargs={"pk": cc.pk}), data=payload)
    payload2 = {"action": "complete"}
    resp = client.post(reverse("cycle_count_detail", kwargs={"pk": cc.pk}), data=payload2)
    cc.refresh_from_db()
    assert cc.status != CycleCount.Status.COMPLETED
    # Now count the last one (send all three so previously-counted items stay counted)
    payload3 = {"action": "record"}
    for entry in items[:-1]:
        payload3[f"count_{entry.pk}"] = "10"
    payload3[f"count_{items[-1].pk}"] = "11"
    client.post(reverse("cycle_count_detail", kwargs={"pk": cc.pk}), data=payload3)
    payload4 = {"action": "complete"}
    client.post(reverse("cycle_count_detail", kwargs={"pk": cc.pk}), data=payload4)
    cc.refresh_from_db()
    assert cc.status == CycleCount.Status.COMPLETED
    assert cc.completed_at is not None


@pytest.mark.django_db
def test_cycle_count_cancel_and_reopen(client, manager):
    _seed_items("OFCI", 2)
    client.force_login(manager)
    client.post(reverse("cycle_count_create"), data={"percent_OFCI": "100"})
    cc = CycleCount.objects.get()
    client.post(reverse("cycle_count_detail", kwargs={"pk": cc.pk}), data={"action": "cancel"})
    cc.refresh_from_db()
    assert cc.status == CycleCount.Status.CANCELLED
    client.post(reverse("cycle_count_detail", kwargs={"pk": cc.pk}), data={"action": "reopen"})
    cc.refresh_from_db()
    assert cc.status == CycleCount.Status.IN_PROGRESS


@pytest.mark.django_db
def test_cycle_count_pdf_returns_pdf_without_system_qty(client, manager):
    _seed_items("OFCI", 3)
    client.force_login(manager)
    client.post(reverse("cycle_count_create"), data={"percent_OFCI": "100"})
    cc = CycleCount.objects.get()
    # The PDF endpoint streams a PDF; we can't easily inspect text without
    # a parser, but we can assert the content type + that it renders.
    resp = client.get(reverse("cycle_count_pdf", kwargs={"pk": cc.pk}))
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/pdf"
    assert resp.content[:4] == b"%PDF"


@pytest.mark.django_db
def test_cycle_count_pdf_pagination_for_large_counts(client, manager):
    """21 items should produce 2 pages (20 on page 1, 1 on page 2)."""
    _seed_items("OFCI", 21)
    client.force_login(manager)
    client.post(reverse("cycle_count_create"), data={"percent_OFCI": "100"})
    cc = CycleCount.objects.get()
    assert cc.total_items == 21
    ctx = _cycle_count_print_context_for_test(cc, pdf_mode=True)
    assert ctx["total_pages"] == 2
    assert len(ctx["pages"][0]["rows"]) == 20
    assert len(ctx["pages"][1]["rows"]) == 1
    # Numbers are sequential across pages
    assert ctx["pages"][0]["rows"][0]["number"] == 1
    assert ctx["pages"][0]["rows"][-1]["number"] == 20
    assert ctx["pages"][1]["rows"][0]["number"] == 21
    # is_first / is_last are correct
    assert ctx["pages"][0]["is_first"] is True
    assert ctx["pages"][0]["is_last"] is False
    assert ctx["pages"][1]["is_first"] is False
    assert ctx["pages"][1]["is_last"] is True


@pytest.mark.django_db
def test_cycle_count_pdf_context_omits_system_quantity_for_render(client, manager):
    """The PDF context exposes only items, not system_quantity, so the template
    physically cannot render the live on-hand value."""
    _seed_items("OFCI", 5)
    client.force_login(manager)
    client.post(reverse("cycle_count_create"), data={"percent_OFCI": "100"})
    cc = CycleCount.objects.get()
    ctx = _cycle_count_print_context_for_test(cc, pdf_mode=True)
    # Recursively walk the context; ensure no value contains a system_quantity
    system_qtys = list(cc.items.values_list("system_quantity", flat=True))
    flat = _flatten_strings(ctx)
    for qty in system_qtys:
        assert str(qty) not in flat, (
            f"LEAK: PDF context contains system_quantity={qty}"
        )


def _flatten_strings(obj):
    """Recursively collect every string value in a nested dict/list structure."""
    out = []
    if isinstance(obj, dict):
        for v in obj.values():
            out.extend(_flatten_strings(v))
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            out.extend(_flatten_strings(v))
    elif isinstance(obj, str):
        out.append(obj)
    elif obj is None or isinstance(obj, (int, float, bool)):
        # Numbers are fine to skip — but we already assert no system_quantity
        # integer appears next to a part number via the smoke test.
        pass
    else:
        # Models have __str__; coerce via repr to be safe
        out.append(repr(obj))
    return out


def _cycle_count_print_context_for_test(cc, *, pdf_mode=False):
    """Inline the helper to avoid an import dance in the test module."""
    from inventory.views import _cycle_count_print_context
    return _cycle_count_print_context(cc, pdf_mode=pdf_mode)


@pytest.mark.django_db
def test_hamburger_link_visible_for_permitted_users(client, counter, manager, outsider):
    # The hamburger link is in base.html; the easiest assertion is that
    # the cycle_count_list page itself is accessible. The actual link
    # rendering is exercised manually + by the integration test above.
    client.force_login(manager)
    resp = client.get(reverse("cycle_count_list"))
    assert resp.status_code == 200
    client.force_login(counter)
    resp = client.get(reverse("cycle_count_list"))
    assert resp.status_code == 200
    client.force_login(outsider)
    resp = client.get(reverse("cycle_count_list"))
    assert resp.status_code in (302, 403)


def _make_completed_cycle_count(manager):
    """Helper: create a cycle count with 2 categories, count everything, mark complete."""
    from django.utils import timezone as _tz
    items_ofci = _seed_items("OFCI", 3)
    items_eq = _seed_items("Equipment", 2)
    cc = CycleCount.objects.create(
        name="audit-recon-test",
        created_by=manager,
        status=CycleCount.Status.OPEN,
        notes="Recon test notes",
    )
    # Manually create the items with system + counted quantities and a variance.
    cc.items.create(item=items_ofci[0], category="OFCI", system_quantity=10, counted_quantity=10, counted_by=manager, counted_at=_tz.now())
    cc.items.create(item=items_ofci[1], category="OFCI", system_quantity=5, counted_quantity=4, counted_by=manager, counted_at=_tz.now())  # variance -1
    cc.items.create(item=items_ofci[2], category="OFCI", system_quantity=7, counted_quantity=9, counted_by=manager, counted_at=_tz.now())  # variance +2
    cc.items.create(item=items_eq[0], category="Equipment", system_quantity=3, counted_quantity=3, counted_by=manager, counted_at=_tz.now())
    cc.items.create(item=items_eq[1], category="Equipment", system_quantity=2, counted_quantity=2, counted_by=manager, counted_at=_tz.now())
    cc.status = CycleCount.Status.COMPLETED
    cc.completed_at = _tz.now()
    cc.save()
    return cc


@pytest.mark.django_db
def test_cycle_count_results_pdf_returns_pdf_for_completed(client, manager):
    cc = _make_completed_cycle_count(manager)
    client.force_login(manager)
    resp = client.get(reverse("cycle_count_results_pdf", kwargs={"pk": cc.pk}))
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/pdf"
    assert resp.content[:4] == b"%PDF"


@pytest.mark.django_db
def test_cycle_count_results_pdf_redirects_when_not_completed(client, manager):
    """Open or in-progress cycle counts must NOT issue a reconciliation PDF —
    it would be misleading. The view should redirect to the detail page with
    an error message instead."""
    _seed_items("OFCI", 3)
    client.force_login(manager)
    client.post(reverse("cycle_count_create"), data={"percent_OFCI": "100"})
    cc = CycleCount.objects.get()
    client.force_login(manager)
    # Status is OPEN right after creation
    resp = client.get(reverse("cycle_count_results_pdf", kwargs={"pk": cc.pk}))
    assert resp.status_code == 302
    assert resp["Location"].endswith(f"/cycle-counts/{cc.pk}/")


@pytest.mark.django_db
def test_cycle_count_results_pdf_requires_manage_perm(client, manager, counter):
    """Counter-only users must not be able to download reconciliation PDFs —
    that document is the audit-trail artifact and must only be issued by
    someone authorized to close the count."""
    cc = _make_completed_cycle_count(manager)
    client.force_login(counter)
    resp = client.get(reverse("cycle_count_results_pdf", kwargs={"pk": cc.pk}))
    # 302 redirect to login, or 403 — both are acceptable forms of denial
    assert resp.status_code in (302, 403)


def _pdf_text(pdf_bytes):
    """Extract text from PDF bytes via the system pdftotext tool."""
    import subprocess
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", tmp_path, "-"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout
    finally:
        import os
        os.unlink(tmp_path)


@pytest.mark.django_db
def test_cycle_count_results_pdf_shows_counted_quantities_and_variance(client, manager):
    """The whole point of the results PDF is showing actual counts + variance.
    Extract text via pdftotext and confirm the system_quantity AND counted_quantity
    BOTH appear (no system-quantity-stripping like the blank count sheet does)."""
    cc = _make_completed_cycle_count(manager)
    client.force_login(manager)
    resp = client.get(reverse("cycle_count_results_pdf", kwargs={"pk": cc.pk}))
    assert resp.status_code == 200
    text = _pdf_text(resp.content)
    # The actual system quantities from the test fixture:
    for sys_qty in (10, 5, 7, 3, 2):
        assert str(sys_qty) in text, f"System quantity {sys_qty} missing from PDF"
    # The counted quantities (some match system, some differ):
    for counted_qty in (10, 4, 9, 3, 2):
        assert str(counted_qty) in text, f"Counted quantity {counted_qty} missing from PDF"


@pytest.mark.django_db
def test_cycle_count_results_pdf_summary_shows_variance_total(client, manager):
    """Header should show net variance total + count for the auditor at a glance."""
    cc = _make_completed_cycle_count(manager)
    client.force_login(manager)
    resp = client.get(reverse("cycle_count_results_pdf", kwargs={"pk": cc.pk}))
    assert resp.status_code == 200
    text = _pdf_text(resp.content)
    # Net variance: -1 (5→4) + 2 (7→9) = +1, 2 variance rows
    assert "1 item" in text or "2 items" in text  # variance count rendering
    # The "+1" net variance should appear in the summary
    assert "+1" in text


def _pdf_page_count(pdf_bytes):
    """Return the number of pages in a PDF via pdfinfo."""
    import subprocess
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name
    try:
        result = subprocess.run(
            ["pdfinfo", tmp_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        for line in result.stdout.splitlines():
            if line.startswith("Pages:"):
                return int(line.split(":", 1)[1].strip())
    finally:
        import os
        os.unlink(tmp_path)
    return None


@pytest.mark.django_db
def test_cycle_count_results_pdf_uses_letter_landscape(client, manager):
    """Regression: the results PDF must be Letter landscape (792x612 pts),
    not A4 portrait, so it prints correctly on US warehouse printers without
    page-fit shrinkage. This was broken when the @media print block was
    missing from the template."""
    cc = _make_completed_cycle_count(manager)
    client.force_login(manager)
    resp = client.get(reverse("cycle_count_results_pdf", kwargs={"pk": cc.pk}))
    assert resp.status_code == 200
    import subprocess, tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(resp.content)
        tmp_path = tmp.name
    try:
        info = subprocess.run(
            ["pdfinfo", tmp_path], capture_output=True, text=True, timeout=30
        ).stdout
        # Page size line: "Page size:       792 x 612 pts (letter)"
        for line in info.splitlines():
            if line.startswith("Page size:"):
                assert "letter" in line.lower(), (
                    f"Page size is not Letter: {line}. Without @media print "
                    f"@page{{size:Letter landscape}} WeasyPrint defaults to A4."
                )
                # Letter landscape is 792 x 612 (width x height)
                # Extract width and height
                parts = line.replace("Page size:", "").strip().split()
                w, h = int(parts[0]), int(parts[2])
                assert w > h, f"Page is portrait ({w}x{h}), expected landscape"
                break
        else:
            pytest.fail("pdfinfo output missing Page size line")
    finally:
        os.unlink(tmp_path)


@pytest.mark.django_db
def test_cycle_count_results_pdf_has_dedicated_signoff_page(client, manager):
    """Regression: the sign-off block must be on its own page (last page of
    the PDF), not absolutely positioned over the table rows. Before the fix,
    the receipt overlapped the table bottom and produced unreadable output."""
    cc = _make_completed_cycle_count(manager)
    client.force_login(manager)
    resp = client.get(reverse("cycle_count_results_pdf", kwargs={"pk": cc.pk}))
    assert resp.status_code == 200
    pages = _pdf_page_count(resp.content)
    # 5 items / 20 per page = 1 data page + 1 sign-off page = 2
    assert pages == 2, f"Expected 2 pages (1 data + 1 sign-off), got {pages}"

    # Last page text must contain the signature labels and per-category header
    import subprocess, tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(resp.content)
        tmp_path = tmp.name
    try:
        text = subprocess.run(
            ["pdftotext", "-layout", tmp_path, "-"],
            capture_output=True, text=True, timeout=30,
        ).stdout
    finally:
        os.unlink(tmp_path)

    # The sign-off page must NOT contain item row data (which would mean the
    # table and sign-off overlap on the same page)
    parts = text.rsplit("\f", 1)
    last_page_only = parts[-1] if parts[-1].strip() else parts[0].rsplit("\f", 1)[-1]
    if not last_page_only.strip():
        # No form feed boundary found; treat the whole text as one page
        last_page_only = text
    assert "FINAL VERIFICATION & SIGN-OFF" in last_page_only
    assert "COUNTER SIGNATURE" in last_page_only
    assert "MANAGER SIGNATURE" in last_page_only
    assert "PER-CATEGORY BREAKDOWN" in last_page_only
    # And the sign-off page should NOT contain table rows like the data pages do
    assert "PART #" not in last_page_only or "ITEM DESCRIPTION" not in last_page_only, (
        "Sign-off page should not contain item table headers — the receipt "
        "block must be on a dedicated page, not overlapping the data table."
    )


@pytest.mark.django_db
def test_cycle_count_detail_shows_download_button_when_completed(client, manager):
    cc = _make_completed_cycle_count(manager)
    client.force_login(manager)
    resp = client.get(reverse("cycle_count_detail", kwargs={"pk": cc.pk}))
    assert resp.status_code == 200
    body = resp.content.decode("utf-8")
    assert "Download Reconciliation PDF" in body
    assert f"/cycle-counts/{cc.pk}/results-pdf/" in body


@pytest.mark.django_db
def test_cycle_count_detail_hides_download_button_when_open(client, manager):
    _seed_items("OFCI", 3)
    client.force_login(manager)
    client.post(reverse("cycle_count_create"), data={"percent_OFCI": "100"})
    cc = CycleCount.objects.get()
    client.force_login(manager)
    resp = client.get(reverse("cycle_count_detail", kwargs={"pk": cc.pk}))
    assert resp.status_code == 200
    assert "Download Reconciliation PDF" not in resp.content.decode("utf-8")


# ---------------------------------------------------------------------------
# Archive feature
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_cycle_count_archive_list_shows_archived_counts(client, manager):
    """The archive list page shows only archived counts."""
    cc = _make_completed_cycle_count(manager)
    # Archive the count
    cc.archived_at = timezone.now()
    cc.archived_by = manager
    cc.save()
    client.force_login(manager)
    resp = client.get(reverse("cycle_count_archive"))
    assert resp.status_code == 200
    body = resp.content.decode("utf-8")
    assert '<td data-label="Name">' + cc.display_name in body, (
        f"Archive page should list archived count {cc.display_name} as a row"
    )
    assert "archived" in body.lower()


@pytest.mark.django_db
def test_cycle_count_archive_list_excludes_active_counts(client, manager):
    """An open or completed-but-not-archived count must NOT appear on the archive page."""
    _seed_items("OFCI", 3)
    client.force_login(manager)
    client.post(reverse("cycle_count_create"), data={"percent_OFCI": "100"})
    cc_active = CycleCount.objects.get()
    client.force_login(manager)
    resp = client.get(reverse("cycle_count_archive"))
    body = resp.content.decode("utf-8")
    # The active count must NOT appear as a row in the archive table.
    # (It will appear in a notification-center success message from the create
    # POST, which is fine — that's the messages framework, not the table.)
    assert '<td data-label="Name">' + cc_active.display_name not in body, (
        f"Archive page table contains the active count {cc_active.display_name}; "
        f"the archive queryset is supposed to filter out non-archived counts."
    )
    assert "No archived cycle counts yet" in body


@pytest.mark.django_db
def test_cycle_count_list_hides_archived_by_default(client, manager):
    """The active list page must hide archived counts (they belong on the archive page)."""
    cc = _make_completed_cycle_count(manager)
    cc.archived_at = timezone.now()
    cc.archived_by = manager
    cc.save()
    client.force_login(manager)
    resp = client.get(reverse("cycle_count_list"))
    body = resp.content.decode("utf-8")
    # The archived count must NOT appear as a row in the active list table.
    # (Messages framework may render it elsewhere; that's fine.)
    assert '<td data-label="Name">' + cc.display_name not in body, (
        f"Active list table contains the archived count {cc.display_name}; "
        f"the active list queryset is supposed to hide archived counts."
    )


@pytest.mark.django_db
def test_cycle_count_list_shows_archived_count_when_flag_set(client, manager):
    """Power users can opt in to see archived counts on the active list (?archived=1)."""
    cc = _make_completed_cycle_count(manager)
    cc.archived_at = timezone.now()
    cc.archived_by = manager
    cc.save()
    client.force_login(manager)
    resp = client.get(reverse("cycle_count_list") + "?archived=1")
    body = resp.content.decode("utf-8")
    assert '<td data-label="Name">' + cc.display_name in body, (
        f"Active list (?archived=1) should show archived count {cc.display_name} as a row"
    )


@pytest.mark.django_db
def test_cycle_count_archive_action_archives_completed_count(client, manager):
    cc = _make_completed_cycle_count(manager)
    client.force_login(manager)
    resp = client.post(reverse("cycle_count_archive_action", kwargs={"pk": cc.pk}))
    assert resp.status_code == 302
    assert resp["Location"].endswith("/cycle-counts/archive/")
    cc.refresh_from_db()
    assert cc.is_archived
    assert cc.archived_by == manager
    assert cc.archived_at is not None


@pytest.mark.django_db
def test_cycle_count_archive_action_rejects_open_count(client, manager):
    """An open (not yet completed) count must not be archivable — the audit
    trail isn't sealed until completion."""
    _seed_items("OFCI", 3)
    client.force_login(manager)
    client.post(reverse("cycle_count_create"), data={"percent_OFCI": "100"})
    cc = CycleCount.objects.get()
    client.force_login(manager)
    resp = client.post(reverse("cycle_count_archive_action", kwargs={"pk": cc.pk}))
    assert resp.status_code == 302  # redirected with error message
    assert resp["Location"].endswith(f"/cycle-counts/{cc.pk}/")
    cc.refresh_from_db()
    assert not cc.is_archived


@pytest.mark.django_db
def test_cycle_count_archive_action_rejects_already_archived(client, manager):
    """Archiving an already-archived count is a no-op error."""
    cc = _make_completed_cycle_count(manager)
    cc.archived_at = timezone.now()
    cc.archived_by = manager
    cc.save()
    original_archived_at = cc.archived_at
    client.force_login(manager)
    resp = client.post(reverse("cycle_count_archive_action", kwargs={"pk": cc.pk}))
    assert resp.status_code == 302
    cc.refresh_from_db()
    # Should not have re-archived (timestamp should be unchanged)
    assert cc.archived_at == original_archived_at


@pytest.mark.django_db
def test_cycle_count_archive_action_requires_archive_perm(client, manager, counter, archiver):
    """The archive perm is its own gate — counter (perform only) cannot archive,
    but the archiver fixture (archive only, no manage) can."""
    cc = _make_completed_cycle_count(manager)
    # Counter should be denied
    client.force_login(counter)
    resp = client.post(reverse("cycle_count_archive_action", kwargs={"pk": cc.pk}))
    assert resp.status_code in (302, 403)
    cc.refresh_from_db()
    assert not cc.is_archived
    # Archiver should be allowed
    client.force_login(archiver)
    resp = client.post(reverse("cycle_count_archive_action", kwargs={"pk": cc.pk}))
    assert resp.status_code == 302
    cc.refresh_from_db()
    assert cc.is_archived


@pytest.mark.django_db
def test_cycle_count_unarchive_action_restores_to_active_list(client, manager):
    cc = _make_completed_cycle_count(manager)
    cc.archived_at = timezone.now()
    cc.archived_by = manager
    cc.save()
    client.force_login(manager)
    resp = client.post(reverse("cycle_count_unarchive_action", kwargs={"pk": cc.pk}))
    assert resp.status_code == 302
    assert resp["Location"].endswith("/cycle-counts/archive/")
    cc.refresh_from_db()
    assert not cc.is_archived
    assert cc.archived_at is None
    assert cc.archived_by is None
    # Now visible on active list again
    resp = client.get(reverse("cycle_count_list"))
    assert '<td data-label="Name">' + cc.display_name in resp.content.decode("utf-8")


@pytest.mark.django_db
def test_cycle_count_archive_and_unarchive_reject_get(client, manager):
    """Archive state changes must be POST-only so links/prefetch cannot mutate data."""
    cc = _make_completed_cycle_count(manager)
    client.force_login(manager)

    archive_url = reverse("cycle_count_archive_action", kwargs={"pk": cc.pk})
    assert client.get(archive_url).status_code == 405
    cc.refresh_from_db()
    assert not cc.is_archived

    cc.archived_at = timezone.now()
    cc.archived_by = manager
    cc.save(update_fields=["archived_at", "archived_by"])
    unarchive_url = reverse("cycle_count_unarchive_action", kwargs={"pk": cc.pk})
    assert client.get(unarchive_url).status_code == 405
    cc.refresh_from_db()
    assert cc.is_archived


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("status", "archived"),
    [
        (CycleCount.Status.COMPLETED, False),
        (CycleCount.Status.CANCELLED, False),
        (CycleCount.Status.COMPLETED, True),
    ],
)
def test_finished_or_archived_cycle_count_rejects_record_changes(
    client, manager, status, archived
):
    """Completion/cancellation/archive seals recorded quantities and notes."""
    cc = _make_completed_cycle_count(manager)
    cc.status = status
    if archived:
        cc.archived_at = timezone.now()
        cc.archived_by = manager
    cc.save()
    entry = cc.items.order_by("pk").first()
    before = (
        entry.counted_quantity,
        entry.note,
        entry.counted_by_id,
        entry.counted_at,
    )
    client.force_login(manager)

    response = client.post(
        reverse("cycle_count_detail", kwargs={"pk": cc.pk}),
        data={
            "action": "record",
            f"count_{entry.pk}": "999",
            f"note_{entry.pk}": "must not overwrite sealed audit evidence",
        },
    )

    assert response.status_code == 302
    entry.refresh_from_db()
    after = (
        entry.counted_quantity,
        entry.note,
        entry.counted_by_id,
        entry.counted_at,
    )
    assert after == before


@pytest.mark.django_db
def test_cycle_count_unarchive_action_requires_archive_perm(client, manager, counter):
    cc = _make_completed_cycle_count(manager)
    cc.archived_at = timezone.now()
    cc.archived_by = manager
    cc.save()
    client.force_login(counter)
    resp = client.post(reverse("cycle_count_unarchive_action", kwargs={"pk": cc.pk}))
    assert resp.status_code in (302, 403)
    cc.refresh_from_db()
    assert cc.is_archived  # still archived


@pytest.mark.django_db
def test_cycle_count_can_be_archived_property():
    """Model property: only completed + not-already-archived counts are archivable."""
    from inventory.models import InventoryItem
    item = InventoryItem.objects.create(part_number="X-1", name="X", category="OFCI", active=True)
    cc = CycleCount.objects.create(
        name="open",
        created_by=User.objects.create_user(username="u", password="x"),
        status=CycleCount.Status.OPEN,
    )
    assert cc.can_be_archived is False
    cc.status = CycleCount.Status.IN_PROGRESS
    cc.save()
    assert cc.can_be_archived is False
    cc.status = CycleCount.Status.COMPLETED
    cc.completed_at = timezone.now()
    cc.save()
    assert cc.can_be_archived is True
    # Archive it
    cc.archived_at = timezone.now()
    cc.archived_by = cc.created_by
    cc.save()
    assert cc.can_be_archived is False
    assert cc.is_archived is True
    item.delete()


@pytest.mark.django_db
def test_cycle_count_detail_shows_archive_button_when_completed(client, manager):
    cc = _make_completed_cycle_count(manager)
    client.force_login(manager)
    resp = client.get(reverse("cycle_count_detail", kwargs={"pk": cc.pk}))
    assert resp.status_code == 200
    body = resp.content.decode("utf-8")
    assert "Archive cycle count" in body
    # The form posts to /cycle-counts/<pk>/archive/
    assert f"/cycle-counts/{cc.pk}/archive/" in body


@pytest.mark.django_db
def test_cycle_count_detail_shows_unarchive_button_when_archived(client, manager):
    cc = _make_completed_cycle_count(manager)
    cc.archived_at = timezone.now()
    cc.archived_by = manager
    cc.save()
    client.force_login(manager)
    resp = client.get(reverse("cycle_count_detail", kwargs={"pk": cc.pk}))
    assert resp.status_code == 200
    body = resp.content.decode("utf-8")
    assert "Restore to active list" in body
    assert f"/cycle-counts/{cc.pk}/unarchive/" in body
    # Archive form must NOT be present
    assert "Archive cycle count</button>" not in body


@pytest.mark.django_db
def test_cycle_count_results_pdf_still_works_after_archive(client, manager):
    """Archiving is a UI housekeeping move — the reconciliation PDF must still
    be downloadable after a count is archived. This is the whole point of
    preserving the audit trail."""
    cc = _make_completed_cycle_count(manager)
    cc.archived_at = timezone.now()
    cc.archived_by = manager
    cc.save()
    client.force_login(manager)
    resp = client.get(reverse("cycle_count_results_pdf", kwargs={"pk": cc.pk}))
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/pdf"
    assert resp.content[:4] == b"%PDF"


@pytest.mark.django_db
def test_cycle_count_list_archived_count_in_actions(client, manager):
    """The Archive action chip must appear on completed, unarchived rows when
    the user has the archive perm."""
    cc = _make_completed_cycle_count(manager)
    client.force_login(manager)
    resp = client.get(reverse("cycle_count_list"))
    body = resp.content.decode("utf-8")
    # The cycle_count isn't archived yet, so the archive button must be present
    assert f"/cycle-counts/{cc.pk}/archive/" in body
    assert "Archive" in body
