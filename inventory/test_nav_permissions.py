"""Navigation permission contracts: visible links must lead somewhere accessible."""
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse


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
