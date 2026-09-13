"""Isolated settings used by the local responsive visual-audit harness."""
import os
from .settings import *  # noqa: F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("BBX_VISUAL_DB", "/tmp/bbx-visual-audit.sqlite3"),
    }
}
DEBUG = True
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "bbx.rplwms.com", "requests.rplwms.com"]
CSRF_TRUSTED_ORIGINS = [
    "http://bbx.rplwms.com:8099",
    "http://requests.rplwms.com:8099",
]
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
