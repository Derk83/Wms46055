from django import template

register = template.Library()


@register.filter
def get_field(form, field_name):
    """Get a form field by dynamic name."""
    try:
        return form[field_name]
    except KeyError:
        return ""