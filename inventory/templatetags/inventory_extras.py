import hashlib
from functools import lru_cache

from django import template
from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


@lru_cache(maxsize=64)
def _static_content_digest(path):
    """Return a short digest for a source static asset, cached per process."""
    source_path = finders.find(path)
    if not source_path:
        return ""
    digest = hashlib.sha256()
    with open(source_path, "rb") as source:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:12]


@register.simple_tag
def versioned_static(path):
    """Build a static URL whose query key changes whenever its content changes."""
    url = static(path)
    digest = _static_content_digest(path)
    return f"{url}?v={digest}" if digest else url


@register.simple_tag
def demo_url():
    """Return the configured canonical demo launcher URL."""
    return settings.DEMO_URL


_MANAGER_REPORT_GROUPS = frozenset({
    "Logistics Manager",
    "Sr. Logistics Manager",
    "Procurement Manager",
})


@register.filter
def get_field(form, field_name):
    """Get a form field by dynamic name."""
    try:
        return form[field_name]
    except KeyError:
        return ""


@register.filter
def call_status(obj, fn):
    """Call a status-derivation function on ``obj`` and return the result."""
    if not fn:
        return ""
    try:
        return fn(obj)
    except Exception:
        return ""


@register.filter
def has_manager_group(user):
    """True if user is superuser or in any of the manager-report groups."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=_MANAGER_REPORT_GROUPS).exists()