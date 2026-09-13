import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.urls import reverse
from inventory.models import InventoryItem, PickTicket, PickTicketLine

line_count = int(os.environ.get("LINE_COUNT", "12"))
output = f"/tmp/pick-ticket-{line_count}-lines.pdf"
with transaction.atomic():
    user = get_user_model().objects.filter(is_superuser=True).first()
    if user is None:
        raise RuntimeError("No superuser available for print verification")
    ticket = PickTicket.objects.create(
        picked_by_name="Warehouse Picker",
        received_by_name="Delivery Receiver",
        requested_by_name="Project Contact",
        building_room="Building 100 / Room 210",
        location="North Loading Dock",
        notes="Verify count and condition at delivery.",
        created_by=user,
    )
    for index in range(line_count):
        item = InventoryItem.objects.create(
            part_number=f"PRINT-VERIFY-{index + 1:02d}",
            name=f"Professional verification line item {index + 1}",
            rack="A",
            section=f"{index + 1:02d}",
            bin_location="01",
            quantity_on_hand=100,
        )
        PickTicketLine.objects.create(ticket=ticket, item=item, quantity=index + 1)
    client = Client(HTTP_HOST="bbx.rplwms.com")
    client.force_login(user)
    response = client.get(reverse("ticket_print_pdf", args=[ticket.pk]), secure=True)
    if response.status_code != 200:
        raise RuntimeError(f"PDF endpoint returned {response.status_code}")
    with open(output, "wb") as handle:
        handle.write(response.content)
    transaction.set_rollback(True)
print(output)
