from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_date

from equipment.services import generate_due_maintenance


class Command(BaseCommand):
    help = "Generate idempotent work orders for recurring vehicle maintenance entering a lead window."

    def add_arguments(self, parser):
        parser.add_argument("--as-of", type=str, help="Forecast date in YYYY-MM-DD format (defaults to today).")

    def handle(self, *args, **options):
        as_of = None
        if options["as_of"]:
            as_of = parse_date(options["as_of"])
            if as_of is None:
                raise CommandError("--as-of must be YYYY-MM-DD")
        generated = generate_due_maintenance(as_of=as_of)
        for order in generated:
            self.stdout.write(f"{order.work_order_number} {order.asset.asset_tag} {order.title}")
        self.stdout.write(self.style.SUCCESS(f"Generated {len(generated)} due maintenance work order(s)."))
