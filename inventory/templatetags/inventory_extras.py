from django import template

register = template.Library()


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