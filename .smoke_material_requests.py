import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

User = get_user_model()
user = User.objects.filter(is_active=True, is_superuser=True).first() or User.objects.filter(is_active=True).first()
if not user:
    raise SystemExit("No active user available for read-only smoke test")
client = Client()
client.force_login(user)
checks = [
    ("requests-board", "requests.rplwms.com", reverse("material_request_board"), {}),
    ("requests-partial", "requests.rplwms.com", reverse("material_request_board"), {"partial": "1"}),
    ("requests-archive", "requests.rplwms.com", reverse("material_request_archive"), {}),
    ("requests-inventory", "requests.rplwms.com", reverse("inventory_list"), {}),
    ("wms-board", "bbx.rplwms.com", reverse("material_request_board"), {}),
    ("wms-archive", "bbx.rplwms.com", reverse("material_request_archive"), {}),
]
for name, host, path, data in checks:
    response = client.get(path, data, HTTP_HOST=host, secure=True)
    print(f"{name}: {response.status_code} {len(response.content)}")
    if response.status_code != 200:
        raise SystemExit(1)
board = client.get(reverse("material_request_board"), HTTP_HOST="requests.rplwms.com", secure=True).content.decode()
for text in ("Open", "Picked", "Ready for Delivery", "Closed/Delivered", "Request archive", "material-request-board-poll"):
    if text not in board:
        raise SystemExit(f"Missing board marker: {text}")
if "Approval Queue" in board:
    raise SystemExit("Approval Queue unexpectedly visible")
print("authenticated production smoke test passed")
