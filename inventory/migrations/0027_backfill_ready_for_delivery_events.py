from django.db import migrations


def backfill_ready_for_delivery_events(apps, schema_editor):
    MaterialRequest = apps.get_model("inventory", "MaterialRequest")
    MaterialRequestEvent = apps.get_model("inventory", "MaterialRequestEvent")

    ready_requests = MaterialRequest.objects.filter(pick_ticket__status="RECEIVED")
    for material_request in ready_requests.iterator():
        MaterialRequestEvent.objects.get_or_create(
            material_request_id=material_request.pk,
            event_type="status_changed",
            new_status="RECEIVED",
            defaults={"old_status": "PICKED"},
        )


def remove_backfilled_ready_for_delivery_events(apps, schema_editor):
    MaterialRequestEvent = apps.get_model("inventory", "MaterialRequestEvent")
    MaterialRequestEvent.objects.filter(
        event_type="status_changed",
        old_status="PICKED",
        new_status="RECEIVED",
        actor__isnull=True,
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0026_pushsubscription_audience"),
    ]

    operations = [
        migrations.RunPython(
            backfill_ready_for_delivery_events,
            remove_backfilled_ready_for_delivery_events,
        ),
    ]
