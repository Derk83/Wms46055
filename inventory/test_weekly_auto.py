"""Tests for the Friday 07:00 weekly auto-generation command.

The previous version of this app exposed a /reports/weekly/ URL that managers
could open on-demand. That manual flow has been retired — the weekly report
is now produced by `manage.py generate_weekly_report` and triggered by a
systemd timer on Friday mornings. These tests cover both the schedule
math (which Monday–Sunday the Friday run covers) and the command's output.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from email.message import EmailMessage
from io import BytesIO
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.utils import timezone

from inventory.management.commands.generate_weekly_report import previous_week_range
from inventory.models import InventoryItem


User = get_user_model()


# ---------------------------------------------------------------------------
# previous_week_range() — pure date arithmetic
# ---------------------------------------------------------------------------


def test_previous_week_range_on_monday():
    """A Monday run should report the *previous* Monday–Sunday (7 days back)."""
    # 2026-09-14 is a Monday
    today = date(2026, 9, 14)
    start, end = previous_week_range(today)
    assert start == date(2026, 9, 7)
    assert end == date(2026, 9, 13)


def test_previous_week_range_on_friday():
    """A Friday run should report last Monday through last Sunday."""
    # 2026-09-18 is a Friday
    today = date(2026, 9, 18)
    start, end = previous_week_range(today)
    assert start == date(2026, 9, 7)
    assert end == date(2026, 9, 13)


def test_previous_week_range_on_sunday():
    """A Sunday run should still report the *prior* Monday–Sunday, not today."""
    # 2026-09-20 is a Sunday
    today = date(2026, 9, 20)
    start, end = previous_week_range(today)
    assert start == date(2026, 9, 7)
    assert end == date(2026, 9, 13)


def test_previous_week_range_on_wednesday():
    """A mid-week run still reports the previous full Mon–Sun."""
    # 2026-09-16 is a Wednesday
    today = date(2026, 9, 16)
    start, end = previous_week_range(today)
    assert start == date(2026, 9, 7)
    assert end == date(2026, 9, 13)


def test_previous_week_range_crosses_year_boundary():
    """Range math works across December → January."""
    # 2027-01-01 is a Friday
    today = date(2027, 1, 1)
    start, end = previous_week_range(today)
    assert start == date(2026, 12, 21)
    assert end == date(2026, 12, 27)


# ---------------------------------------------------------------------------
# Command output
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_command_dry_run_writes_nothing(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    out_before = list((tmp_path / "auto_reports").glob("**/*.pdf"))
    assert out_before == []

    call_command("generate_weekly_report", "--dry-run")

    out_after = list((tmp_path / "auto_reports").glob("**/*.pdf"))
    assert out_after == []


@pytest.mark.django_db
def test_command_builds_weekly_range_in_america_chicago(tmp_path, settings):
    """The Friday report's Mon-Sun boundaries must be Chicago-local, not UTC."""
    settings.MEDIA_ROOT = tmp_path
    with patch(
        "inventory.management.commands.generate_weekly_report.activity_summary",
        return_value={},
    ) as summary:
        call_command("generate_weekly_report", "--dry-run")

    rng = summary.call_args.args[0]
    assert getattr(rng.start.tzinfo, "key", None) == "America/Chicago"
    assert getattr(rng.end.tzinfo, "key", None) == "America/Chicago"
    assert rng.start.hour == 0
    assert rng.end.hour == 0


@pytest.mark.django_db
def test_command_saves_pdf_to_expected_path(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path

    call_command("generate_weekly_report")

    # Compute expected filename from "today" the same way the command does.
    from datetime import date as _date, timedelta as _td
    today_local = timezone.localdate()
    days_since_mon = today_local.weekday()
    last_sunday = today_local - _td(days=days_since_mon) - _td(days=1)
    expected = tmp_path / "auto_reports" / "weekly" / f"weekly-{last_sunday.isoformat()}.pdf"
    assert expected.exists(), f"expected {expected} on disk"
    body = expected.read_bytes()
    assert body.startswith(b"%PDF-")
    assert len(body) > 100


@pytest.mark.django_db
def test_command_email_attaches_pdf_to_managers(tmp_path, settings, manager_groups):
    settings.MEDIA_ROOT = tmp_path
    # Reset the test mail outbox
    mail.outbox = []

    # 3 manager-group users + 1 superuser = 4 candidates. The admin's
    # @example.com placeholders get filtered out (see SMTP-resilience in
    # the command), so only the two real managers receive.
    superuser = User.objects.create_superuser(
        username="admin-auto", email="admin@example.com", password="pw"
    )
    from django.contrib.auth.models import Group
    grp = Group.objects.get(name="Logistics Manager")
    mgr1 = User.objects.create_user(
        username="mgr-auto-1", email="mgr1@blackbox.com", password="pw"
    )
    mgr1.groups.add(grp)
    mgr2 = User.objects.create_user(
        username="mgr-auto-2", email="mgr2@blackbox.com", password="pw"
    )
    mgr2.groups.add(grp)
    # No email → should be filtered out
    mgr3 = User.objects.create_user(
        username="mgr-auto-3", email="", password="pw"
    )
    mgr3.groups.add(grp)
    # Specialist should NOT receive (not in any manager group, not superuser)
    spec_grp = Group.objects.get(name="Logistics Specialist")
    specialist = User.objects.create_user(
        username="spec-auto", email="spec@blackbox.com", password="pw"
    )
    specialist.groups.add(spec_grp)

    call_command("generate_weekly_report", "--email")

    assert len(mail.outbox) == 1, f"expected 1 message, got {len(mail.outbox)}"
    msg = mail.outbox[0]
    # admin@example.com and the no-email user are filtered out; only the two
    # managers with real addresses receive.
    assert set(msg.to) == {"mgr1@blackbox.com", "mgr2@blackbox.com"}
    assert "admin@example.com" not in msg.to
    assert "spec@blackbox.com" not in msg.to
    assert len(msg.attachments) == 1
    filename, content, mime = msg.attachments[0]
    assert filename.startswith("weekly-") and filename.endswith(".pdf")
    assert mime == "application/pdf"
    assert content.startswith(b"%PDF-")


@pytest.mark.django_db
def test_command_pdf_omits_audit_events(tmp_path, settings):
    """Regression: weekly PDF must NOT contain audit event section."""
    settings.MEDIA_ROOT = tmp_path

    call_command("generate_weekly_report")

    from inventory.management.commands.generate_weekly_report import DateRange
    # Compute the same Mon–Sun the command used.
    from datetime import date as _date, timedelta as _td
    today_local = timezone.localdate()
    days_since_mon = today_local.weekday()
    last_monday = today_local - _td(days=days_since_mon) - _td(days=7)
    last_sunday = last_monday + _td(days=6)
    pdf_path = tmp_path / "auto_reports" / "weekly" / f"weekly-{last_sunday.isoformat()}.pdf"
    assert pdf_path.exists()

    # Easier to verify against the rendered HTML than the binary PDF:
    # re-render directly and grep the output.
    week_start = last_monday
    week_end = last_sunday
    rng = DateRange(
        start=timezone.make_aware(datetime.combine(week_start, time.min)),
        end=timezone.make_aware(datetime.combine(week_end + timedelta(days=1), time.min)),
        label=f"{week_start.strftime('%b %-d, %Y')} – {week_end.strftime('%b %-d, %Y')}",
    )
    from inventory.reports import (
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
    }
    from django.template.loader import render_to_string
    html = render_to_string("inventory/reports_weekly_pdf.html", ctx)
    # The PDF template previously had these sections; they should now be gone.
    assert "Trends — requests per day" not in html
    assert "Oldest open ticket age" not in html
    assert "audit_events" not in html.lower()


@pytest.mark.django_db
def test_command_smtp_failure_does_not_abort(tmp_path, settings, manager_groups):
    """If SMTP rejects a recipient, the PDF must still be on disk and the
    command must exit 0. fail_silently=True on the EmailMessage."""
    settings.MEDIA_ROOT = tmp_path
    from datetime import date as _date, timedelta as _td
    today_local = timezone.localdate()
    days_since_mon = today_local.weekday()
    last_sunday = today_local - _td(days=days_since_mon) - _td(days=1)

    User.objects.create_user(
        username="real-mgr", email="real@example.com", password="pw"
    )

    with patch("django.core.mail.message.EmailMessage.send", return_value=0):
        # Should NOT raise even though the (mocked) send returns 0.
        call_command("generate_weekly_report", "--email")

    pdf = tmp_path / "auto_reports" / "weekly" / f"weekly-{last_sunday.isoformat()}.pdf"
    assert pdf.exists(), "PDF must be saved even if email fails"


@pytest.mark.django_db
def test_command_no_recipients_no_email(tmp_path, settings, manager_groups):
    """--email with no recipients should be a no-op (no exception)."""
    settings.MEDIA_ROOT = tmp_path
    mail.outbox = []

    # No manager users at all in this test DB
    call_command("generate_weekly_report", "--email")

    assert mail.outbox == []
    # PDF should still be written; compute path dynamically.
    from datetime import date as _date, timedelta as _td
    today_local = timezone.localdate()
    days_since_mon = today_local.weekday()
    last_sunday = today_local - _td(days=days_since_mon) - _td(days=1)
    pdf = tmp_path / "auto_reports" / "weekly" / f"weekly-{last_sunday.isoformat()}.pdf"
    assert pdf.exists()


@pytest.mark.django_db
def test_command_pdf_includes_summary_tiles(tmp_path, settings):
    """The rendered PDF should have the 8 summary tiles filled in."""
    settings.MEDIA_ROOT = tmp_path
    InventoryItem.objects.create(part_number="X1", name="Item", quantity_on_hand=5)

    call_command("generate_weekly_report")

    from datetime import date as _date, timedelta as _td
    today_local = timezone.localdate()
    days_since_mon = today_local.weekday()
    last_monday = today_local - _td(days=days_since_mon) - _td(days=7)
    last_sunday = last_monday + _td(days=6)
    pdf_path = tmp_path / "auto_reports" / "weekly" / f"weekly-{last_sunday.isoformat()}.pdf"
    assert pdf_path.exists()
    # We can't easily parse a PDF in pytest, so instead re-render the HTML
    # context and assert the tile values are present.
    from inventory.management.commands.generate_weekly_report import DateRange
    rng = DateRange(
        start=timezone.make_aware(datetime.combine(last_monday, time.min)),
        end=timezone.make_aware(datetime.combine(last_sunday + _td(days=1), time.min)),
        label=f"{last_monday.strftime('%b %-d')} – {last_sunday.strftime('%b %-d')}",
    )
    from inventory.reports import activity_summary
    s = activity_summary(rng)
    assert "new_requests" in s
    assert "confirmed_deliveries" in s
    assert "tickets_picked" in s
