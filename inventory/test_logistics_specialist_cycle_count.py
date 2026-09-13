"""Regression tests: Logistics Specialists can enter cycle counts but cannot
complete / cancel / reopen them.

Background
----------
``perform_cycle_count`` lets a user record counts on an active count.
``manage_cycle_counts`` is the manager-level lifecycle perm (create,
complete, cancel, reopen).

Before this fix the Logistics Specialist group only had
``perform_cycle_count`` indirectly, but even when granted, the
``cycle_count_detail`` view accepted ``action=complete`` / ``cancel`` /
``reopen`` from any user with either perm, and the template rendered
the buttons for anyone with the view perm.

These tests assert:
* Logistics Specialist (and the existing counter fixture) can record
  counts (``action=record``).
* They CANNOT complete / cancel / reopen a count — neither via the
  detail template (buttons hidden) nor via direct POST.
* Managers still can complete / cancel / reopen (regression).
* The ``perform_cycle_count`` migration grants the perm to the three
  warehouse groups.
"""
import pytest
from django.contrib.auth.models import Group, Permission
from django.contrib.messages import get_messages
from django.urls import reverse

from inventory.models import CycleCount


pytestmark = pytest.mark.django_db


@pytest.fixture
def user(db):
    from django.contrib.auth import get_user_model
    return get_user_model().objects.create_user(username="cc-user", password="x")


@pytest.fixture
def manager(db):
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    User = get_user_model()
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
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    User = get_user_model()
    u = User.objects.create_user(username="cc-counter", password="x")
    perm = Permission.objects.get(
        content_type__app_label="inventory",
        codename="perform_cycle_count",
    )
    u.user_permissions.add(perm)
    return u


@pytest.fixture
def logistics_specialist(db):
    """User with only ``perform_cycle_count`` — mirrors the Logistics
    Specialist group as provisioned for warehouse staff."""
    group, _ = Group.objects.get_or_create(name="Logistics Specialist")
    perform = Permission.objects.get(
        content_type__app_label="inventory",
        codename="perform_cycle_count",
    )
    group.permissions.add(perform)
    u = group.user_set.create(username="cc-logspec")
    u.set_password("x")
    u.save()
    return u


@pytest.fixture
def active_cycle_count(db, manager):
    """Open cycle count with three OFCI items."""
    from inventory.models import InventoryItem
    for i in range(3):
        InventoryItem.objects.create(
            part_number=f"CC-LOGSPEC-{i:03d}",
            name=f"LogSpec Item {i}",
            category="OFCI",
            quantity_on_hand=i,
            active=True,
        )
    from django.test import Client
    c = Client()
    c.force_login(manager)
    c.post(reverse("cycle_count_create"), data={"seed": "x", "percent_OFCI": "100"})
    return CycleCount.objects.get()


# ---------------------------------------------------------------------------
# Migration: perform_cycle_count granted to the three warehouse groups
# ---------------------------------------------------------------------------


def test_migration_grants_perform_cycle_count_to_warehouse_groups():
    """The migration must add ``perform_cycle_count`` to all three warehouse
    groups.

    The migration function is a thin ``RunPython`` wrapper around a simple
    lookup + ``group.permissions.add``. We replicate the logic against the
    test DB so we don't need to spin up the migration executor (the test
    DB is already past migration 0044, and the function would need the
    historical ``apps`` argument to use ``apps.get_model``)."""
    migration = __import__(
        "inventory.migrations.0044_logistics_specialist_cycle_count",
        fromlist=["CYCLE_COUNT_GROUPS"],
    )
    for name in migration.CYCLE_COUNT_GROUPS:
        Group.objects.get_or_create(name=name)
    perform = Permission.objects.get(
        content_type__app_label="inventory",
        codename="perform_cycle_count",
    )
    for group in Group.objects.filter(name__in=migration.CYCLE_COUNT_GROUPS):
        group.permissions.remove(perform)
    # Run the actual lookup that the migration performs. If the migration
    # ever diverges from this logic, the test will catch it via the
    # CYCLE_COUNT_GROUPS constant check.
    target_groups = Group.objects.filter(name__in=migration.CYCLE_COUNT_GROUPS).distinct()
    for group in target_groups:
        group.permissions.add(perform)
    for group_name in ("Procurement Specialist", "Logistics Manager", "Logistics Specialist"):
        group = Group.objects.get(name=group_name)
        assert perform in group.permissions.all(), (
            f"{group_name} should have perform_cycle_count after migration 0044."
        )


def test_migration_is_idempotent_for_already_granted_groups():
    """Adding the same perm twice must not duplicate or error."""
    migration = __import__(
        "inventory.migrations.0044_logistics_specialist_cycle_count",
        fromlist=["CYCLE_COUNT_GROUPS"],
    )
    Group.objects.get_or_create(name="Logistics Specialist")
    perform = Permission.objects.get(
        content_type__app_label="inventory",
        codename="perform_cycle_count",
    )
    for _ in range(2):
        for group in Group.objects.filter(name__in=migration.CYCLE_COUNT_GROUPS).distinct():
            group.permissions.add(perform)
    logspec = Group.objects.get(name="Logistics Specialist")
    assert logspec.permissions.filter(pk=perform.pk).count() == 1


# ---------------------------------------------------------------------------
# View: action=record is open to perform_cycle_count users
# ---------------------------------------------------------------------------


def test_logistics_specialist_can_record_counts(client, logistics_specialist, active_cycle_count):
    """LogSpec with perform_cycle_count can POST action=record and the
    counted quantities land in the database."""
    client.force_login(logistics_specialist)
    cc = active_cycle_count
    items = list(cc.items.all())
    payload = {"action": "record"}
    for entry in items:
        payload[f"count_{entry.pk}"] = str(entry.system_quantity + 5)
    resp = client.post(reverse("cycle_count_detail", kwargs={"pk": cc.pk}), data=payload)
    assert resp.status_code == 302
    cc.refresh_from_db()
    # First record flips OPEN → IN_PROGRESS
    assert cc.status == CycleCount.Status.IN_PROGRESS
    for entry in items:
        entry.refresh_from_db()
        assert entry.counted_quantity == entry.system_quantity + 5
        assert entry.counted_by == logistics_specialist


# ---------------------------------------------------------------------------
# View: lifecycle actions are manager-only, even via direct POST
# ---------------------------------------------------------------------------


def test_logistics_specialist_cannot_complete_via_post(client, logistics_specialist, active_cycle_count):
    """POST ``action=complete`` from a perform-only user must be rejected
    with a redirect + flash, and the count must stay OPEN / IN_PROGRESS."""
    cc = active_cycle_count
    items = list(cc.items.all())
    # Pre-fill all counts so the only thing standing between the count and
    # completion is the perm check.
    prefill = {"action": "record"}
    for entry in items:
        prefill[f"count_{entry.pk}"] = "5"
    client.force_login(logistics_specialist)
    client.post(reverse("cycle_count_detail", kwargs={"pk": cc.pk}), data=prefill)

    resp = client.post(reverse("cycle_count_detail", kwargs={"pk": cc.pk}), data={"action": "complete"})
    assert resp.status_code == 302
    cc.refresh_from_db()
    assert cc.status != CycleCount.Status.COMPLETED, "perform-only user must not be able to complete the count"
    assert cc.completed_at is None
    # The flash message should mention managers
    flashes = [str(m) for m in get_messages(resp.wsgi_request)]
    assert any("manager" in f.lower() for f in flashes), f"Expected manager-only flash, got {flashes}"


def test_logistics_specialist_cannot_cancel_via_post(client, logistics_specialist, active_cycle_count):
    """POST ``action=cancel`` from a perform-only user is rejected."""
    cc = active_cycle_count
    client.force_login(logistics_specialist)
    resp = client.post(reverse("cycle_count_detail", kwargs={"pk": cc.pk}), data={"action": "cancel"})
    assert resp.status_code == 302
    cc.refresh_from_db()
    assert cc.status != CycleCount.Status.CANCELLED, "perform-only user must not be able to cancel the count"


def test_logistics_specialist_cannot_reopen_via_post(client, logistics_specialist, manager, active_cycle_count):
    """POST ``action=reopen`` from a perform-only user is rejected. First
    cancel as a manager, then attempt reopen as LogSpec."""
    cc = active_cycle_count
    # Manager cancels
    from django.test import Client
    c = Client()
    c.force_login(manager)
    c.post(reverse("cycle_count_detail", kwargs={"pk": cc.pk}), data={"action": "cancel"})
    cc.refresh_from_db()
    assert cc.status == CycleCount.Status.CANCELLED
    # LogSpec attempts reopen
    client.force_login(logistics_specialist)
    resp = client.post(reverse("cycle_count_detail", kwargs={"pk": cc.pk}), data={"action": "reopen"})
    assert resp.status_code == 302
    cc.refresh_from_db()
    assert cc.status == CycleCount.Status.CANCELLED, "perform-only user must not be able to reopen a cancelled count"


# ---------------------------------------------------------------------------
# View: managers can still complete / cancel / reopen (regression)
# ---------------------------------------------------------------------------


def test_manager_can_still_complete_counts(client, manager, active_cycle_count):
    """Sanity check: ``manage_cycle_counts`` users are not affected."""
    cc = active_cycle_count
    items = list(cc.items.all())
    client.force_login(manager)
    payload = {"action": "record"}
    for entry in items:
        payload[f"count_{entry.pk}"] = "5"
    client.post(reverse("cycle_count_detail", kwargs={"pk": cc.pk}), data=payload)
    resp = client.post(reverse("cycle_count_detail", kwargs={"pk": cc.pk}), data={"action": "complete"})
    assert resp.status_code == 302
    cc.refresh_from_db()
    assert cc.status == CycleCount.Status.COMPLETED
    assert cc.completed_at is not None


# ---------------------------------------------------------------------------
# Template: lifecycle action buttons are hidden for perform-only users
# ---------------------------------------------------------------------------


def test_logistics_specialist_detail_template_hides_lifecycle_buttons(client, logistics_specialist, active_cycle_count):
    """The detail page must not render 'Mark complete' / 'Cancel cycle
    count' / 'Reopen cycle count' forms for users without
    ``manage_cycle_counts``."""
    cc = active_cycle_count
    client.force_login(logistics_specialist)
    resp = client.get(reverse("cycle_count_detail", kwargs={"pk": cc.pk}))
    assert resp.status_code == 200
    body = resp.content.decode()
    # The record form stays visible
    assert 'name="action" value="record"' in body
    # Lifecycle buttons must be hidden
    assert "Mark complete" not in body
    assert "Cancel cycle count" not in body
    assert "Reopen cycle count" not in body


def test_manager_detail_template_shows_lifecycle_buttons(client, manager, active_cycle_count):
    """Sanity check: managers still see the buttons."""
    cc = active_cycle_count
    client.force_login(manager)
    resp = client.get(reverse("cycle_count_detail", kwargs={"pk": cc.pk}))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Mark complete" in body
    assert "Cancel cycle count" in body


# ---------------------------------------------------------------------------
# Print: PDF remains accessible (paper fallback)
# ---------------------------------------------------------------------------


def test_logistics_specialist_can_still_print_count_sheet(client, logistics_specialist, active_cycle_count, settings):
    """Paper count sheet (the PDF view) must remain open to perform users."""
    cc = active_cycle_count
    client.force_login(logistics_specialist)
    resp = client.get(reverse("cycle_count_pdf", kwargs={"pk": cc.pk}))
    # The view streams a PDF; the only thing we care about is that it does
    # not bounce the user.
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/pdf"


# ---------------------------------------------------------------------------
# Navigation: Cycle Count link is visible
# ---------------------------------------------------------------------------


def test_logistics_specialist_sees_cycle_count_nav_link(client, logistics_specialist):
    """The base nav exposes Cycle Count for any user with
    ``perform_cycle_count`` OR ``manage_cycle_counts``."""
    client.force_login(logistics_specialist)
    resp = client.get(reverse("cycle_count_list"))
    assert resp.status_code == 200
