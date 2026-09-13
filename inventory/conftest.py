"""Pytest fixtures shared by the inventory test suite."""
from __future__ import annotations

import pytest


@pytest.fixture
def manager_groups(db):
    """Ensure all groups referenced by manager-gated views exist.

    Used by tests that exercise the report permission gates and the
    weekly auto-generation command's email recipients.
    """
    from django.contrib.auth.models import Group
    for name in (
        "Logistics Manager",
        "Sr. Logistics Manager",
        "Procurement Manager",
        "Logistics Specialist",
        "Procurement Specialist",
        "Material Requester",
    ):
        Group.objects.get_or_create(name=name)
