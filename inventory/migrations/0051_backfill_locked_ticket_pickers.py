from django.db import migrations
from django.db.models import F


def backfill_locked_ticket_pickers(apps, schema_editor):
    PickTicket = apps.get_model("inventory", "PickTicket")

    for ticket in PickTicket.objects.filter(
        status="OPEN",
        assigned_to_id__isnull=False,
        picked_by_user_id__isnull=True,
    ).select_related("assigned_to"):
        user = ticket.assigned_to
        display_name = f"{user.first_name} {user.last_name}".strip() or user.username
        PickTicket.objects.filter(pk=ticket.pk, picked_by_user_id__isnull=True).update(
            picked_by_user_id=ticket.assigned_to_id,
            picked_by_name=display_name,
        )


def reverse_backfill_locked_ticket_pickers(apps, schema_editor):
    PickTicket = apps.get_model("inventory", "PickTicket")
    PickTicket.objects.filter(
        status="OPEN",
        assigned_to_id__isnull=False,
        picked_by_user_id=F("assigned_to_id"),
    ).update(picked_by_user_id=None, picked_by_name="")


class Migration(migrations.Migration):
    dependencies = [("inventory", "0050_pick_quantity_signoff_acknowledgement")]

    operations = [
        migrations.RunPython(
            backfill_locked_ticket_pickers,
            reverse_backfill_locked_ticket_pickers,
        )
    ]
