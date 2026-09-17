MANAGER_GROUP_NAMES = frozenset({
    "Logistics Manager",
    "Sr. Logistics Manager",
    "Procurement Manager",
})


def user_is_manager(user):
    """Return whether a user holds an established manager role."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=MANAGER_GROUP_NAMES).exists()
