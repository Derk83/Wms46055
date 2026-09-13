import os, re, secrets
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPCookieProcessor
from http.cookiejar import CookieJar
from django.contrib.auth import get_user_model
from inventory.models import PickTicket

def authenticated_opener(host, username, password, next_path):
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    login_url = f"https://{host}/login/"
    body = opener.open(login_url, timeout=20).read().decode("utf-8", "replace")
    token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', body)
    if not token:
        raise RuntimeError(f"No CSRF token at {login_url}")
    data = urlencode({"username": username, "password": password, "csrfmiddlewaretoken": token.group(1), "next": next_path}).encode()
    opener.open(Request(login_url, data=data, headers={"Referer": login_url}), timeout=20).read()
    return opener

username = "deployment_verifier_" + secrets.token_hex(4)
password = secrets.token_urlsafe(24)
user = get_user_model().objects.create_superuser(username=username, email="", password=password)
try:
    request_opener = authenticated_opener("requests.rplwms.com", username, password, "/material-requests/new/")
    form = request_opener.open("https://requests.rplwms.com/material-requests/new/", timeout=20)
    form_body = form.read().decode("utf-8", "replace")
    if form.status != 200 or "Delivery date &amp; time" not in form_body or "datetime-local" not in form_body:
        raise SystemExit("FAIL live material request form")
    print("PASS 200 https://requests.rplwms.com/material-requests/new/")

    ticket = PickTicket.objects.order_by("-pk").first()
    wms_opener = authenticated_opener("bbx.rplwms.com", username, password, f"/tickets/{ticket.pk}/print/")
    printed = wms_opener.open(f"https://bbx.rplwms.com/tickets/{ticket.pk}/print/", timeout=20)
    print_body = printed.read().decode("utf-8", "replace")
    markers = ["data-client-generated-at", "SCHEDULED DELIVERY", "Intl.DateTimeFormat"]
    missing = [m for m in markers if m not in print_body]
    if printed.status != 200 or missing or "CHECKED BY" in print_body:
        raise SystemExit(f"FAIL live pick print missing={missing}")
    print(f"PASS 200 https://bbx.rplwms.com/tickets/{ticket.pk}/print/")
finally:
    user.delete()
