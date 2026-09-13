"""Split assignment rights from view rights for material requests.

Previously the ``material_request_assign`` view was gated on
``view_all_materialrequests`` — which means every warehouse role, including
Logistics Specialists, could assign a request to anyone. The Logistics
Specialist role only needs **visibility** across all requests (per
``0042_logistics_specialist_global_access``); they should not be able to
assign work.

This migration introduces a new permission
``inventory.assign_materialrequest`` and grants it to the warehouse groups
that already manage work distribution (Procurement Specialist and Logistics
Manager). Logistics Specialist is intentionally left out of this set so
they retain read-only visibility into every ticket without being able to
re-assign it.

The ``material_request_assign`` view, the queue template, and the
permission helper are updated in the same change set so the new permission
takes effect immediately on deploy.
"""
from django.db import migrations


ASSIGN_GROUPS = {
    "Procurement Specialist",
    "Logistics Manager",
}


def grant_assign_permission(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    content_type, _ = ContentType.objects.get_or_create(
        app_label="inventory", model="materialrequest"
    )
    assign_permission, _ = Permission.objects.get_or_create(
        content_type=content_type,
        codename="assign_materialrequest",
        defaults={"name": "Can assign material requests to warehouse staff"},
    )
    for group in Group.objects.filter(name__in=ASSIGN_GROUPS).distinct():
        group.permissions.add(assign_permission)


def remove_assign_permission(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    try:
        assign_permission = Permission.objects.get(
            content_type__app_label="inventory",
            content_type__model="materialrequest",
            codename="assign_materialrequest",
        )
    except Permission.DoesNotExist:
        return
    for group in Group.objects.filter(name__in=ASSIGN_GROUPS).distinct():
        group.permissions.remove(assign_permission)


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0042_logistics_specialist_global_access"),
    ]

    operations = [
        migrations.RunPython(grant_assign_permission, remove_assign_permission),
    ]
