"""Navigation permission contracts: visible links must lead somewhere accessible."""
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse

from .models import InventoryItem, PickTicket


User = get_user_model()


def _perm(codename):
    return Permission.objects.get(content_type__app_label="inventory", codename=codename)


@pytest.mark.django_db
def test_receiving_link_targets_log_for_view_log_only_user(client):
    user = User.objects.create_user(username="nav-log-only", password="pw")
    user.user_permissions.add(_perm("view_receiving_log"))
    client.force_login(user)

    response = client.get(reverse("inventory_list"), HTTP_HOST="bbx.rplwms.com")
    body = response.content.decode()

    assert response.status_code == 200
    assert 'class="site-nav site-nav--sectioned"' in body
    assert f'href="{reverse("receiving_log")}"' in body
    assert f'href="{reverse("receiving")}"' not in body


@pytest.mark.django_db
def test_request_portal_hides_links_when_endpoint_permissions_are_incomplete(client):
    user = User.objects.create_user(username="nav-partial-requester", password="pw")
    user.user_permissions.add(
        _perm("access_material_request_portal"),
        _perm("view_inventoryitem"),
        _perm("add_materialrequest"),
    )
    client.force_login(user)

    response = client.get(reverse("inventory_list"), HTTP_HOST="requests.rplwms.com")
    body = response.content.decode()

    assert response.status_code == 200
    assert "site-nav--sectioned" not in body
    assert f'href="{reverse("material_request_board")}"' not in body
    assert f'href="{reverse("material_request_create")}"' not in body


@pytest.mark.django_db
def test_request_portal_shows_links_when_endpoint_permissions_are_complete(client):
    user = User.objects.create_user(username="nav-full-requester", password="pw")
    user.user_permissions.add(
        _perm("access_material_request_portal"),
        _perm("view_inventoryitem"),
        _perm("view_materialrequest"),
        _perm("add_materialrequest"),
        _perm("add_materialrequestline"),
    )
    client.force_login(user)

    response = client.get(reverse("inventory_list"), HTTP_HOST="requests.rplwms.com")
    body = response.content.decode()

    assert response.status_code == 200
    assert f'href="{reverse("material_request_board")}"' in body
    assert f'href="{reverse("material_request_create")}"' in body


def test_option_a_mobile_navigation_css_targets_real_nested_links():
    css = (Path(__file__).parent / "static" / "inventory" / "css" / "app.css").read_text()

    assert ".site-nav .nav-section>a" in css
    assert ".site-nav--sectioned{gap:0}" in css
    assert ".nav-section:first-of-type" not in css
    assert ".nav-section:last-of-type" not in css
    assert ".nav-section:nth-child(2)" in css
    assert ".nav-section:nth-last-child(2)" in css
    assert "min-height:50px" in css
    assert "border-bottom:1px solid var(--border)" in css
    assert ".site-nav .nav-section>a:hover" in css


@pytest.mark.django_db
def test_approved_desktop_header_has_priority_navigation_and_grouped_more_menu(client):
    user = User.objects.create_superuser(
        username="desktop-header", email="header@example.com", password="pw"
    )
    client.force_login(user)

    response = client.get(reverse("dashboard"), HTTP_HOST="bbx.rplwms.com")
    body = response.content.decode()

    assert response.status_code == 200
    assert 'class="desktop-header-top"' in body
    assert 'class="desktop-global-search"' in body
    assert f'action="{reverse("global_search")}"' in body
    assert 'class="desktop-priority-nav"' in body
    assert 'data-header-more-toggle' in body
    assert 'data-header-more-menu' in body
    assert 'data-account-toggle' in body
    assert 'data-account-menu' in body
    for label in ("Inventory", "Tickets", "Requests", "Receiving"):
        assert f">{label}<" in body
    assert ">More <" in body
    for label in ("Insights", "Tools", "Admin"):
        assert f">{label}<" in body


def test_approved_desktop_header_css_preserves_mobile_drawer_breakpoint():
    css = (Path(__file__).parent / "static" / "inventory" / "css" / "app.css").read_text()

    assert ".desktop-header-top" in css
    assert ".desktop-priority-nav" in css
    assert ".desktop-global-search" in css
    assert ".header-more-menu" in css
    assert ".account-menu-panel" in css
    assert "@media(max-width:1120px)" in css
    assert ".desktop-header-top{display:contents}" in css
    assert ".desktop-header-top>.brand,.desktop-global-search,.desktop-header-top .account-menu{display:none}" in css
    assert ".desktop-priority-nav{display:none}" in css


def test_approved_desktop_header_script_controls_more_and_account_menus():
    script = (
        Path(__file__).parent / "static" / "inventory" / "js" / "app.js"
    ).read_text()

    assert "data-header-more-toggle" in script
    assert "data-header-more-menu" in script
    assert "data-account-toggle" in script
    assert "data-account-menu" in script


def test_approved_phone_header_keeps_only_notification_beside_last_hamburger():
    template = (
        Path(__file__).parent / "templates" / "inventory" / "base.html"
    ).read_text()
    css = (Path(__file__).parent / "static" / "inventory" / "css" / "app.css").read_text()
    script = (Path(__file__).parent / "static" / "inventory" / "js" / "app.js").read_text()

    assert 'class="mobile-nav-utilities"' in template
    assert "data-mobile-theme-toggle" in template
    assert "mobile-nav-install" in template
    assert ".site-header--wms .header-controls{order:2;margin-left:auto}" in css
    assert ".site-header--wms .nav-toggle{order:3;margin-left:0}" in css
    assert ".desktop-header-top #theme-toggle{display:none!important}" in css
    assert ".mobile-nav-utilities{display:grid;" in css
    assert "querySelectorAll('[data-theme-toggle]')" in script


@pytest.mark.django_db
def test_global_search_returns_inventory_matches(client):
    user = User.objects.create_user(username="warehouse-search", password="pw")
    item = InventoryItem.objects.create(
        part_number="SEARCH-100", name="Searchable safety vest", quantity_on_hand=12
    )
    client.force_login(user)

    response = client.get(
        reverse("global_search"), {"q": "SEARCH-100"}, HTTP_HOST="bbx.rplwms.com"
    )

    assert response.status_code == 200
    assert item in response.context["inventory_results"]
    assert "Searchable safety vest" in response.content.decode()


@pytest.mark.django_db
def test_global_search_does_not_expose_ticket_results_without_permission(client):
    user = User.objects.create_user(username="inventory-search-only", password="pw")
    PickTicket.objects.create(
        requested_by_name="Restricted Person",
        picked_by_name="",
        received_by_name="",
        building_room="Restricted Room",
        location="Restricted Location",
        created_by=user,
    )
    client.force_login(user)

    response = client.get(
        reverse("global_search"), {"q": "Restricted"}, HTTP_HOST="bbx.rplwms.com"
    )

    assert response.status_code == 200
    assert list(response.context["ticket_results"]) == []
    assert "Restricted Person" not in response.content.decode()


@pytest.mark.django_db
def test_request_portal_keeps_notification_center(client):
    user = User.objects.create_superuser(
        username="portal-header", email="portal@example.com", password="pw"
    )
    client.force_login(user)

    response = client.get(reverse("dashboard"), HTTP_HOST="requests.rplwms.com")
    body = response.content.decode()

    assert response.status_code == 200
    assert "data-notification-center" in body
    assert "data-notification-toggle" in body
    assert "data-notification-panel" in body
