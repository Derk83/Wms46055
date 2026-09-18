from django.db import migrations


MANAGER_GROUPS = ("Logistics Manager", "Sr. Logistics Manager", "Procurement Manager")


def clean_equipment_request_permissions(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    equipment_permissions = Permission.objects.filter(content_type__app_label="equipment")
    manage_request = equipment_permissions.filter(codename="manage_equipment_requests").first()
    if manage_request:
        for group in Group.objects.filter(name__in=MANAGER_GROUPS):
            group.permissions.remove(manage_request)

    requester = Group.objects.filter(name="Equipment Requester").first()
    if requester:
        allowed = equipment_permissions.filter(codename="access_equipment_requests")
        requester.permissions.remove(*equipment_permissions)
        requester.permissions.add(*allowed)


def restore_legacy_manager_permission(apps, schema_editor):
    """Restore the only deterministic grant removed from legacy manager roles."""
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    permission = Permission.objects.filter(
        content_type__app_label="equipment", codename="manage_equipment_requests"
    ).first()
    if permission:
        for group in Group.objects.filter(name__in=MANAGER_GROUPS):
            group.permissions.add(permission)


class Migration(migrations.Migration):
    dependencies = [("equipment", "0005_maintenanceworkorder_meter_at_completion_and_more")]
    operations = [migrations.RunPython(clean_equipment_request_permissions, restore_legacy_manager_permission)]
