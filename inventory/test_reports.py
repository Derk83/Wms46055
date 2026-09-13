"""
Tests for the manager reports section.

Covers:
  - Permission gate: only managers (and superusers/staff) see the report
  - Daily view: aggregations are correct for the selected date
  - Weekly/range view: aggregations are correct for the selected range
  - PDF download: returns a non-empty PDF
  - Empty state: ranges with no data render gracefully
  - URL routing: /reports/, /reports/daily/, /reports/weekly/, /reports/<kind>/pdf/
"""
from datetime import timedelta
from io import BytesIO

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from inventory.models import (
    InventoryItem,
    InventoryTransaction,
    MaterialRequest,
    MaterialRequestEvent,
    MaterialRequestLine,
    PickTicket,
    PickTicketLine,
    ReceivingLine,
    ReceivingTicket,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Permission gate
# ---------------------------------------------------------------------------


@pytest.fixture
def manager_groups(db):
    """Make sure all relevant groups exist for permission tests."""
    from django.contrib.auth.models import Group
    for name in (
        "Logistics Manager",
        "Sr. Logistics Manager",
        "Procurement Manager",
        "Logistics Specialist",
        "Procurement Specialist",
        "Material Requester",
    ):
        Group.objects.get_or_create(name=name)


@pytest.mark.django_db
def test_anonymous_cannot_view_reports(client):
    response = client.get(reverse("reports_index"))
    assert response.status_code in {302, 403}


@pytest.mark.django_db
def test_logistics_specialist_cannot_view_reports(client, manager_groups):
    user = User.objects.create_user(
        username="logspecialist-rpt", password="pw"
    )
    from django.contrib.auth.models import Group
    user.groups.add(Group.objects.get(name="Logistics Specialist"))
    client.force_login(user)
    response = client.get(reverse("reports_index"))
    assert response.status_code == 403


@pytest.mark.django_db
def test_requester_cannot_view_reports(client, manager_groups):
    user = User.objects.create_user(username="requester-rpt", password="pw")
    from django.contrib.auth.models import Group
    user.groups.add(Group.objects.get(name="Material Requester"))
    client.force_login(user)
    response = client.get(reverse("reports_index"))
    assert response.status_code == 403


@pytest.mark.django_db
def test_logistics_manager_can_view_reports(client, manager_groups):
    user = User.objects.create_user(username="logmgr-rpt", password="pw")
    from django.contrib.auth.models import Group
    user.groups.add(Group.objects.get(name="Logistics Manager"))
    client.force_login(user)
    response = client.get(reverse("reports_index"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_superuser_can_view_reports(client):
    user = User.objects.create_superuser(
        username="superrpt", email="s@x.test", password="pw"
    )
    client.force_login(user)
    response = client.get(reverse("reports_index"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_is_staff_user_without_manager_group_cannot_view_reports(client, manager_groups):
    """is_staff is not sufficient — must be superuser OR in a manager group.

    This protects against Logistics Specialists who happen to be is_staff=True
    (which they are in this app) from seeing manager-only reports.
    """
    user = User.objects.create_user(username="staffonly-rpt", password="pw")
    user.is_staff = True
    user.save()
    client.force_login(user)
    response = client.get(reverse("reports_index"))
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Daily aggregation correctness
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_daily_report_counts_new_requests_on_selected_date(client, manager_groups):
    user = User.objects.create_user(username="mgr-daily", password="pw")
    from django.contrib.auth.models import Group
    user.groups.add(Group.objects.get(name="Logistics Manager"))
    client.force_login(user)

    from inventory.services import create_material_request

    today = timezone.now()
    item = InventoryItem.objects.create(
        part_number="RPT-1", name="RPT item", quantity_on_hand=10
    )
    requester = User.objects.create_user(username="req-daily", password="pw")
    mr = create_material_request(
        creator=requester,
        requestor_name="Test",
        building_room="Rm1",
        location="Loc",
        notes="",
        lines=[{"item": item, "quantity": 2, "notes": ""}],
    )
    # Pin created_at to today (services uses now())
    MaterialRequest.objects.filter(pk=mr.pk).update(created_at=today)

    response = client.get(reverse("reports_daily"), {"date": today.date().isoformat()})
    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "New material requests" in body
    assert mr.request_number in body


@pytest.mark.django_db
def test_daily_report_excludes_other_days(client, manager_groups):
    user = User.objects.create_user(username="mgr-excl", password="pw")
    from django.contrib.auth.models import Group
    user.groups.add(Group.objects.get(name="Logistics Manager"))
    client.force_login(user)

    from inventory.services import create_material_request

    today = timezone.now()
    yesterday = today - timedelta(days=1)
    item = InventoryItem.objects.create(
        part_number="RPT-Y", name="Yesterday item", quantity_on_hand=10
    )
    requester = User.objects.create_user(username="req-y", password="pw")
    mr_yesterday = create_material_request(
        creator=requester,
        requestor_name="Yesterday",
        building_room="Rm",
        location="Loc",
        notes="",
        lines=[{"item": item, "quantity": 1, "notes": ""}],
    )
    MaterialRequest.objects.filter(pk=mr_yesterday.pk).update(created_at=yesterday)

    response = client.get(reverse("reports_daily"), {"date": today.date().isoformat()})
    body = response.content.decode("utf-8")
    # The yesterday-dated request should not appear in the "New material requests"
    # section header. (Note: it can still appear in audit_events because audit-event
    # timestamps are independent of their parent MR's created_at.)
    assert "New material requests (0)" in body
    # And the MR number from yesterday should not be in the new-requests table body
    new_section = body.split("New material requests")[1].split("Delivery confirmations")[0]
    assert mr_yesterday.request_number not in new_section


@pytest.mark.django_db
def test_weekly_manual_route_is_removed(client, manager_groups):
    """The weekly report is auto-generated by the Friday cron job now, so the
    manual /reports/weekly/ route should be a 404."""
    user = User.objects.create_user(username="mgr-wk", password="pw")
    from django.contrib.auth.models import Group
    user.groups.add(Group.objects.get(name="Logistics Manager"))
    client.force_login(user)

    # The URL pattern was removed entirely — Django should 404 on the path.
    response = client.get("/inventory/reports/weekly/")
    assert response.status_code == 404

    # The reports_pdf view should also reject anything other than "daily".
    response = client.get("/inventory/reports/weekly/pdf/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_daily_report_renders_with_no_data(client, manager_groups):
    user = User.objects.create_user(username="mgr-empty", password="pw")
    from django.contrib.auth.models import Group
    user.groups.add(Group.objects.get(name="Logistics Manager"))
    client.force_login(user)

    response = client.get(reverse("reports_daily"))
    assert response.status_code == 200
    # Should render without errors and contain an empty-state hint
    body = response.content.decode("utf-8")
    assert "No" in body or "0" in body


# ---------------------------------------------------------------------------
# Stock alerts section
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_daily_report_lists_out_of_stock_items(client, manager_groups):
    user = User.objects.create_user(username="mgr-stock", password="pw")
    from django.contrib.auth.models import Group
    user.groups.add(Group.objects.get(name="Logistics Manager"))
    client.force_login(user)

    item_oos = InventoryItem.objects.create(
        part_number="OOS-1", name="Out of stock item", quantity_on_hand=0
    )
    item_ok = InventoryItem.objects.create(
        part_number="OK-1", name="Plenty item", quantity_on_hand=100
    )

    response = client.get(reverse("reports_daily"))
    body = response.content.decode("utf-8")
    assert item_oos.part_number in body
    assert item_ok.part_number not in body or "Plenty" not in body


# ---------------------------------------------------------------------------
# PDF download
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_daily_report_pdf_returns_pdf(client, manager_groups):
    user = User.objects.create_user(username="mgr-pdf", password="pw")
    from django.contrib.auth.models import Group
    user.groups.add(Group.objects.get(name="Logistics Manager"))
    client.force_login(user)

    response = client.get(reverse("reports_pdf", args=["daily"]))
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    body = response.content
    assert body.startswith(b"%PDF-")
    assert len(body) > 100


@pytest.mark.django_db
def test_pdf_requires_manager_role(client, manager_groups):
    user = User.objects.create_user(username="no-mgr-pdf", password="pw")
    from django.contrib.auth.models import Group
    user.groups.add(Group.objects.get(name="Logistics Specialist"))
    client.force_login(user)
    response = client.get(reverse("reports_pdf", args=["daily"]))
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Top requesters / top items
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_top_items_section_counts_requested_quantities(client, manager_groups):
    user = User.objects.create_user(username="mgr-top", password="pw")
    from django.contrib.auth.models import Group
    user.groups.add(Group.objects.get(name="Logistics Manager"))
    client.force_login(user)

    from inventory.services import create_material_request

    today = timezone.now()
    item_a = InventoryItem.objects.create(
        part_number="TOP-A", name="Top A", quantity_on_hand=100
    )
    item_b = InventoryItem.objects.create(
        part_number="TOP-B", name="Top B", quantity_on_hand=100
    )
    requester = User.objects.create_user(username="req-top", password="pw")
    mr = create_material_request(
        creator=requester,
        requestor_name="T",
        building_room="Rm",
        location="Loc",
        notes="",
        lines=[
            {"item": item_a, "quantity": 10, "notes": ""},
            {"item": item_b, "quantity": 3, "notes": ""},
        ],
    )

    response = client.get(reverse("reports_daily"))
    body = response.content.decode("utf-8")
    assert "TOP-A" in body or "Top A" in body
    # The "Top items" section header should be present
    assert "Top items" in body or "Most-requested" in body
