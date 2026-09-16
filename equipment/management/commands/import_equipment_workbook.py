from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from equipment.importer import import_workbook


class Command(BaseCommand):
    help = "Import the staged RPL equipment workbook without silently dropping source rows."

    def add_arguments(self, parser):
        parser.add_argument("workbook", type=Path)
        parser.add_argument("--username", required=True, help="Existing user recorded as the import actor")

    def handle(self, *args, **options):
        path = options["workbook"].expanduser().resolve()
        if not path.is_file():
            raise CommandError(f"Workbook not found: {path}")
        try:
            actor = get_user_model().objects.get(username=options["username"])
        except get_user_model().DoesNotExist as error:
            raise CommandError("Import actor does not exist.") from error
        batch, created = import_workbook(path.read_bytes(), source_name=path.name, actor=actor)
        if not created:
            self.stdout.write(self.style.WARNING(f"Already imported: {batch.pk}"))
            return
        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {batch.summary.get('assets_created', 0)} assets; "
                f"{batch.review_rows} source rows require review; batch {batch.pk}."
            )
        )
