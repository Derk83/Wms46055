from django.core.management.base import BaseCommand
from django.utils import timezone

from inventory.models import MaterialRequest, PickTicket


class Command(BaseCommand):
    help = "Archive all unarchived closed/delivered material requests."

    def handle(self, *args, **options):
        archived_at = timezone.now()
        count = MaterialRequest.objects.filter(
            archived_at__isnull=True,
            pick_ticket__status=PickTicket.Status.CLOSED,
        ).update(archived_at=archived_at)
        self.stdout.write(self.style.SUCCESS(f"Archived {count} closed material request(s)."))
