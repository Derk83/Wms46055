from django.contrib.auth.models import Group, Permission, User
from django.test import Client, TestCase
from django.urls import reverse

from .models import CycleCount, InventoryItem, InventoryTransaction, ReceivingTicket


class UserDeletionSafetyTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(username="user-manager", password="ManagerPass!928")
        self.manager.user_permissions.add(
            Permission.objects.get(content_type__app_label="inventory", codename="manage_users")
        )
        self.client.force_login(self.manager)

    def test_protected_history_removes_access_without_deleting_identity(self):
        target = User.objects.create_user(
            username="mobileqa-pattern", email="mobileqa@example.com", password="TargetPass!928",
            is_staff=True,
        )
        group = Group.objects.create(name="Temporary operators")
        target.groups.add(group)
        target.user_permissions.add(
            Permission.objects.get(content_type__app_label="inventory", codename="view_inventoryitem")
        )
        cycle_count = CycleCount.objects.create(name="Mobile QA count", created_by=target)
        receiving_ticket = ReceivingTicket.objects.create(created_by=target)
        target_client = Client()
        target_client.force_login(target)

        confirmation = self.client.get(reverse("user_delete", args=[target.pk]))
        self.assertEqual(confirmation.status_code, 200)
        self.assertContains(confirmation, "protected operational history")
        self.assertContains(confirmation, "1 cycle counts")
        self.assertContains(confirmation, "1 receiving tickets")
        self.assertContains(confirmation, "Remove Access")

        response = self.client.post(reverse("user_delete", args=[target.pk]), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Access removed for &#x27;mobileqa-pattern&#x27;")
        target.refresh_from_db()
        self.assertFalse(target.is_active)
        self.assertFalse(target.is_staff)
        self.assertFalse(target.is_superuser)
        self.assertFalse(target.has_usable_password())
        self.assertFalse(target.groups.exists())
        self.assertFalse(target.user_permissions.exists())
        self.assertEqual(CycleCount.objects.get(pk=cycle_count.pk).created_by, target)
        self.assertEqual(ReceivingTicket.objects.get(pk=receiving_ticket.pk).created_by, target)
        self.assertRedirects(
            target_client.get(reverse("dashboard")),
            f"{reverse('login')}?next={reverse('dashboard')}",
        )

    def test_set_null_ledger_attribution_also_retains_account_identity(self):
        target = User.objects.create_user(username="ledger-actor", password="TargetPass!928")
        item = InventoryItem.objects.create(part_number="AUDIT-USER-1", name="Audit user item")
        transaction_row = item.adjust_quantity(
            1,
            InventoryTransaction.TransactionType.ADJUSTMENT,
            user=target,
            notes="User retention regression",
        )

        confirmation = self.client.get(reverse("user_delete", args=[target.pk]))
        self.assertContains(confirmation, "1 inventory transactions")

        response = self.client.post(reverse("user_delete", args=[target.pk]))

        self.assertEqual(response.status_code, 302)
        target.refresh_from_db()
        transaction_row.refresh_from_db()
        self.assertFalse(target.is_active)
        self.assertEqual(transaction_row.created_by, target)

    def test_unused_user_is_deleted(self):
        target = User.objects.create_user(username="unused-account", password="TargetPass!928")

        confirmation = self.client.get(reverse("user_delete", args=[target.pk]))
        self.assertContains(confirmation, "will be permanently deleted")
        self.assertContains(confirmation, "Delete User")

        response = self.client.post(reverse("user_delete", args=[target.pk]), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(pk=target.pk).exists())
        self.assertContains(response, "User &#x27;unused-account&#x27; deleted")

    def test_non_superuser_cannot_remove_superuser(self):
        target = User.objects.create_superuser(
            username="protected-admin", email="admin@example.com", password="AdminPass!928"
        )

        response = self.client.post(reverse("user_delete", args=[target.pk]), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(User.objects.filter(pk=target.pk, is_active=True, is_superuser=True).exists())
        self.assertContains(response, "Only a superuser can remove another superuser account")
