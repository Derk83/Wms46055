"""Let Logistics Specialists participate in active cycle counts.

The cycle count feature already had a perfectly-shaped permission split:
``perform_cycle_count`` (enter counts on an open count) and
``manage_cycle_counts`` (create / complete / cancel / reopen counts).
The view and list views were already gated on either of these via
``any_perm_required``, but the Logistics Specialist group only had
``perform_cycle_count`` indirectly through manual provisioning.

Derek wants Logistics Specialists to be able to see the active cycle
count and enter counts in the app (paper backup is up to them) — they
should still NOT be able to create / complete / cancel / reopen cycle
counts, since those are manager-level lifecycle operations. The same
migration also grants the perm to Procurement Specialist and Logistics
Manager idempotently so the three warehouse groups stay in sync if any
of them were provisioned without it.

Template and view guards for the lifecycle actions
(``complete`` / ``cancel`` / ``reopen``) are added in the same change set
so Logistics Specialists cannot accidentally lock a count by clicking
"Mark complete" or destroy it by clicking "Cancel".
"""
from django.db import migrations


CYCLE_COUNT_GROUPS = {
    "Procurement Specialist",
    "Logistics Manager",
    "Logistics Specialist",
}


def grant_perform_cycle_count(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    # ``perform_cycle_count`` was added in migration 0039 via ``Meta.permissions``
    # on the ``CycleCount`` model. Django attaches ``Meta.permissions`` from any
    # model to the InventoryItem content type (because that was the first
    # inventory content type at migration time), so we look it up there.
    perform = Permission.objects.filter(
        content_type__app_label="inventory",
        codename="perform_cycle_count",
    ).first()
    if perform is None:
        return
    for group in Group.objects.filter(name__in=CYCLE_COUNT_GROUPS).distinct():
        group.permissions.add(perform)


def remove_perform_cycle_count(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    perform = Permission.objects.filter(
        content_type__app_label="inventory",
        codename="perform_cycle_count",
    ).first()
    if perform is None:
        return
    for group in Group.objects.filter(name__in=CYCLE_COUNT_GROUPS).distinct():
        group.permissions.remove(perform)


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0043_assign_materialrequest_permission"),
    ]

    operations = [
        migrations.RunPython(grant_perform_cycle_count, remove_perform_cycle_count),
    ]
