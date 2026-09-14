import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.urls import reverse

from inventory.models import InventoryItem, PickTicket


@pytest.mark.django_db
def test_item_detail_records_one_recent_entry_per_user(client):
    RecentWork = apps.get_model("inventory", "RecentWork")
    user = get_user_model().objects.create_user("recent-user", password="unused")
    item = InventoryItem.objects.create(part_number="REC-001", name="Recent Widget")
    client.force_login(user)

    assert client.get(reverse("item_detail", args=[item.pk])).status_code == 200
    assert client.get(reverse("item_detail", args=[item.pk])).status_code == 200

    entries = RecentWork.objects.filter(user=user)
    assert entries.count() == 1
    entry = entries.get()
    assert entry.kind == "inventory_item"
    assert entry.object_id == item.pk
    assert entry.label == "REC-001 — Recent Widget"
    assert entry.url.endswith(reverse("item_detail", args=[item.pk]))


@pytest.mark.django_db
def test_recently_viewed_page_is_user_scoped(client):
    RecentWork = apps.get_model("inventory", "RecentWork")
    User = get_user_model()
    user = User.objects.create_user("recent-owner", password="unused")
    other = User.objects.create_user("recent-other", password="unused")
    own_item = InventoryItem.objects.create(part_number="OWN-1", name="Own item")
    other_item = InventoryItem.objects.create(part_number="OTHER-1", name="Other item")
    RecentWork.objects.create(user=user, kind="inventory_item", object_id=own_item.pk, label="OWN-1 — Own item", url=reverse("item_detail", args=[own_item.pk]))
    RecentWork.objects.create(user=other, kind="inventory_item", object_id=other_item.pk, label="OTHER-1 — Other item", url=reverse("item_detail", args=[other_item.pk]))
    client.force_login(user)

    response = client.get(reverse("recently_viewed"))

    assert response.status_code == 200
    assert "OWN-1 — Own item" in response.content.decode()
    assert "OTHER-1 — Other item" not in response.content.decode()


@pytest.mark.django_db
def test_recently_viewed_prunes_ticket_after_permission_is_revoked(client):
    RecentWork = apps.get_model("inventory", "RecentWork")
    user = get_user_model().objects.create_user("recent-revoked", password="unused")
    ticket = PickTicket.objects.create(
        picked_by_name="Picker",
        received_by_name="Receiver",
        requested_by_name="Requester",
        building_room="B1",
        location="Room 1",
        created_by=user,
    )
    RecentWork.objects.create(
        user=user,
        kind="pick_ticket",
        object_id=ticket.pk,
        label=f"Pick Ticket {ticket.ticket_number}",
        url=reverse("ticket_detail", args=[ticket.pk]),
    )
    client.force_login(user)

    response = client.get(reverse("recently_viewed"))

    assert response.status_code == 200
    assert ticket.ticket_number not in response.content.decode()
    assert not RecentWork.objects.filter(user=user).exists()


@pytest.mark.django_db
def test_dashboard_quick_actions_contains_resume_work_tile(client):
    user = get_user_model().objects.create_superuser("recent-admin", "recent@example.com", "unused")
    client.force_login(user)

    response = client.get(reverse("dashboard"))

    assert response.status_code == 200
    html = response.content.decode()
    assert f'href="{reverse("recently_viewed")}"' in html
    assert "Resume Work" in html
    assert html.count('class="quick-action-btn') == 7
