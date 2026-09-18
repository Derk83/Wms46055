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
    "Logistics Manager": "*",
    "Sr. Logistics Manager": "*",
    "Procurement Manager": "*",
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
        assigned = permissions if codenames == "*" else [by_codename[name] for name in codenames if name in by_codename]
        group.permissions.add(*assigned)
