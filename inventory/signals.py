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
