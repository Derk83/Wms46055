"""Grant warehouse-wide material-request visibility to the Logistics Specialist group.

Mirrors inventory/migrations/0035_material_request_global_access.py but adds
"Logistics Specialist" to the set of groups that receive
``view_all_materialrequests`` so logistics specialists can see every material
request and pick ticket regardless of who created or is assigned to them.
"""

from django.db import migrations


WAREHOUSE_GROUPS = {
    "Procurement Specialist",
    "Logistics Manager",
    "Logistics Specialist",
}
WAREHOUSE_CAPABILITIES = {
    "add_inventoryitem",
    "change_pickticket",
    "receive_stock",
    "manage_users",
}


def grant_warehouse_roles(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    content_type, _ = ContentType.objects.get_or_create(
        app_label="inventory", model="materialrequest"
    )
    global_permission, _ = Permission.objects.get_or_create(
        content_type=content_type,
        codename="view_all_materialrequests",
        defaults={"name": "Can view and manage all material requests"},
    )
    warehouse_groups = Group.objects.filter(
        name__in=WAREHOUSE_GROUPS,
        permissions__content_type__app_label="inventory",
        permissions__codename="view_materialrequest",
    ).filter(
        permissions__content_type__app_label="inventory",
        permissions__codename__in=WAREHOUSE_CAPABILITIES,
    ).distinct()
    for group in warehouse_groups:
        group.permissions.add(global_permission)


def remove_warehouse_global_permission(apps, schema_editor):
    """Reverse: drop ``view_all_materialrequests`` from groups in the warehouse set."""
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    try:
        global_permission = Permission.objects.get(
            content_type__app_label="inventory",
            content_type__model="materialrequest",
            codename="view_all_materialrequests",
        )
    except Permission.DoesNotExist:
        return
    for group in Group.objects.filter(name__in=WAREHOUSE_GROUPS).distinct():
        group.permissions.remove(global_permission)


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0041_alter_inventoryitem_options_cyclecount_archived_at_and_more"),
    ]

    operations = [
        migrations.RunPython(grant_warehouse_roles, remove_warehouse_global_permission),
    ]
