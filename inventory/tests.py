import io

import openpyxl
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import Group, Permission, User

from .forms import PickTicketForm, PickTicketLineForm
from .models import ApprovalRequest, InventoryItem, InventoryTransaction, MaterialRequest, PickTicket, PickTicketLine, ReceivingTicket, ReceivingLine


class TicketImprovementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="creator", password="pw", is_staff=True, is_superuser=True)
        self.picker = User.objects.create_user(username="picker", first_name="Pat", last_name="Picker")
        self.client.login(username="creator", password="pw")
        self.item_a = InventoryItem.objects.create(part_number="WH-A", name="Gloves", description="Cut resistant gloves", category="Safety", quantity_on_hand=10, barcode_value="BCA")
        self.item_b = InventoryItem.objects.create(part_number="WH-B", name="Helmet", description="Hard hat helmet", category="PPE", quantity_on_hand=10, qr_code_value="QRB")

    def test_bbx_rplwms_domain_is_trusted(self):
        self.assertIn("bbx.rplwms.com", settings.ALLOWED_HOSTS)
        self.assertIn("https://bbx.rplwms.com", settings.CSRF_TRUSTED_ORIGINS)

    def test_app_qr_code_uses_bbx_rplwms_domain(self):
        response = self.client.get(reverse("app_qr_code"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["app_url"], "https://bbx.rplwms.com")

    def test_groups_settings_page_loads_permissions(self):
        """Legacy groups route redirects to the canonical groups settings tab."""
        manager = Group.objects.create(name="Manager")
        permission = Permission.objects.filter(codename="view_inventoryitem").first()
        if permission:
            manager.permissions.add(permission)

        response = self.client.get(reverse("group_management"))

        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, f"{reverse('settings')}?tab=groups")

    def test_groups_page_shows_material_handler_group_when_created(self):
        """Legacy groups route redirects to the canonical groups settings tab."""
        material_handler = Group.objects.create(name="Material Handler")

        response = self.client.get(reverse("group_management"))

        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, f"{reverse('settings')}?tab=groups")

    def test_group_update_uses_only_submitted_group_permission_field(self):
        """Group update now redirects to user management — verify redirect."""
        manager = Group.objects.create(name="Manager")
        lead = Group.objects.create(name="Lead")
        manager_perm = Permission.objects.filter(codename="view_inventoryitem").first()
        lead_perm = Permission.objects.filter(codename="change_inventoryitem").first()
        self.assertIsNotNone(manager_perm)
        self.assertIsNotNone(lead_perm)

        response = self.client.post(reverse("group_update", args=["Manager"]), {
            f"permissions_{manager.pk}": [str(manager_perm.pk)],
            f"permissions_{lead.pk}": [str(lead_perm.pk)],
        })

        self.assertRedirects(response, f"{reverse('settings')}?tab=groups")
        # Permissions are no longer updated via group_update — use group permissions.

    def test_self_edit_preserves_disabled_status_and_groups(self):
        manager = Group.objects.create(name="Manager")
        self.user.groups.add(manager)

        response = self.client.post(reverse("user_edit", args=[self.user.pk]), {
            "username": self.user.username,
            "first_name": "Updated",
            "last_name": "User",
            "email": "updated@example.com",
            "password": "newpw",
        })

        self.assertRedirects(response, reverse("user_management"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertTrue(self.user.is_staff)
        self.assertTrue(self.user.is_superuser)
        self.assertTrue(self.user.groups.filter(pk=manager.pk).exists())
        self.assertTrue(self.user.check_password("newpw"))

    def test_user_edit_page_has_inline_group_crud_controls(self):
        group = Group.objects.create(name="Warehouse Staff")

        response = self.client.get(reverse("user_edit", args=[self.picker.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="group-create-form"', html=False)
        self.assertContains(response, reverse("group_permissions", args=[group.pk]))
        self.assertContains(response, reverse("group_delete", args=[group.pk]))
        self.assertContains(response, "Add Group")
        self.assertContains(response, "Edit Permissions")

    def test_inline_group_create_delete(self):
        edit_url = reverse("user_edit", args=[self.picker.pk])

        create_response = self.client.post(reverse("group_create"), {
            "name": "Material Handler",
            "next": edit_url,
        })
        self.assertRedirects(create_response, edit_url)
        group = Group.objects.get(name="Material Handler")

        delete_response = self.client.post(reverse("group_delete", args=[group.pk]), {
            "next": edit_url,
        })
        self.assertRedirects(delete_response, edit_url)
        self.assertFalse(Group.objects.filter(pk=group.pk).exists())

    def test_new_user_page_can_assign_groups(self):
        group = Group.objects.create(name="Material Handler")

        response = self.client.get(reverse("user_create"))

        self.assertContains(response, f'name="groups" value="{group.pk}"', html=False)
        self.assertNotContains(response, f'value="{group.pk}"\n                                    disabled', html=False)

    def test_group_permissions_page_can_update_only_selected_group(self):
        manager = Group.objects.create(name="Manager")
        lead = Group.objects.create(name="Lead")
        view_perm = Permission.objects.get(codename="view_inventoryitem")
        change_perm = Permission.objects.get(codename="change_inventoryitem")
        lead.permissions.add(change_perm)

        get_response = self.client.get(reverse("group_permissions", args=[manager.pk]))
        self.assertEqual(get_response.status_code, 200)
        self.assertContains(get_response, "Edit Group Permissions: Manager")
        self.assertContains(get_response, 'name="permissions"', html=False)

        response = self.client.post(reverse("group_permissions", args=[manager.pk]), {
            "permissions": [str(view_perm.pk)],
        })

        self.assertRedirects(response, f"{reverse('settings')}?tab=groups")
        self.assertEqual(set(manager.permissions.values_list("pk", flat=True)), {view_perm.pk})
        self.assertEqual(set(lead.permissions.values_list("pk", flat=True)), {change_perm.pk})

    def test_inventory_permission_catalog_includes_recent_feature_permissions(self):
        expected = {
            "import_inventory",
            "export_inventory",
            "bulk_adjust_inventory",
            "clear_inventory",
            "manage_storage_locations",
            "view_qr_codes",
            "print_qr_codes",
            "view_app_qr_code",
            "scan_codes",
            "receive_stock",
            "view_receiving_log",
            "print_receivingticket",
            "print_pickticket",
            "manage_users",
            "manage_groups",
            "manage_group_permissions",
        }

        actual = set(Permission.objects.filter(content_type__app_label="inventory").values_list("codename", flat=True))

        self.assertTrue(expected.issubset(actual), expected - actual)

    def test_material_handler_ui_hides_admin_settings_and_unassigned_actions(self):
        material_handler = Group.objects.create(name="Material Handler")
        for codename in ["view_inventoryitem", "add_pickticket", "scan_codes"]:
            material_handler.permissions.add(Permission.objects.get(codename=codename))
        adam = User.objects.create_user(username="adam", password="pw", is_staff=True)
        adam.groups.add(material_handler)
        self.client.force_login(adam)

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("inventory_list"))
        self.assertContains(response, reverse("scanner"))
        self.assertNotContains(response, ">Admin<", html=False)
        self.assertNotContains(response, ">Settings<", html=False)
        self.assertNotContains(response, reverse("user_management"))
        self.assertNotContains(response, reverse("qr_codes"))
        self.assertNotContains(response, reverse("receiving"))
        self.assertNotContains(response, "Bulk Adjust")
        self.assertNotContains(response, "Add Inventory Item")

    def test_permissions_block_direct_access_to_restricted_pages(self):
        material_handler = Group.objects.create(name="Material Handler")
        material_handler.permissions.add(Permission.objects.get(codename="view_inventoryitem"))
        adam = User.objects.create_user(username="adam2", password="pw")
        adam.groups.add(material_handler)
        self.client.force_login(adam)

        restricted_urls = [
            reverse("settings"),
            reverse("user_management"),
            reverse("qr_codes"),
            reverse("receiving"),
            reverse("bulk_adjust"),
        ]
        for url in restricted_urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.url, reverse("inventory_list"))

    def test_all_users_can_see_full_inventory_without_view_permission(self):
        user = User.objects.create_user(username="viewer", password="pw")
        self.client.force_login(user)

        response = self.client.get(reverse("inventory_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "WH-A")
        self.assertContains(response, "WH-B")
        self.assertNotContains(response, "Bulk Edit Selected")
        self.assertNotContains(response, reverse("inventory_edit", args=[self.item_a.pk]))
        self.assertNotContains(response, reverse("receive_stock", args=[self.item_a.pk]))

    def test_unauthorized_action_redirect_shows_error_popup(self):
        user = User.objects.create_user(username="blocked", password="pw")
        self.client.force_login(user)

        response = self.client.get(reverse("bulk_adjust"), follow=True)

        self.assertRedirects(response, reverse("inventory_list"))
        self.assertContains(response, "You do not have permission to do that.")
        self.assertContains(response, "data-popup-message=\"You do not have permission to do that.\"")

    def test_assigned_permissions_show_matching_navigation(self):
        qr_group = Group.objects.create(name="QR Printer")
        for codename in ["view_inventoryitem", "view_qr_codes", "receive_stock", "view_receiving_log"]:
            qr_group.permissions.add(Permission.objects.get(codename=codename))
        user = User.objects.create_user(username="qruser", password="pw")
        user.groups.add(qr_group)
        self.client.force_login(user)

        response = self.client.get(reverse("dashboard"))

        self.assertContains(response, reverse("inventory_list"))
        self.assertContains(response, reverse("qr_codes"))
        self.assertContains(response, reverse("receiving"))
        self.assertNotContains(response, ">Admin<", html=False)
        self.assertNotContains(response, ">Settings<", html=False)

    def test_pick_ticket_form_uses_user_dropdown_and_received_by_is_optional(self):
        form = PickTicketForm(data={
            "date": "2026-06-19T10:00",
            "status": PickTicket.Status.OPEN,
            "picked_by_name": str(self.picker.pk),
            "received_by_name": "",
            "requested_by_name": "Derek",
            "building_room": "Warehouse",
            "location": "Aisle 1",
            "notes": "",
        })
        self.assertTrue(form.is_valid(), form.errors)
        ticket = form.save(commit=False)
        self.assertEqual(ticket.picked_by_name, "Pat Picker")
        self.assertEqual(ticket.received_by_name, "")

    def test_pick_ticket_item_select_is_sorted_by_category_and_searchable_classed(self):
        form = PickTicketLineForm()
        self.assertEqual(list(form.fields["item"].queryset), [self.item_b, self.item_a])
        self.assertIn("searchable-item-select", form.fields["item"].widget.attrs.get("class", ""))

    def test_pick_ticket_item_rows_have_dropdown_search_and_scan_emoji(self):
        response = self.client.get(reverse("ticket_create"))
        self.assertContains(response, 'class="item-search-input"', html=False)
        self.assertContains(response, 'placeholder="Search by part #, name, category, or bin…"', html=False)
        self.assertContains(response, 'class="searchable-item-select"', html=False)
        self.assertContains(response, 'class="scan-code-hidden"', html=False)
        self.assertContains(response, "scan-emoji-btn")
        self.assertContains(response, 'aria-label="Scan barcode or QR code"', html=False)
        self.assertContains(response, "📷")
        self.assertContains(response, "filterItemDropdown")
        self.assertContains(response, "initItemPicker")
        self.assertContains(response, "startTicketScanner")

    def test_pick_ticket_create_rejects_line_quantity_without_item_instead_of_integrity_error(self):
        before = PickTicket.objects.count()

        response = self.client.post(reverse("ticket_create"), {
            "date": "2026-06-24T19:20",
            "status": PickTicket.Status.OPEN,
            "picked_by_name": str(self.picker.pk),
            "received_by_name": "Receiver",
            "requested_by_name": "Req",
            "building_room": "B1",
            "location": "L1",
            "notes": "",
            "lines-TOTAL_FORMS": "1",
            "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "0",
            "lines-MAX_NUM_FORMS": "1000",
            "lines-0-scan_code": "",
            "lines-0-item": "",
            "lines-0-quantity": "2",
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(PickTicket.objects.count(), before)
        self.assertEqual(PickTicketLine.objects.filter(item__isnull=True).count(), 0)

    def test_completed_pick_ticket_can_be_edited_and_inventory_is_rebalanced(self):
        ticket = PickTicket.objects.create(
            status=PickTicket.Status.CLOSED,
            picked_by_name="Old",
            received_by_name="Receiver",
            requested_by_name="Req",
            building_room="B1",
            location="L1",
            created_by=self.user,
        )
        PickTicketLine.objects.create(ticket=ticket, item=self.item_a, quantity=2)
        self.item_a.refresh_from_db()
        self.assertEqual(self.item_a.quantity_on_hand, 8)

        response = self.client.post(reverse("ticket_edit", args=[ticket.pk]), {
            "date": ticket.date.strftime("%Y-%m-%dT%H:%M"),
            "status": PickTicket.Status.CLOSED,
            "picked_by_name": str(self.picker.pk),
            "received_by_name": "",
            "requested_by_name": "Req2",
            "building_room": "B2",
            "location": "L2",
            "notes": "updated",
            "lines-TOTAL_FORMS": "1",
            "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "1",
            "lines-MAX_NUM_FORMS": "1000",
            "lines-0-scan_code": "",
            "lines-0-item": str(self.item_b.pk),
            "lines-0-quantity": "3",
        })
        self.assertRedirects(response, reverse("ticket_list"))
        ticket.refresh_from_db()
        self.item_a.refresh_from_db()
        self.item_b.refresh_from_db()
        self.assertEqual(ticket.picked_by_name, "Pat Picker")
        self.assertEqual(ticket.lines.count(), 1)
        self.assertEqual(ticket.lines.first().item, self.item_b)
        self.assertEqual(self.item_a.quantity_on_hand, 10)
        self.assertEqual(self.item_b.quantity_on_hand, 7)

    def test_receiving_ticket_can_be_edited_and_inventory_is_rebalanced(self):
        rt = ReceivingTicket.objects.create(po_number="PO1", notes="old", created_by=self.user)
        ReceivingLine.objects.create(ticket=rt, item=self.item_a, quantity=4, notes="old line")
        self.item_a.refresh_from_db()
        self.assertEqual(self.item_a.quantity_on_hand, 14)

        response = self.client.post(reverse("receiving_ticket_edit", args=[rt.pk]), {
            "date": rt.date.strftime("%Y-%m-%dT%H:%M"),
            "po_number": "PO2",
            "notes": "new",
            "lines-TOTAL_FORMS": "1",
            "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "1",
            "lines-MAX_NUM_FORMS": "1000",
            "lines-0-item": str(self.item_b.pk),
            "lines-0-quantity": "2",
            "lines-0-notes": "new line",
        })
        self.assertRedirects(response, reverse("receiving_ticket_print", args=[rt.pk]))
        rt.refresh_from_db()
        self.item_a.refresh_from_db()
        self.item_b.refresh_from_db()
        self.assertEqual(rt.po_number, "PO2")
        self.assertEqual(rt.lines.count(), 1)
        self.assertEqual(rt.lines.first().item, self.item_b)
        self.assertEqual(self.item_a.quantity_on_hand, 10)
        self.assertEqual(self.item_b.quantity_on_hand, 12)


    def test_receiving_ticket_edit_page_posts_rendered_form_successfully(self):
        rt = ReceivingTicket.objects.create(po_number="PO-OLD", notes="old", created_by=self.user)
        line = ReceivingLine.objects.create(ticket=rt, item=self.item_a, quantity=4, notes="old line")
        self.item_a.refresh_from_db()
        self.assertEqual(self.item_a.quantity_on_hand, 14)

        get_response = self.client.get(reverse("receiving_ticket_edit", args=[rt.pk]))
        self.assertContains(get_response, 'name="po_number"')
        self.assertContains(get_response, f'name="lines-0-id" value="{line.pk}"', html=False)
        self.assertContains(get_response, "Save Changes")

        response = self.client.post(reverse("receiving_ticket_edit", args=[rt.pk]), {
            "date": rt.date.strftime("%Y-%m-%dT%H:%M"),
            "po_number": "PO-NEW",
            "notes": "updated note",
            "lines-TOTAL_FORMS": "2",
            "lines-INITIAL_FORMS": "1",
            "lines-MIN_NUM_FORMS": "1",
            "lines-MAX_NUM_FORMS": "1000",
            "lines-0-id": str(line.pk),
            "lines-0-item": str(self.item_b.pk),
            "lines-0-quantity": "2",
            "lines-0-notes": "changed line",
            "lines-1-id": "",
            "lines-1-item": "",
            "lines-1-quantity": "",
            "lines-1-notes": "",
        })
        self.assertRedirects(response, reverse("receiving_ticket_print", args=[rt.pk]))
        rt.refresh_from_db()
        self.item_a.refresh_from_db()
        self.item_b.refresh_from_db()
        self.assertEqual(rt.po_number, "PO-NEW")
        self.assertEqual(rt.notes, "updated note")
        self.assertEqual(rt.lines.count(), 1)
        self.assertEqual(rt.lines.first().item, self.item_b)
        self.assertEqual(rt.lines.first().quantity, 2)
        self.assertEqual(self.item_a.quantity_on_hand, 10)
        self.assertEqual(self.item_b.quantity_on_hand, 12)

    def test_inventory_page_includes_rear_camera_scanner_controls(self):
        response = self.client.get(reverse("inventory_list"))
        self.assertContains(response, "Start Camera Scan")
        self.assertContains(response, "facingMode: { exact: 'environment' }")
        self.assertContains(response, "inventory-camera-reader")
        self.assertContains(response, "Camera starting")
        self.assertContains(response, "Camera active")
        self.assertContains(response, "📷 Stop")
        self.assertContains(response, "Camera off. Click 📷 to scan again.")
        self.assertContains(response, "Camera permission denied")
        self.assertContains(response, "scan-lookup")

    def test_standalone_scanner_surfaces_camera_status_states(self):
        response = self.client.get(reverse("scanner"))
        self.assertContains(response, "Phone Barcode / QR Scanner")
        self.assertContains(response, "Camera starting")
        self.assertContains(response, "Camera active")
        self.assertContains(response, "Camera permission denied")
        self.assertContains(response, "Scanner library could not load")
        self.assertContains(response, "min-height")

    def test_receiving_log_shows_correct_total_quantity_and_edit_print_actions(self):
        rt = ReceivingTicket.objects.create(po_number="PO-TOTAL", created_by=self.user)
        ReceivingLine.objects.create(ticket=rt, item=self.item_a, quantity=4)
        ReceivingLine.objects.create(ticket=rt, item=self.item_b, quantity=6)

        response = self.client.get(reverse("receiving_log"))

        self.assertContains(response, "PO-TOTAL")
        self.assertContains(response, ">10<", html=False)
        self.assertContains(response, reverse("receiving_ticket_print", args=[rt.pk]))
        self.assertContains(response, reverse("receiving_ticket_edit", args=[rt.pk]))
        self.assertContains(response, reverse("receiving_ticket_delete", args=[rt.pk]))
        self.assertContains(response, "Remove")

    def test_receiving_ticket_print_uses_black_box_full_page_style(self):
        rt = ReceivingTicket.objects.create(po_number="PO-PRINT", vendor="Graybar", created_by=self.user)
        ReceivingLine.objects.create(ticket=rt, item=self.item_a, quantity=4, shipper="UPS")

        response = self.client.get(reverse("receiving_ticket_print", args=[rt.pk]))

        self.assertContains(response, "brand-bar")
        self.assertContains(response, "blackbox-logo.png")
        self.assertContains(response, 'alt="RPL Warehouse"')
        self.assertContains(response, "min-height: calc(100vh - 0.4in)")
        self.assertContains(response, "margin: 0.2in")
        self.assertContains(response, "openPrintDialog")
        self.assertContains(response, "window.print")
        self.assertContains(response, "Receiving Ticket")
        self.assertContains(response, "PO-PRINT")
        self.assertContains(response, "Graybar")

    def test_pick_ticket_print_matches_last_standalone_black_box_ticket(self):
        pick = PickTicket.objects.create(
            status=PickTicket.Status.OPEN,
            picked_by_name="Picker",
            received_by_name="Receiver",
            requested_by_name="Req",
            building_room="B1",
            location="L1",
            notes="Leave at receiving desk",
            created_by=self.user,
        )
        PickTicketLine.objects.create(ticket=pick, item=self.item_b, quantity=3)

        response = self.client.get(reverse("ticket_print", args=[pick.pk]))

        self.assertContains(response, "Pick Ticket")
        self.assertContains(response, "@page{size:Letter landscape;margin:.24in}")
        self.assertNotContains(response, "CHECKED BY")
        self.assertContains(response, "QA BY")
        self.assertContains(response, "PICKED BY")
        self.assertContains(response, "POC (POINT OF CONTACT)")
        self.assertContains(response, "DELIVERY LOCATION")
        self.assertContains(response, "ITEMS TO PICK")
        self.assertContains(response, "QTY REQUESTED")
        self.assertContains(response, "QTY FILLED")
        self.assertContains(response, "DELIVERY / RECEIPT CONFIRMATION")
        self.assertContains(response, "blackbox-logo.png")
        self.assertContains(response, pick.ticket_number)
        self.assertContains(response, "Picker")
        self.assertContains(response, "Req")
        self.assertContains(response, "B1 — L1")
        self.assertContains(response, "WH-B")
        self.assertContains(response, "Leave at receiving desk")
        self.assertContains(response, "backupAndPrint")
        self.assertContains(response, reverse("ticket_print_pdf", args=[pick.pk]))
        self.assertContains(response, 'data-client-generated-at', html=False)
        self.assertContains(response, "new Date()")
        self.assertContains(response, "Intl.DateTimeFormat")

    def test_material_request_pick_ticket_print_shows_scheduled_delivery(self):
        from datetime import datetime, timezone as datetime_timezone
        from .services import create_material_request

        scheduled = datetime(2026, 9, 15, 14, 30, tzinfo=datetime_timezone.utc)
        request_obj = create_material_request(
            creator=self.user,
            requestor_name="Schedule Tester",
            building_room="B2",
            location="Dock",
            notes="",
            delivery_at=scheduled,
            lines=[{"item": self.item_b, "quantity": 1, "notes": ""}],
        )

        response = self.client.get(reverse("ticket_print", args=[request_obj.pick_ticket_id]))

        self.assertContains(response, "SCHEDULED DELIVERY")
        self.assertContains(response, "09/15/2026")
        self.assertContains(response, "2:30 PM")

    def test_pick_ticket_pdf_download_creates_local_backup_file(self):
        pick = PickTicket.objects.create(
            status=PickTicket.Status.OPEN,
            picked_by_name="Picker",
            requested_by_name="Req",
            building_room="B1",
            location="L1",
            created_by=self.user,
        )
        PickTicketLine.objects.create(ticket=pick, item=self.item_b, quantity=2)

        response = self.client.get(reverse("ticket_print_pdf", args=[pick.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(
            response["Content-Disposition"],
            f'attachment; filename="{pick.ticket_number}_{pick.date.date().isoformat()}.pdf"',
        )
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_receiving_ticket_edit_and_delete_are_admin_only(self):
        rt = ReceivingTicket.objects.create(po_number="PO-ADMIN", created_by=self.user)
        ReceivingLine.objects.create(ticket=rt, item=self.item_a, quantity=4)
        for codename in ["view_receiving_log", "change_receivingticket", "delete_receivingticket", "print_receivingticket"]:
            self.user.user_permissions.add(Permission.objects.get(codename=codename))
        normal = User.objects.create_user(username="normal", password="pw")
        normal.user_permissions.add(Permission.objects.get(codename="view_receiving_log"))
        self.client.logout()
        self.client.login(username="normal", password="pw")

        edit_response = self.client.get(reverse("receiving_ticket_edit", args=[rt.pk]))
        delete_response = self.client.get(reverse("receiving_ticket_delete", args=[rt.pk]))
        log_response = self.client.get(reverse("receiving_log"))

        self.assertEqual(edit_response.status_code, 302)
        self.assertEqual(delete_response.status_code, 302)
        self.assertNotContains(log_response, reverse("receiving_ticket_edit", args=[rt.pk]))
        self.assertNotContains(log_response, reverse("receiving_ticket_delete", args=[rt.pk]))
        self.assertNotContains(log_response, reverse("receiving_ticket_print", args=[rt.pk]))

    def test_receiving_ticket_delete_reverses_inventory_for_admin(self):
        rt = ReceivingTicket.objects.create(po_number="PO-DELETE", created_by=self.user)
        ReceivingLine.objects.create(ticket=rt, item=self.item_a, quantity=4)
        self.item_a.refresh_from_db()
        self.assertEqual(self.item_a.quantity_on_hand, 14)

        response = self.client.post(reverse("receiving_ticket_delete", args=[rt.pk]))

        self.assertRedirects(response, reverse("receiving_log"))
        self.assertFalse(ReceivingTicket.objects.filter(pk=rt.pk).exists())
        self.item_a.refresh_from_db()
        self.assertEqual(self.item_a.quantity_on_hand, 10)

    def test_transaction_type_badges_are_color_coded(self):
        self.item_a.adjust_quantity(
            -1,
            InventoryTransaction.TransactionType.PICK,
            user=self.user,
            notes="Pick badge test",
        )
        self.item_a.adjust_quantity(
            2,
            InventoryTransaction.TransactionType.RECEIPT,
            user=self.user,
            notes="Receipt badge test",
        )
        self.item_a.adjust_quantity(
            3,
            InventoryTransaction.TransactionType.ADJUSTMENT,
            user=self.user,
            notes="Adjustment badge test",
        )

        response = self.client.get(reverse("transaction_history"))

        self.assertContains(response, "tx-type-pick")
        self.assertContains(response, "tx-type-receipt")
        self.assertContains(response, "tx-type-adjustment")
        self.assertContains(response, "tx-type-badge")

    def test_qr_codes_page_loads_with_all_locations(self):
        response = self.client.get(reverse("qr_codes"))

        self.assertContains(response, "QR Codes")
        self.assertContains(response, "480 total")
        self.assertContains(response, "D-20-06")
        self.assertContains(response, "🖨️ Print")
        self.assertContains(response, "printQrCard")

    def test_inventory_list_filters_by_part_po_category_low_stock_and_sort(self):
        rt = ReceivingTicket.objects.create(po_number="PO-FILTER", created_by=self.user)
        ReceivingLine.objects.create(ticket=rt, item=self.item_a, quantity=4)
        self.item_a.low_stock_threshold = 20
        self.item_a.save(update_fields=["low_stock_threshold"])

        response = self.client.get(reverse("inventory_list"), {"part_number": "WH-A"})
        self.assertContains(response, "WH-A")
        self.assertNotContains(response, "WH-B")

        response = self.client.get(reverse("inventory_list"), {"po_number": "PO-FILTER"})
        self.assertContains(response, "WH-A")
        self.assertNotContains(response, "WH-B")

        response = self.client.get(reverse("inventory_list"), {"category": "PPE"})
        self.assertContains(response, "WH-B")
        self.assertNotContains(response, "WH-A")

        response = self.client.get(reverse("inventory_list"), {"stock": "low"})
        self.assertContains(response, "WH-A")
        self.assertNotContains(response, "WH-B")

        response = self.client.get(reverse("inventory_list"), {"sort": "part_desc"})
        self.assertContains(response, 'name="part_number"')
        self.assertContains(response, 'name="po_number"')
        self.assertContains(response, 'name="stock"')
        self.assertContains(response, 'name="sort"')
        self.assertEqual(list(response.context["items"]), [self.item_b, self.item_a])

    def test_inventory_list_name_column_displays_description_and_clicks_to_detail(self):
        response = self.client.get(reverse("inventory_list"))
        self.assertContains(response, "<th>Name</th>", html=False)
        self.assertContains(response, f'href="{reverse("item_detail", args=[self.item_a.pk])}"', html=False)
        self.assertContains(response, "Cut resistant gloves")
        self.assertContains(response, "Hard hat helmet")

    def test_inventory_list_is_condensed_and_mobile_readable(self):
        response = self.client.get(reverse("inventory_list"))
        self.assertContains(response, 'class="inventory-table condensed compact-inventory"')
        self.assertContains(response, 'class="mobile-inventory-list"')
        self.assertContains(response, 'class="mobile-inventory-card"')
        self.assertContains(response, 'data-label="FB Part #"')
        self.assertNotContains(response, 'data-label="Shipper"')
        self.assertNotContains(response, 'data-label="Building/Room"')
        self.assertNotContains(response, '<th class="more-col">More</th>', html=False)
        self.assertContains(response, '@media (max-width: 760px)')

    def test_inventory_page_hides_shipper_but_export_retains_it(self):
        self.item_a.shipper = "Graybar"
        self.item_a.save(update_fields=["shipper"])

        response = self.client.get(reverse("inventory_list"))
        self.assertNotContains(response, '<th class="shipper-col">Shipper</th>', html=False)
        self.assertNotContains(response, 'data-label="Shipper"', html=False)

        response = self.client.get(reverse("export_inventory_xlsx"))
        workbook = openpyxl.load_workbook(io.BytesIO(response.content), data_only=True)
        sheet = workbook.active
        headers = [cell.value for cell in sheet[1]]
        self.assertEqual(headers[:7], ["Part #", "FB Part #", "Model #", "Name", "Shipper", "Category", "Description"])
        rows = {row[0]: row for row in sheet.iter_rows(min_row=2, values_only=True)}
        self.assertEqual(rows["WH-A"][3], "Cut resistant gloves")
        self.assertEqual(rows["WH-A"][4], "Graybar")
        self.assertEqual(rows["WH-A"][6], "Cut resistant gloves")

    def test_inventory_import_reads_shipper_column(self):
        upload = self._inventory_upload_file([
            ["Part #", "Name", "Shipper", "Category", "Description", "Qty"],
            ["IMP-1", "Imported fallback", "Westco", "Fiber", "Imported description", 7],
        ])
        response = self.client.post(reverse("inventory_import_xlsx"), {"spreadsheet": upload})
        self.assertRedirects(response, reverse("inventory_list"))
        item = InventoryItem.objects.get(part_number="IMP-1")
        self.assertEqual(item.name, "Imported description")
        self.assertEqual(item.description, "Imported description")
        self.assertEqual(item.shipper, "Westco")

    def test_inventory_import_accepts_existing_rack_location_model_layout_unchanged(self):
        upload = self._inventory_upload_file([
            ["Rack", "Location", "Type", "Part #", "Model #", "Description", "Qty", "Uom"],
            ["A", "02-03", "Equipment", "", "ZE-001932", "Pneumatic Minijet", 4, "ea"],
            ["A", "02-01", "Supplies", "S-5144", "ALT-5144", "Black Electrical Tape", 12, "rolls"],
        ])

        response = self.client.post(reverse("inventory_import_xlsx"), {"spreadsheet": upload})

        self.assertRedirects(response, reverse("inventory_list"))
        minijet = InventoryItem.objects.get(part_number="ZE-001932")
        self.assertEqual(minijet.name, "Pneumatic Minijet")
        self.assertEqual(minijet.category, "Equipment")
        self.assertEqual(minijet.quantity_on_hand, 4)
        self.assertEqual(minijet.unit, "ea")
        self.assertEqual((minijet.rack, minijet.section, minijet.bin_location), ("A", "02", "03"))
        tape = InventoryItem.objects.get(part_number="S-5144")
        self.assertEqual(tape.model_number, "ALT-5144")
        self.assertEqual((tape.rack, tape.section, tape.bin_location), ("A", "02", "01"))

    def test_location_fields_combine_rack_section_and_numeric_bin(self):
        self.item_a.rack = "A"
        self.item_a.section = "3"
        self.item_a.bin_location = "6"
        self.item_a.save()

        self.assertEqual(self.item_a.storage_location, "A-03-06")

    def test_inventory_form_uses_dropdowns_for_rack_section_and_bin_location(self):
        response = self.client.get(reverse("inventory_new"))

        self.assertContains(response, 'name="rack"', html=False)
        self.assertContains(response, 'name="section"', html=False)
        self.assertContains(response, 'name="bin_location"', html=False)
        self.assertContains(response, '<option value="A">A</option>', html=False)
        self.assertContains(response, '<option value="D">D</option>', html=False)
        self.assertContains(response, '<option value="01">01</option>', html=False)
        self.assertContains(response, '<option value="20">20</option>', html=False)
        self.assertContains(response, '<option value="06">06</option>', html=False)
        self.assertNotContains(response, 'value="R1"', html=False)
        self.assertNotContains(response, 'value="1L"', html=False)

    def test_inventory_import_allows_same_part_number_in_multiple_locations(self):
        upload = self._inventory_upload_file([
            ["Part #", "Name", "Category", "Description", "Qty", "Unit", "BLDG/Room #", "Rack", "Section", "Bin Location"],
            ["DUP-1", "Cable", "OFCI", "Cable at A-1-1", 5, "each", "WH", "A", "1", "1"],
            ["DUP-1", "Cable", "OFCI", "Cable at D-20-06", 2, "each", "WH", "D", "20", "6"],
        ])

        response = self.client.post(reverse("inventory_import_xlsx"), {"spreadsheet": upload})

        self.assertRedirects(response, reverse("inventory_list"))
        locations = sorted(InventoryItem.objects.filter(part_number="DUP-1").values_list("bin_location", "rack", "section", "quantity_on_hand"))
        self.assertEqual(locations, [("01", "A", "01", 5), ("06", "D", "20", 2)])

    def test_export_inventory_xlsx_has_location_columns_and_dropdown_validations(self):
        self.item_a.rack = "A"
        self.item_a.section = "3"
        self.item_a.bin_location = "6"
        self.item_a.save()

        response = self.client.get(reverse("export_inventory_xlsx"))
        workbook = openpyxl.load_workbook(io.BytesIO(response.content), data_only=True)
        sheet = workbook.active
        headers = [cell.value for cell in sheet[1]]
        self.assertIn("Rack", headers)
        self.assertIn("Section", headers)
        self.assertIn("Bin Location", headers)
        rows = {row[0]: row for row in sheet.iter_rows(min_row=2, values_only=True)}
        values = dict(zip(headers, rows["WH-A"]))
        self.assertEqual(values["Rack"], "A")
        self.assertEqual(values["Section"], "03")
        self.assertEqual(values["Bin Location"], "06")
        validations = [dv.formula1 for dv in sheet.data_validations.dataValidation]
        self.assertTrue(any('"A,B,C,D"' == formula for formula in validations))
        self.assertTrue(any("01,02,03" in formula and "19,20" in formula for formula in validations))
        self.assertTrue(any('"01,02,03,04,05,06"' == formula for formula in validations))

    def test_pick_and_receiving_tickets_show_combined_storage_location(self):
        self.item_a.rack = "A"
        self.item_a.section = "3"
        self.item_a.bin_location = "6"
        self.item_a.save()
        pick = PickTicket.objects.create(
            status=PickTicket.Status.OPEN,
            picked_by_name="Picker",
            received_by_name="Receiver",
            requested_by_name="Req",
            building_room="B1",
            location="L1",
            created_by=self.user,
        )
        PickTicketLine.objects.create(ticket=pick, item=self.item_a, quantity=1)
        receiving = ReceivingTicket.objects.create(po_number="PO-LOC", created_by=self.user)
        ReceivingLine.objects.create(ticket=receiving, item=self.item_a, quantity=1)

        pick_response = self.client.get(reverse("ticket_print", args=[pick.pk]))
        receiving_response = self.client.get(reverse("receiving_ticket_print", args=[receiving.pk]))

        self.assertContains(pick_response, "A-03-06")
        self.assertContains(receiving_response, "A-03-06")

    def test_receiving_page_add_line_can_set_location_for_new_item(self):
        ticket = ReceivingTicket.objects.create(po_number="PO-NEWLOC", created_by=self.user)
        session = self.client.session
        session["receiving_ticket_id"] = ticket.pk
        session.save()

        response = self.client.post(reverse("receiving"), {
            "action": "add_line",
            "item": "",
            "part_number": "NEW-LOC",
            "name": "Located item",
            "quantity": "4",
            "rack": "B",
            "section": "4",
            "bin_location": "2",
            "po_number": "",
            "shipper": "",
            "notes": "",
            "source": "file",
        })

        self.assertRedirects(response, reverse("receiving"))
        item = InventoryItem.objects.get(part_number="NEW-LOC")
        self.assertEqual(item.storage_location, "B-04-02")

    def test_receiving_existing_item_can_receive_into_a_different_location(self):
        self.item_a.rack = "A"
        self.item_a.section = "1"
        self.item_a.bin_location = "1"
        self.item_a.save()
        ticket = ReceivingTicket.objects.create(po_number="PO-DEST", created_by=self.user)
        session = self.client.session
        session["receiving_ticket_id"] = ticket.pk
        session.save()

        response = self.client.post(reverse("receiving"), {
            "action": "add_line",
            "item": str(self.item_a.pk),
            "part_number": "",
            "name": "",
            "quantity": "4",
            "rack": "C",
            "section": "12",
            "bin_location": "5",
            "po_number": "",
            "shipper": "UPS",
            "notes": "destination test",
            "source": "file",
        })

        self.assertRedirects(response, reverse("receiving"))
        self.item_a.refresh_from_db()
        destination = InventoryItem.objects.get(part_number="WH-A", rack="C", section="12", bin_location="05")
        self.assertEqual(self.item_a.quantity_on_hand, 10)
        self.assertEqual(destination.quantity_on_hand, 4)
        self.assertEqual(ticket.lines.get().item, destination)

    def test_receiving_rejects_partial_destination_location(self):
        ticket = ReceivingTicket.objects.create(po_number="PO-PARTIAL", created_by=self.user)
        session = self.client.session
        session["receiving_ticket_id"] = ticket.pk
        session.save()

        response = self.client.post(reverse("receiving"), {
            "action": "add_line",
            "item": str(self.item_a.pk),
            "part_number": "",
            "name": "",
            "quantity": "2",
            "rack": "D",
            "section": "",
            "bin_location": "3",
            "po_number": "",
            "shipper": "",
            "notes": "",
            "source": "file",
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose a rack, section, and bin together")
        self.assertFalse(ticket.lines.exists())

    def test_receiving_dashboard_has_compact_location_workflow(self):
        ticket = ReceivingTicket.objects.create(po_number="PO-DASH", created_by=self.user)
        session = self.client.session
        session["receiving_ticket_id"] = ticket.pk
        session.save()

        response = self.client.get(reverse("receiving"))

        self.assertContains(response, "Destination Location")
        self.assertContains(response, 'name="rack"', html=False)
        self.assertContains(response, 'name="section"', html=False)
        self.assertContains(response, 'name="bin_location"', html=False)
        self.assertContains(response, 'class="receiving-workspace"', html=False)
        self.assertContains(response, "Items on Ticket")

    def test_receiving_page_and_ticket_print_include_shipper_column(self):
        rt = ReceivingTicket.objects.create(po_number="PO-SHIP", vendor="Vendor", created_by=self.user)
        ReceivingLine.objects.create(ticket=rt, item=self.item_a, quantity=4, shipper="Amazon")

        response = self.client.get(reverse("receiving"))
        self.assertContains(response, "Shipper")
        self.assertContains(response, "Amazon")

        response = self.client.get(reverse("receiving_ticket_print", args=[rt.pk]))
        self.assertContains(response, "Shipper")
        self.assertContains(response, "Amazon")

    def test_receiving_log_can_search_by_part_number_and_vendor(self):
        grainger = ReceivingTicket.objects.create(po_number="PO-G", vendor="Grainger", created_by=self.user)
        fastenal = ReceivingTicket.objects.create(po_number="PO-F", vendor="Fastenal", created_by=self.user)
        ReceivingLine.objects.create(ticket=grainger, item=self.item_a, quantity=2)
        ReceivingLine.objects.create(ticket=fastenal, item=self.item_a, quantity=3)
        ReceivingLine.objects.create(ticket=fastenal, item=self.item_b, quantity=1)

        response = self.client.get(reverse("receiving_log"), {"part_number": "WH-A"})
        self.assertContains(response, "Grainger")
        self.assertContains(response, "Fastenal")
        self.assertContains(response, "Vendors for WH-A")
        self.assertContains(response, "Qty 2")
        self.assertContains(response, "Qty 3")
        self.assertContains(response, 'name="vendor"')

        response = self.client.get(reverse("receiving_log"), {"part_number": "WH-A", "vendor": "Grainger"})
        self.assertContains(response, "Grainger")
        self.assertNotContains(response, "Fastenal")

    def test_receiving_ticket_form_captures_vendor(self):
        response = self.client.post(reverse("receiving"), {
            "action": "create_receiving_ticket",
            "po_number": "PO-VENDOR",
            "vendor": "Graybar",
            "notes": "",
        })
        self.assertRedirects(response, reverse("receiving"))
        self.assertEqual(ReceivingTicket.objects.get(po_number="PO-VENDOR").vendor, "Graybar")

    def test_ticket_edit_page_has_save_button_and_updates_user_and_quantities(self):
        ticket = PickTicket.objects.create(
            status=PickTicket.Status.OPEN,
            picked_by_name="Old Picker",
            received_by_name="Receiver",
            requested_by_name="Req",
            building_room="B1",
            location="L1",
            created_by=self.user,
        )
        PickTicketLine.objects.create(ticket=ticket, item=self.item_a, quantity=1)

        get_response = self.client.get(reverse("ticket_edit", args=[ticket.pk]))
        self.assertContains(get_response, "Update Ticket")
        self.assertContains(get_response, f'name="lines-0-id" value="{ticket.lines.first().pk}"', html=False)

        response = self.client.post(reverse("ticket_edit", args=[ticket.pk]), {
            "date": ticket.date.strftime("%Y-%m-%dT%H:%M"),
            "status": PickTicket.Status.OPEN,
            "picked_by_name": str(self.picker.pk),
            "received_by_name": "New Receiver",
            "requested_by_name": "Req2",
            "building_room": "B2",
            "location": "L2",
            "notes": "updated",
            "lines-TOTAL_FORMS": "1",
            "lines-INITIAL_FORMS": "1",
            "lines-MIN_NUM_FORMS": "1",
            "lines-MAX_NUM_FORMS": "1000",
            "lines-0-id": str(ticket.lines.first().pk),
            "lines-0-scan_code": "",
            "lines-0-item": str(self.item_b.pk),
            "lines-0-quantity": "4",
        })
        self.assertRedirects(response, reverse("ticket_list"))
        ticket.refresh_from_db()
        self.item_a.refresh_from_db()
        self.item_b.refresh_from_db()
        self.assertEqual(ticket.picked_by_name, "Pat Picker")
        self.assertEqual(ticket.received_by_name, "New Receiver")
        self.assertEqual(ticket.lines.count(), 1)
        self.assertEqual(ticket.lines.first().item, self.item_b)
        self.assertEqual(ticket.lines.first().quantity, 4)
        self.assertEqual(self.item_a.quantity_on_hand, 10)
        self.assertEqual(self.item_b.quantity_on_hand, 6)

    def _inventory_upload_file(self, rows):
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "Inventory"
        default_headers = [
            "Part #", "Name", "Category", "Description", "Qty", "Unit",
            "BLDG/Room #", "Rack", "Section", "Bin Location", "Low Stock Threshold",
            "Barcode Value", "QR Code Value", "Active",
        ]
        if rows and str(rows[0][0]).strip().lower() in {"part #", "part", "part number", "rack"}:
            sheet.append(rows[0])
            rows = rows[1:]
        else:
            sheet.append(default_headers)
        for row in rows:
            sheet.append(row)
        buffer = io.BytesIO()
        workbook.save(buffer)
        return SimpleUploadedFile(
            "inventory-upload.xlsx",
            buffer.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def test_inventory_page_has_spreadsheet_import_upload_form(self):
        response = self.client.get(reverse("inventory_list"))
        self.assertContains(response, "Import Spreadsheet")
        self.assertContains(response, reverse("inventory_import_xlsx"))
        self.assertContains(response, 'name="spreadsheet"')
        self.assertContains(response, 'type="file"')

    def test_inventory_page_does_not_show_clear_inventory_form(self):
        response = self.client.get(reverse("inventory_list"))
        self.assertNotContains(response, "Clear Inventory")
        self.assertNotContains(response, reverse("inventory_clear"))
        self.assertContains(response, "Delete")

    def test_clear_inventory_refuses_to_destroy_receiving_ledger(self):
        ticket = PickTicket.objects.create(
            status=PickTicket.Status.OPEN,
            picked_by_name="Picker",
            received_by_name="Receiver",
            requested_by_name="Req",
            building_room="B1",
            location="L1",
            created_by=self.user,
        )
        pick_line = PickTicketLine.objects.create(ticket=ticket, item=self.item_a, quantity=1)
        receiving = ReceivingTicket.objects.create(po_number="PO-CLEAR", created_by=self.user)
        receiving_line = ReceivingLine.objects.create(ticket=receiving, item=self.item_b, quantity=2)
        adjustment = InventoryTransaction.record_adjustment(
            item=self.item_a,
            delta=1,
            user=self.user,
        )
        receipt = receiving_line.receipt_transaction

        response = self.client.post(reverse("inventory_clear"), {"confirm": "DELETE"})

        self.assertRedirects(response, reverse("inventory_list"))
        self.assertTrue(InventoryItem.objects.filter(pk=self.item_a.pk).exists())
        self.assertTrue(InventoryItem.objects.filter(pk=self.item_b.pk).exists())
        self.assertTrue(InventoryTransaction.objects.filter(pk=adjustment.pk).exists())
        self.assertTrue(InventoryTransaction.objects.filter(pk=receipt.pk).exists())
        self.assertTrue(PickTicketLine.objects.filter(pk=pick_line.pk).exists())
        self.assertTrue(ReceivingLine.objects.filter(pk=receiving_line.pk).exists())

    def test_inventory_spreadsheet_import_adds_and_updates_items(self):
        self.item_a.rack = "A"
        self.item_a.section = "2"
        self.item_a.bin_location = "2"
        self.item_a.save()
        upload = self._inventory_upload_file([
            ["WH-A", "Updated Gloves", "Safety", "New desc", 15, "box", "B1", "A", "2", "2", 4, "BCA-NEW", "QRA-NEW", "Yes"],
            ["WH-C", "Harness", "Fall", "Harness desc", 7, "each", "B2", "B", "1", "1", 2, "BCC", "QRC", "Yes"],
        ])

        response = self.client.post(reverse("inventory_import_xlsx"), {"spreadsheet": upload})

        self.assertRedirects(response, reverse("inventory_list"))
        self.item_a.refresh_from_db()
        self.assertEqual(self.item_a.name, "New desc")
        self.assertEqual(self.item_a.description, "New desc")
        self.assertEqual(self.item_a.quantity_on_hand, 15)
        self.assertEqual(self.item_a.unit, "box")
        self.assertEqual(self.item_a.rack, "A")
        self.assertEqual(self.item_a.section, "02")
        self.assertEqual(self.item_a.bin_location, "02")
        self.assertEqual(self.item_a.barcode_value, "BCA-NEW")
        new_item = InventoryItem.objects.get(part_number="WH-C")
        self.assertEqual(new_item.name, "Harness desc")
        self.assertEqual(new_item.description, "Harness desc")
        self.assertEqual(new_item.quantity_on_hand, 7)
        self.assertEqual(new_item.low_stock_threshold, 2)
        self.assertTrue(new_item.active)
        self.assertTrue(InventoryTransaction.objects.filter(item=self.item_a, transaction_type=InventoryTransaction.TransactionType.IMPORT, quantity_delta=5).exists())
        self.assertTrue(InventoryTransaction.objects.filter(item=new_item, transaction_type=InventoryTransaction.TransactionType.IMPORT, quantity_delta=7).exists())

    def test_bulk_edit_page_loads_selected_items(self):
        response = self.client.get(reverse("bulk_adjust"), {"items": [self.item_a.pk, self.item_b.pk]})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Save Changes")
        self.assertContains(response, "Gloves")
        self.assertContains(response, "Helmet")

    def test_bulk_edit_selection_posts_single_selected_items_field(self):
        response = self.client.post(reverse("bulk_adjust"), {"selected_items": f"{self.item_a.pk},{self.item_b.pk}"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Save Changes")
        self.assertContains(response, "Gloves")
        self.assertContains(response, "Helmet")

    def test_receiving_active_ticket_prefills_po_when_adding_items(self):
        ticket = ReceivingTicket.objects.create(po_number="PO-CARRY", vendor="Graybar", created_by=self.user)
        session = self.client.session
        session["receiving_ticket_id"] = ticket.pk
        session.save()

        response = self.client.get(reverse("receiving"))
        self.assertContains(response, "PO-CARRY")
        self.assertContains(response, "Graybar")
        self.assertContains(response, 'value="PO-CARRY"', html=False)

    def test_qr_payload_tracks_current_quantity_and_scans_by_part(self):
        self.item_a.quantity_on_hand = 22
        self.item_a.save(update_fields=["quantity_on_hand"])
        self.assertEqual(self.item_a.current_qr_code_value, "ITEM:WH-A|QTY:22")
        response = self.client.get(reverse("scan_lookup"), {"code": self.item_a.current_qr_code_value})
        self.assertRedirects(response, reverse("item_detail", args=[self.item_a.pk]))

    def test_bulk_edit_saves_selected_item_changes(self):
        response = self.client.post(reverse("bulk_adjust"), {
            "item_ids": [str(self.item_a.pk), str(self.item_b.pk)],
            f"item_{self.item_a.pk}_id": str(self.item_a.pk),
            f"item_{self.item_a.pk}_name": "Cut Gloves",
            f"item_{self.item_a.pk}_category": "Tools",
            f"item_{self.item_a.pk}_part_number": self.item_a.part_number,
            f"item_{self.item_a.pk}_quantity_on_hand": "15",
            f"item_{self.item_a.pk}_building_room": "B2",
            f"item_{self.item_a.pk}_rack": "A",
            f"item_{self.item_a.pk}_section": "1",
            f"item_{self.item_a.pk}_bin_location": "1",
            f"item_{self.item_a.pk}_low_stock_threshold": "3",
            f"item_{self.item_b.pk}_id": str(self.item_b.pk),
            f"item_{self.item_b.pk}_name": self.item_b.name,
            f"item_{self.item_b.pk}_category": self.item_b.category,
            f"item_{self.item_b.pk}_part_number": self.item_b.part_number,
            f"item_{self.item_b.pk}_quantity_on_hand": str(self.item_b.quantity_on_hand),
            f"item_{self.item_b.pk}_building_room": self.item_b.building_room,
            f"item_{self.item_b.pk}_bin_location": self.item_b.bin_location,
            f"item_{self.item_b.pk}_low_stock_threshold": str(self.item_b.low_stock_threshold),
        })
        self.assertRedirects(response, reverse("inventory_list"))
        self.item_a.refresh_from_db()
        self.assertEqual(self.item_a.name, "Cut Gloves")
        self.assertEqual(self.item_a.category, "Tools")
        self.assertEqual(self.item_a.quantity_on_hand, 15)
        self.assertEqual(self.item_a.building_room, "B2")
        self.assertEqual(self.item_a.rack, "A")
        self.assertEqual(self.item_a.section, "01")
        self.assertEqual(self.item_a.bin_location, "01")
        self.assertEqual(self.item_a.low_stock_threshold, 3)

    def test_create_user_form_fields_are_fillable_and_saves_names(self):
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save()
        response = self.client.get(reverse("user_create"))
        self.assertContains(response, 'name="first_name"')
        self.assertContains(response, 'name="last_name"')
        self.assertContains(response, 'name="email"')
        response = self.client.post(reverse("user_create"), {
            "username": "newtech",
            "first_name": "New",
            "last_name": "Tech",
            "email": "newtech@example.com",
            "password1": "StrongPass12345!",
            "password2": "StrongPass12345!",
        })
        self.assertRedirects(response, reverse("user_management"))
        new_user = User.objects.get(username="newtech")
        self.assertEqual(new_user.get_full_name(), "New Tech")
        self.assertEqual(new_user.email, "newtech@example.com")

    def test_main_navigation_is_combined_and_camera_emoji_links_to_scanner(self):
        response = self.client.get(reverse("inventory_list"))
        self.assertContains(response, ">Inventory<", html=False)
        self.assertNotContains(response, ">Bulk Adjust<", html=False)
        self.assertContains(response, ">Tickets<", html=False)
        self.assertNotContains(response, ">New Ticket<", html=False)
        self.assertContains(response, ">Receiving<", html=False)
        self.assertNotContains(response, ">Receiving Log<", html=False)
        self.assertContains(response, "📷")
        self.assertNotContains(response, "Phone Scanner")

    def test_scan_lookup_redirects_to_item_action_page(self):
        response = self.client.get(reverse("scan_lookup"), {"code": "BCA"})
        self.assertRedirects(response, reverse("item_detail", args=[self.item_a.pk]))

    def test_item_detail_has_full_item_action_suite(self):
        response = self.client.get(reverse("item_detail", args=[self.item_a.pk]))
        self.assertContains(response, "Start Pick Ticket")
        self.assertContains(response, "Receive Stock")
        self.assertContains(response, "Inventory Level")
        self.assertContains(response, "Transaction History")

    def test_inventory_delete_removes_unreferenced_item(self):
        resp = self.client.post(reverse("inventory_delete", args=[self.item_a.pk]))
        self.assertRedirects(resp, reverse("inventory_list"))
        self.assertFalse(InventoryItem.objects.filter(pk=self.item_a.pk).exists())

    def test_inventory_delete_blocks_item_with_ticket_references(self):
        ticket = PickTicket.objects.create(created_by=self.user)
        PickTicketLine.objects.create(ticket=ticket, item=self.item_a, quantity=1)
        resp = self.client.post(reverse("inventory_delete", args=[self.item_a.pk]))
        self.assertRedirects(resp, reverse("inventory_list"))
        self.assertTrue(InventoryItem.objects.filter(pk=self.item_a.pk).exists())

    # ------------------------------------------------------------------ #
    #  Location QR tests
    # ------------------------------------------------------------------ #

    def test_location_list_loads_when_no_locations(self):
        response = self.client.get(reverse("location_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Storage Locations")
        self.assertContains(response, "No locations found")

    def test_location_list_shows_populated_locations(self):
        self.item_a.rack = "A"
        self.item_a.section = "1"
        self.item_a.bin_location = "2"
        self.item_a.save()
        response = self.client.get(reverse("location_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A-01-02")
        self.assertContains(response, "1 item")
        self.assertContains(response, "https://bbx.rplwms.com/locations/A-01-02/")

    def test_location_detail_shows_items_at_location(self):
        self.item_a.rack = "B"
        self.item_a.section = "11"
        self.item_a.bin_location = "2"
        self.item_a.save()
        response = self.client.get(reverse("location_detail", kwargs={"location_key": "B-11-02"}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "B-11-02")
        self.assertContains(response, "WH-A")
        self.assertContains(response, "Gloves")
        self.assertContains(response, "LOC:B-11-02")

    def test_location_detail_returns_redirect_for_bad_key(self):
        response = self.client.get(reverse("location_detail", kwargs={"location_key": "INVALIDZONK"}))
        self.assertRedirects(response, reverse("location_list"))

    def test_location_detail_rejects_old_location_scheme(self):
        response = self.client.get(reverse("location_detail", kwargs={"location_key": "R1-01-1L"}))
        self.assertRedirects(response, reverse("location_list"))

    def test_lead_can_add_item_to_location(self):
        """Superuser (which this test user is) should see add and be able to POST."""
        self.item_a.rack = "A"
        self.item_a.section = "1"
        self.item_a.bin_location = "3"
        self.item_a.save()
        response = self.client.post(
            reverse("location_add_item", kwargs={"location_key": "A-01-03"}),
            {"item_id": self.item_b.pk},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A-01-03")
        self.item_b.refresh_from_db()
        self.assertEqual(self.item_b.rack, "A")
        self.assertEqual(self.item_b.section, "01")
        self.assertEqual(self.item_b.bin_location, "03")

    def test_lead_can_remove_item_from_location(self):
        self.item_a.rack = "A"
        self.item_a.section = "1"
        self.item_a.bin_location = "3"
        self.item_a.save()
        response = self.client.post(
            reverse("location_remove_item", kwargs={"location_key": "A-01-03", "item_pk": self.item_a.pk}),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A-01-03")
        self.item_a.refresh_from_db()
        self.assertEqual(self.item_a.rack, "")
        self.assertEqual(self.item_a.section, "")
        self.assertEqual(self.item_a.bin_location, "")

    def test_non_lead_cannot_add_item_to_location(self):
        """Material Handler can view locations but cannot add/remove items."""
        self.client.logout()
        handler = User.objects.create_user(username="handler", password="pw")
        group = Group.objects.create(name="Material Handler")
        group.permissions.add(Permission.objects.get(codename="view_inventoryitem"))
        handler.groups.add(group)
        self.client.login(username="handler", password="pw")
        self.item_a.rack = "A"
        self.item_a.section = "1"
        self.item_a.bin_location = "3"
        self.item_a.save()
        response = self.client.get(reverse("location_add_item", kwargs={"location_key": "A-01-03"}))
        self.assertNotEqual(response.status_code, 200)
        response2 = self.client.get(reverse("location_detail", kwargs={"location_key": "A-01-03"}))
        self.assertNotContains(response2, "Add Item")

    def test_scan_lookup_api_detects_location_qr(self):
        self.item_a.rack = "A"
        self.item_a.section = "1"
        self.item_a.bin_location = "3"
        self.item_a.save()
        response = self.client.get(reverse("scan_lookup_api"), {"code": "LOC:A-01-03"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["found"])
        self.assertTrue(data["is_location"])
        self.assertEqual(data["location_key"], "A-01-03")

    def test_scan_lookup_redirects_to_location_for_loc_qr(self):
        self.item_a.rack = "A"
        self.item_a.section = "1"
        self.item_a.bin_location = "3"
        self.item_a.save()
        response = self.client.get(reverse("scan_lookup"), {"code": "LOC:A-01-03"})
        self.assertRedirects(response, reverse("location_detail", kwargs={"location_key": "A-01-03"}))

    def test_qr_codes_shows_all_480_locations(self):
        self.item_a.rack = "A"
        self.item_a.section = "1"
        self.item_a.bin_location = "3"
        self.item_a.save()
        response = self.client.get(reverse("qr_codes"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Location QR Codes")
        self.assertContains(response, "A-01-03")
        self.assertContains(response, "480 total")
        self.assertContains(response, "D-20-06")
