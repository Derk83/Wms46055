from django.contrib.auth.models import Group, Permission
from django.db.models.signals import post_migrate
from django.dispatch import receiver

from .models import ManagedGroupRole


@receiver(post_migrate, dispatch_uid="inventory.provision_material_requests_group")
def provision_material_requests_group(sender, **kwargs):
    if sender.label != "inventory":
        return
    codenames = {
        "add_materialrequest", "view_materialrequest", "change_materialrequest", "delete_materialrequest",
        "add_materialrequestline", "view_materialrequestline", "change_materialrequestline", "delete_materialrequestline",
        "view_inventoryitem", "access_material_request_portal",
    }
    permissions = Permission.objects.filter(content_type__app_label="inventory", codename__in=codenames)
    role = ManagedGroupRole.objects.select_related("group").filter(role_key="material_requests").first()
    if role is not None:
        group = role.group
    else:
        group, _ = Group.objects.get_or_create(name="Material Requests")
        ManagedGroupRole.objects.create(role_key="material_requests", group=group)
    group.permissions.add(*permissions)
    unsafe_ticket_permissions = Permission.objects.filter(
        content_type__app_label="inventory",
        codename__in={"view_pickticket", "print_pickticket"},
    )
    group.permissions.remove(*unsafe_ticket_permissions)


@receiver(post_migrate, dispatch_uid="inventory.provision_shortage_workflow_permissions")
def provision_shortage_workflow_permissions(sender, **kwargs):
    if sender.label != "inventory":
        return
    permission_map = {
        "Logistics Specialist": {
            "view_materialbackorder", "view_backorderfulfillment", "manage_backorders",
        },
        "Logistics Manager": {
            "view_materialbackorder", "view_backorderfulfillment", "manage_backorders",
            "view_procurementrequisition",
        },
        "Sr. Logistics Manager": {
            "view_materialbackorder", "view_backorderfulfillment", "manage_backorders",
            "view_procurementrequisition", "manage_procurement_requisitions",
        },
        "Procurement Specialist": {
            "view_materialbackorder", "view_backorderfulfillment", "manage_backorders",
            "view_procurementrequisition", "manage_procurement_requisitions",
        },
        "Procurement Manager": {
            "view_materialbackorder", "view_backorderfulfillment", "manage_backorders",
            "view_procurementrequisition", "manage_procurement_requisitions",
        },
    }
    for group_name, codenames in permission_map.items():
        group = Group.objects.filter(name=group_name).first()
        if group is None:
            continue
        permissions = Permission.objects.filter(
            content_type__app_label="inventory", codename__in=codenames
        )
        group.permissions.add(*permissions)
