from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from inventory.models import PushDelivery, PushSubscription
from inventory.push import deliver_push_deliveries


class Command(BaseCommand):
    help = "Deliver due Web Push outbox rows and remove obsolete browser endpoints."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        limit = max(1, min(options["limit"], 1000))
        now = timezone.now()
        due_ids = list(
            PushDelivery.objects.filter(
                status__in=(PushDelivery.Status.PENDING, PushDelivery.Status.RETRY, PushDelivery.Status.PROCESSING),
                next_attempt_at__lte=now,
                attempts__lt=5,
            ).order_by("next_attempt_at", "pk").values_list("pk", flat=True)[:limit]
        )
        results = deliver_push_deliveries(due_ids) if due_ids else {"sent": 0, "failed": 0, "expired": 0}

        disabled_cutoff = now - timedelta(days=30)
        stale_subscriptions, _ = PushSubscription.objects.filter(
            enabled=False, updated_at__lt=disabled_cutoff
        ).delete()
        delivery_cutoff = now - timedelta(days=90)
        old_deliveries, _ = PushDelivery.objects.filter(
            status__in=(PushDelivery.Status.SENT, PushDelivery.Status.FAILED, PushDelivery.Status.CANCELLED, PushDelivery.Status.EXPIRED),
            created_at__lt=delivery_cutoff,
        ).delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"push: due={len(due_ids)} sent={results['sent']} failed={results['failed']} "
                f"expired={results['expired']} stale_subscriptions={stale_subscriptions} "
                f"old_deliveries={old_deliveries}"
            )
        )
