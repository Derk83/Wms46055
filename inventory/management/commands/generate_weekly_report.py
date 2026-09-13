"""Auto-generate the Weekly Activity Report PDF on Friday mornings.

Designed to be invoked by systemd on Friday 07:00 America/Chicago. It covers
the *previous* Monday–Sunday range (a finished week), saves the rendered PDF
under ``media/auto_reports/weekly/``, and optionally emails it to all users
who are allowed to view reports.

Usage
-----
    python manage.py generate_weekly_report           # save only
    python manage.py generate_weekly_report --email   # also email managers
    python manage.py generate_weekly_report --dry-run # print path, write nothing

The "previous Monday" anchor is computed in America/Chicago, so the schedule
stays correct through DST shifts (CDT ↔ CST).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMessage
from django.core.management.base import BaseCommand, CommandError
from django.template.loader import render_to_string
from django.utils import timezone
from weasyprint import HTML

from inventory.reports import (
    DateRange,
    activity_summary,
    delivery_confirmations,
    delivery_denials,
    new_material_requests,
    out_of_stock_items,
    pick_tickets_completed,
    pick_tickets_opened,
    receiving_by_item,
    top_items,
    top_requesters,
    material_request_status,
)


User = get_user_model()


def previous_week_range(today: date) -> tuple[date, date]:
    """Return (Monday, Sunday) of the week *before* today.

    Friday morning at 07:00 should report on the week that just ended
    (last Mon–Sun), not the partial Mon–Thu of the current week.
    """
    # Monday = 0 ... Sunday = 6 (Python's weekday())
    days_since_mon = today.weekday()
    this_monday = today - timedelta(days=days_since_mon)
    last_monday = this_monday - timedelta(days=7)
    last_sunday = last_monday + timedelta(days=6)
    return last_monday, last_sunday


def _manager_recipients() -> list[str]:
    """All users allowed to view reports (superusers + manager groups)."""
    manager_groups = (
        "Logistics Manager",
        "Sr. Logistics Manager",
        "Procurement Manager",
    )
    qs = User.objects.filter(is_active=True).filter(
        is_superuser=True
    ) | User.objects.filter(
        is_active=True,
        groups__name__in=manager_groups,
    )
    return list(qs.distinct().values_list("email", flat=True))


class Command(BaseCommand):
    help = "Render the weekly activity PDF for the previous Mon–Sun and (optionally) email it to managers."

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            action="store_true",
            help="Email the rendered PDF to all users who can view reports.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print intended output path and recipient list, write nothing.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        send_email = options["email"]

        today_local = timezone.localtime().date()
        week_start, week_end = previous_week_range(today_local)

        rng_start = timezone.make_aware(datetime.combine(week_start, time.min))
        rng_end = timezone.make_aware(datetime.combine(week_end + timedelta(days=1), time.min))
        rng = DateRange(
            start=rng_start,
            end=rng_end,
            label=f"{week_start.strftime('%b %-d, %Y')} – {week_end.strftime('%b %-d, %Y')}",
        )

        # Build the same context the manual PDF view uses, minus the weekly-only
        # sections (audit events, trends, aging, cycle counts).
        logo_path = settings.BASE_DIR / "inventory" / "static" / "inventory" / "img" / "blackbox-logo.png"
        ctx = {
            "kind": "weekly",
            "range": rng,
            "summary": activity_summary(rng),
            "new_requests": new_material_requests(rng),
            "confirmed": delivery_confirmations(rng),
            "denied": delivery_denials(rng),
            "tickets_opened": pick_tickets_opened(rng),
            "tickets_picked": pick_tickets_completed(rng),
            "receiving_by_item": receiving_by_item(rng),
            "out_of_stock": out_of_stock_items(),
            "top_requesters": top_requesters(rng),
            "top_items": top_items(rng),
            "status_of": material_request_status,
            "now": timezone.now(),
            "logo_uri": logo_path.as_uri(),
        }

        html = render_to_string(
            "inventory/reports_weekly_pdf.html",
            ctx,
            request=None,
        )

        # Output path: media/auto_reports/weekly/weekly-YYYY-MM-DD.pdf
        out_dir = Path(settings.MEDIA_ROOT) / "auto_reports" / "weekly"
        filename = f"weekly-{week_end.isoformat()}.pdf"
        out_path = out_dir / filename

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"[dry-run] would write {out_path}"
            ))
            if send_email:
                recipients = _manager_recipients()
                self.stdout.write(self.style.WARNING(
                    f"[dry-run] would email {len(recipients)} recipient(s): {', '.join(r for r in recipients if r)}"
                ))
            return

        out_dir.mkdir(parents=True, exist_ok=True)
        pdf_bytes = HTML(
            string=html,
            base_url=str(settings.BASE_DIR),
        ).write_pdf()
        out_path.write_bytes(pdf_bytes)
        self.stdout.write(self.style.SUCCESS(
            f"Wrote {out_path} ({len(pdf_bytes):,} bytes)"
        ))

        if send_email:
            # Filter out empty + obvious placeholder addresses. A malformed
            # SMTP-rejected recipient (e.g. admin@example.com) must not abort
            # the whole job — the PDF is already saved to disk by this point
            # and we don't want a single bad address to leave Friday morning
            # with no report.
            raw_recipients = [r for r in _manager_recipients() if r]
            recipients = [
                r for r in raw_recipients
                if not r.endswith("@example.com")
                and not r.endswith("@example.org")
                and not r.endswith("@example.net")
                and "@" in r
            ]
            skipped = sorted(set(raw_recipients) - set(recipients))
            if skipped:
                self.stdout.write(self.style.WARNING(
                    f"Skipping placeholder/invalid recipients: {', '.join(skipped)}"
                ))
            if not recipients:
                self.stdout.write(self.style.WARNING(
                    "No real recipients with email addresses found; skipping email."
                ))
                return
            subject = f"RPL Warehouse Weekly Report — {rng.label}"
            body = (
                f"Attached: WMS Weekly Activity Report for {rng.label}.\n\n"
                f"Generated {timezone.now():%Y-%m-%d %H:%M} America/Chicago.\n"
                f"Saved at: {out_path}\n"
            )
            msg = EmailMessage(
                subject=subject,
                body=body,
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                to=recipients,
            )
            msg.attach(filename, pdf_bytes, "application/pdf")
            # fail_silently=True so a transient SMTP rejection on one address
            # doesn't abort the whole job. The PDF is already saved.
            sent = msg.send(fail_silently=True)
            self.stdout.write(self.style.SUCCESS(
                f"Emailed {sent} message(s) to {len(recipients)} recipient(s): "
                f"{', '.join(recipients)}"
            ))
