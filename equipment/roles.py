from django.contrib.auth.models import Group, Permission
from django.db.models.signals import post_migrate
from django.dispatch import receiver


ROLE_PERMISSIONS = {
    "Equipment Requester": {"access_equipment_requests"},
    "Equipment Manager": "*",
    "Equipment Coordinator": {
        "access_equipment_portal", "view_asset", "add_asset", "change_asset", "manage_equipment",
        "checkout_asset", "return_asset", "view_checkout", "view_checkoutitem", "view_returnrecord",
        "add_reservation", "view_reservation", "change_reservation", "manage_reservations",
        "add_maintenanceworkorder", "view_maintenanceworkorder", "change_maintenanceworkorder",
        "manage_maintenance", "view_rentalcontract", "view_rentalasset", "print_asset_labels",
    },
    "Equipment User": {"access_equipment_portal", "view_asset", "add_reservation", "view_reservation"},
    "Equipment Viewer": {
        "access_equipment_portal", "view_asset", "view_checkout", "view_reservation",
        "view_maintenanceworkorder", "view_rentalcontract",
    },
    "Equipment Auditor": {
        "access_equipment_portal", "view_asset", "view_checkout", "view_checkoutitem",
        "view_returnrecord", "view_reservation", "view_maintenanceworkorder", "view_rentalcontract",
        "view_rentalasset", "view_equipment_audit", "export_equipment",
    },
    # Frozen to the permissions these roles had before Equipment Requests was
    # introduced. Do not use "*": new capabilities require explicit review.
    "Logistics Manager": "legacy-manager",
    "Sr. Logistics Manager": "legacy-manager",
    "Procurement Manager": "legacy-manager",
    "Logistics Specialist": {
        "access_equipment_portal", "view_asset", "checkout_asset", "return_asset", "view_checkout",
        "view_checkoutitem", "view_returnrecord", "add_reservation", "view_reservation",
        "view_maintenanceworkorder", "print_asset_labels",
    },
    "Procurement Specialist": {
        "access_equipment_portal", "view_asset", "view_checkout", "view_reservation",
        "view_maintenanceworkorder", "view_rentalcontract", "view_rentalasset", "manage_rentals",
        "view_asset_costs", "export_equipment",
    },
}

LEGACY_MANAGER_PERMISSIONS = {
    "access_equipment_portal", "access_equipment_requests", "checkout_asset", "export_equipment",
    "import_equipment", "manage_equipment", "manage_maintenance", "manage_rentals",
    "manage_reservations", "print_asset_labels", "return_asset", "view_asset_costs",
    "view_equipment_audit",
}
# Freeze the standard CRUD model list too, so future models are not silently granted.
LEGACY_MANAGER_MODELS = {
    "activecustody", "asset", "assetcomponent", "assetdocument", "assetevent", "assetidentifier",
    "checkout", "checkoutitem", "equipmentcategory", "equipmentimportbatch", "equipmentimportrow",
    "equipmentlocation", "equipmentmutationlock", "equipmentparty", "equipmentrequest",
    "equipmentrequestallocation", "equipmentrequestevent", "equipmentrequestline", "equipmentvendor",
    "maintenanceplan", "maintenanceworkorder", "rentalasset", "rentalcontract", "reservation",
    "reservationasset", "returnrecord", "vehiclemeterreading",
}
LEGACY_MANAGER_PERMISSIONS |= {
    f"{action}_{model}" for action in ("add", "change", "delete", "view") for model in LEGACY_MANAGER_MODELS
}


@receiver(post_migrate, dispatch_uid="equipment.provision_roles")
def provision_equipment_roles(sender, **kwargs):
    if sender.label != "equipment":
        return
    permissions = Permission.objects.filter(content_type__app_label="equipment")
    by_codename = {permission.codename: permission for permission in permissions}
    if not by_codename:
        return
    for group_name, codenames in ROLE_PERMISSIONS.items():
        group, _ = Group.objects.get_or_create(name=group_name)
        if codenames == "*":
            assigned = permissions
        else:
            if codenames == "legacy-manager":
                codenames = LEGACY_MANAGER_PERMISSIONS
            assigned = [by_codename[name] for name in codenames if name in by_codename]
        # Exact semantics clean stale equipment grants left by older versions.
        group.permissions.remove(*permissions)
        group.permissions.add(*assigned)
