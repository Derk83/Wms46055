"""Tests for item image and document upload, serving, and access control."""
import io

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from inventory.models import InventoryItem, ItemImage, ItemDocument


User = get_user_model()


def _make_user(username, *, view_perm=False, change_perm=False, superuser=False):
    user = User.objects.create_user(
        username=username,
        password="testpass123",
        is_superuser=superuser,
    )
    if not superuser:
        ct = ContentType.objects.get_for_model(InventoryItem)
        if view_perm:
            user.user_permissions.add(
                Permission.objects.get(codename="view_inventoryitem", content_type=ct)
            )
        if change_perm:
            user.user_permissions.add(
                Permission.objects.get(codename="change_inventoryitem", content_type=ct)
            )
    return user


def _png_bytes():
    """Return minimal valid PNG bytes."""
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4"
        b"\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe"
        b"\xdc\xccY\xe7\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def _pdf_bytes():
    """Return minimal valid PDF bytes."""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
        b"xref\n0 4\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000111 00000 n \n"
        b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n175\n%%EOF"
    )


@override_settings(MEDIA_ROOT="/tmp/ppe-test-media")
class ItemImageDocumentAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.item = InventoryItem.objects.create(
            part_number="IMG-TEST-001",
            name="Image Test Item",
            rack="A",
            section="01",
            bin_location="01",
            quantity_on_hand=10,
        )

    def setUp(self):
        self.viewer = _make_user("viewer", view_perm=True)
        self.editor = _make_user("editor", view_perm=True, change_perm=True)
        self.no_perm = _make_user("noperm")
        self.super = _make_user("admin", superuser=True)

    def _login(self, user):
        c = Client()
        c.force_login(user)
        return c

    # --- List views ---------------------------------------------------------

    def test_images_list_requires_view_permission(self):
        c = self._login(self.no_perm)
        resp = c.get(reverse("item_images", args=[self.item.pk]))
        # Decorator redirects unauthorized users back to referrer/dashboard
        self.assertEqual(resp.status_code, 302)

    def test_images_list_allows_viewer(self):
        c = self._login(self.viewer)
        resp = c.get(reverse("item_images", args=[self.item.pk]))
        self.assertEqual(resp.status_code, 200)

    def test_documents_list_requires_view_permission(self):
        c = self._login(self.no_perm)
        resp = c.get(reverse("item_documents", args=[self.item.pk]))
        self.assertEqual(resp.status_code, 302)

    # --- Upload validation --------------------------------------------------

    def test_image_add_requires_change_permission(self):
        c = self._login(self.viewer)
        img = SimpleUploadedFile("test.png", _png_bytes(), content_type="image/png")
        resp = c.post(
            reverse("item_image_add", args=[self.item.pk]),
            {"image": img, "caption": "x"},
        )
        # Viewer lacks change_inventoryitem -> redirect
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(ItemImage.objects.count(), 0)

    def test_image_add_rejects_non_image_mime(self):
        c = self._login(self.editor)
        bad = SimpleUploadedFile("evil.pdf", _pdf_bytes(), content_type="application/pdf")
        resp = c.post(
            reverse("item_image_add", args=[self.item.pk]),
            {"image": bad},
        )
        # Should redirect after rejecting
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(ItemImage.objects.count(), 0)

    def test_image_add_accepts_valid_png(self):
        c = self._login(self.editor)
        img = SimpleUploadedFile("ok.png", _png_bytes(), content_type="image/png")
        resp = c.post(
            reverse("item_image_add", args=[self.item.pk]),
            {"image": img, "caption": "Primary view"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(ItemImage.objects.count(), 1)
        image = ItemImage.objects.get()
        self.assertEqual(image.caption, "Primary view")
        self.assertEqual(image.uploaded_by, self.editor)

    def test_document_add_rejects_non_pdf(self):
        c = self._login(self.editor)
        bad = SimpleUploadedFile("evil.png", _png_bytes(), content_type="image/png")
        resp = c.post(
            reverse("item_document_add", args=[self.item.pk]),
            {"document": bad},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(ItemDocument.objects.count(), 0)

    def test_document_add_accepts_valid_pdf(self):
        c = self._login(self.editor)
        doc = SimpleUploadedFile("spec.pdf", _pdf_bytes(), content_type="application/pdf")
        resp = c.post(
            reverse("item_document_add", args=[self.item.pk]),
            {"document": doc, "description": "Spec sheet"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(ItemDocument.objects.count(), 1)
        d = ItemDocument.objects.get()
        self.assertEqual(d.original_filename, "spec.pdf")
        self.assertEqual(d.uploaded_by, self.editor)

    # --- Serve endpoints ----------------------------------------------------

    def _create_image(self):
        c = self._login(self.editor)
        img = SimpleUploadedFile("ok.png", _png_bytes(), content_type="image/png")
        c.post(reverse("item_image_add", args=[self.item.pk]), {"image": img})
        return ItemImage.objects.get()

    def _create_document(self):
        c = self._login(self.editor)
        doc = SimpleUploadedFile("ok.pdf", _pdf_bytes(), content_type="application/pdf")
        c.post(reverse("item_document_add", args=[self.item.pk]), {"document": doc})
        return ItemDocument.objects.get()

    def test_serve_image_requires_view_permission(self):
        image = self._create_image()
        c = self._login(self.no_perm)
        resp = c.get(reverse("serve_item_image", args=[self.item.pk, image.pk]))
        # Redirects unauthorized users (no view_inventoryitem) away
        self.assertEqual(resp.status_code, 302)

    def test_serve_image_anonymous_redirects_to_login(self):
        image = self._create_image()
        c = Client()
        resp = c.get(reverse("serve_item_image", args=[self.item.pk, image.pk]))
        # Anonymous user should be redirected (302) to login, not 200
        self.assertEqual(resp.status_code, 302)

    def test_serve_image_allows_viewer(self):
        image = self._create_image()
        c = self._login(self.viewer)
        resp = c.get(reverse("serve_item_image", args=[self.item.pk, image.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/png")
        self.assertEqual(resp["X-Content-Type-Options"], "nosniff")

    def test_serve_document_requires_view_permission(self):
        doc = self._create_document()
        c = self._login(self.no_perm)
        resp = c.get(reverse("serve_item_document", args=[self.item.pk, doc.pk]))
        self.assertEqual(resp.status_code, 302)

    def test_serve_document_allows_viewer(self):
        doc = self._create_document()
        c = self._login(self.viewer)
        resp = c.get(reverse("serve_item_document", args=[self.item.pk, doc.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")

    def test_serve_image_with_wrong_item_returns_404(self):
        """Item ID/image ID mismatch must not leak the file."""
        image = self._create_image()
        other_item = InventoryItem.objects.create(
            part_number="OTHER",
            name="Other",
            rack="B",
            section="01",
            bin_location="01",
            quantity_on_hand=1,
        )
        c = self._login(self.viewer)
        resp = c.get(reverse("serve_item_image", args=[other_item.pk, image.pk]))
        self.assertEqual(resp.status_code, 404)
