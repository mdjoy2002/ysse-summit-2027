"""Tests for sponsor publication, inquiry validation, file delivery, and Admin."""

from io import BytesIO
import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse
from PIL import Image

from .forms import PartnershipInquiryForm
from .models import (
    PartnershipInquiry,
    PartnershipSettings,
    Sponsor,
    SponsorCategory,
)
from .validators import validate_sponsor_website, validate_sponsorship_deck


def make_png_upload(name="sponsor.png"):
    """Return a small valid PNG upload for ImageField and upload tests."""
    image = Image.new("RGB", (2, 2), color=(20, 80, 40))
    output = BytesIO()
    image.save(output, format="PNG")
    return SimpleUploadedFile(name, output.getvalue(), content_type="image/png")


def valid_inquiry_data(**overrides):
    data = {
        "organization_name": "Example Social Enterprise",
        "contact_name": "Amina Example",
        "business_email": "amina@example.org",
        "phone_number": "",
        "interest": PartnershipInquiry.Interest.STRATEGIC,
        "message": "We would like to learn about partnership opportunities.",
        "consent_given": "on",
        "website": "",
    }
    data.update(overrides)
    return data


class IsolatedPartnerMediaTests(TestCase):
    """Keep uploaded test media out of the project media directory."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media_directory = tempfile.TemporaryDirectory()
        cls.media_settings = override_settings(MEDIA_ROOT=cls.media_directory.name)
        cls.media_settings.enable()

    @classmethod
    def tearDownClass(cls):
        cls.media_settings.disable()
        cls.media_directory.cleanup()
        super().tearDownClass()


class SponsorDirectoryTests(IsolatedPartnerMediaTests):
    """Confirm that public sponsor listings include only published content."""

    @classmethod
    def setUpTestData(cls):
        cls.category = SponsorCategory.objects.get(slug="title-sponsor")
        cls.published_sponsor = Sponsor.objects.create(
            organization_name="Published Example",
            logo=make_png_upload(),
            description="A confirmed public sponsor.",
            category=cls.category,
            website_url="https://example.org/",
            is_published=True,
        )
        cls.draft_sponsor = Sponsor.objects.create(
            organization_name="Draft Example",
            logo=make_png_upload("draft.png"),
            category=cls.category,
            is_published=False,
        )

    def test_directory_shows_published_sponsors_and_secure_website_link(self):
        response = self.client.get(reverse("partners:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.published_sponsor.organization_name)
        self.assertContains(response, 'href="https://example.org/"')
        self.assertContains(response, 'rel="noopener noreferrer"')
        self.assertNotContains(response, self.draft_sponsor.organization_name)
        self.assertContains(response, "Title Sponsor")

    def test_sponsor_logo_upload_and_optional_website_are_supported(self):
        sponsor = Sponsor.objects.create(
            organization_name="No Website Sponsor",
            logo=make_png_upload("no-website.png"),
            category=self.category,
            website_url="",
            is_published=True,
        )

        response = self.client.get(reverse("partners:index"))

        self.assertContains(response, sponsor.organization_name)
        self.assertContains(response, sponsor.logo.url)
        self.assertNotContains(response, 'href=""')

    def test_unpublished_categories_do_not_show_sponsors(self):
        self.category.is_published = False
        self.category.save()

        response = self.client.get(reverse("partners:index"))

        self.assertNotContains(response, self.published_sponsor.organization_name)


class PartnershipInquiryFormTests(TestCase):
    """Validate form fields, consent, and hidden spam honeypot on the server."""

    def test_valid_inquiry_needs_only_requested_contact_fields(self):
        form = PartnershipInquiryForm(data=valid_inquiry_data())

        self.assertTrue(form.is_valid(), form.errors)
        inquiry = form.save()
        self.assertEqual(inquiry.status, PartnershipInquiry.Status.NEW)
        self.assertTrue(inquiry.consent_given)
        self.assertEqual(inquiry.phone_number, "")

    def test_invalid_email_is_rejected(self):
        form = PartnershipInquiryForm(
            data=valid_inquiry_data(business_email="not-an-email")
        )

        self.assertFalse(form.is_valid())
        self.assertIn("business_email", form.errors)

    def test_consent_is_required(self):
        form = PartnershipInquiryForm(
            data=valid_inquiry_data(consent_given="")
        )

        self.assertFalse(form.is_valid())
        self.assertIn("consent_given", form.errors)

    def test_populated_honeypot_is_rejected(self):
        form = PartnershipInquiryForm(
            data=valid_inquiry_data(website="https://spam.example/")
        )

        self.assertFalse(form.is_valid())
        self.assertIn("website", form.errors)

    def test_interest_choice_must_be_configured(self):
        form = PartnershipInquiryForm(
            data=valid_inquiry_data(interest="not-a-valid-interest")
        )

        self.assertFalse(form.is_valid())
        self.assertIn("interest", form.errors)

    def test_message_maximum_length_is_enforced(self):
        form = PartnershipInquiryForm(
            data=valid_inquiry_data(message="x" * 3001)
        )

        self.assertFalse(form.is_valid())
        self.assertIn("message", form.errors)


class PartnershipInquiryViewTests(TestCase):
    """Verify public form handling, confirmation, and basic spam protection."""

    def setUp(self):
        cache.clear()

    def test_successful_inquiry_is_new_not_automatically_approved(self):
        response = self.client.post(
            reverse("partners:index"),
            valid_inquiry_data(),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not an approved partnership")
        inquiry = PartnershipInquiry.objects.get()
        self.assertEqual(inquiry.status, PartnershipInquiry.Status.NEW)

    def test_invalid_post_does_not_create_inquiry(self):
        response = self.client.post(
            reverse("partners:index"),
            valid_inquiry_data(business_email="bad"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter a valid email address.")
        self.assertFalse(PartnershipInquiry.objects.exists())

    def test_honeypot_post_is_rejected_without_persisting(self):
        response = self.client.post(
            reverse("partners:index"),
            valid_inquiry_data(website="https://automated.example/"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(PartnershipInquiry.objects.exists())

    def test_inquiries_are_rate_limited_per_remote_address(self):
        for _ in range(5):
            response = self.client.post(
                reverse("partners:index"),
                valid_inquiry_data(),
            )
            self.assertEqual(response.status_code, 302)

        response = self.client.post(
            reverse("partners:index"),
            valid_inquiry_data(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Too many inquiries were submitted.")
        self.assertEqual(PartnershipInquiry.objects.count(), 5)


class SponsorshipDeckTests(IsolatedPartnerMediaTests):
    """Ensure only validated, Admin-managed PDF material can be downloaded."""

    def test_pdf_validator_rejects_non_pdf_content(self):
        upload = SimpleUploadedFile(
            "deck.pdf",
            b"not a pdf",
            content_type="application/pdf",
        )

        with self.assertRaises(ValidationError):
            validate_sponsorship_deck(upload)

    def test_pdf_validator_accepts_pdf_signature_and_eof_marker(self):
        upload = SimpleUploadedFile(
            "deck.pdf",
            b"%PDF-1.4\nvalid test content\n%%EOF\n",
            content_type="application/pdf",
        )

        validate_sponsorship_deck(upload)

    def test_missing_sponsorship_deck_returns_404(self):
        response = self.client.get(reverse("partners:deck"))

        self.assertEqual(response.status_code, 404)

    def test_admin_uploaded_pdf_is_downloaded_as_attachment(self):
        settings = PartnershipSettings.objects.create(
            sponsorship_deck=SimpleUploadedFile(
                "managed-deck.pdf",
                b"%PDF-1.4\nsponsor info\n%%EOF\n",
                content_type="application/pdf",
            )
        )

        response = self.client.get(reverse("partners:deck"))
        downloaded_content = b"".join(response.streaming_content)
        response.close()
        settings.sponsorship_deck.close()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(downloaded_content.startswith(b"%PDF-"))
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Cache-Control"], "private, no-store")


class SponsorValidationTests(IsolatedPartnerMediaTests):
    def test_external_sponsor_websites_must_use_https(self):
        with self.assertRaises(ValidationError):
            validate_sponsor_website("http://example.org")

        validate_sponsor_website("https://example.org")

    def test_model_validation_rejects_http_sponsor_website(self):
        sponsor = Sponsor(
            organization_name="Invalid Website Sponsor",
            logo=make_png_upload("invalid-website.png"),
            category=SponsorCategory.objects.get(slug="gold-sponsors"),
            website_url="http://example.org",
        )

        with self.assertRaises(ValidationError) as error:
            sponsor.full_clean()

        self.assertIn("website_url", error.exception.message_dict)

    def test_model_validation_rejects_invalid_logo_content(self):
        sponsor = Sponsor(
            organization_name="Fake Image Sponsor",
            logo=SimpleUploadedFile(
                "not-an-image.png",
                b"not image bytes",
                content_type="image/png",
            ),
            category=SponsorCategory.objects.get(slug="gold-sponsors"),
        )

        with self.assertRaises(ValidationError) as error:
            sponsor.full_clean()

        self.assertIn("logo", error.exception.message_dict)


class PartnershipAdminTests(IsolatedPartnerMediaTests):
    """Confirm inquiry, sponsor, and pitch-deck Admin permission boundaries."""

    def setUp(self):
        self.sponsor = Sponsor.objects.create(
            organization_name="Admin Sponsor",
            logo=make_png_upload("admin-sponsor.png"),
            category=SponsorCategory.objects.get(slug="gold-sponsors"),
        )
        self.inquiry = PartnershipInquiry.objects.create(
            organization_name="Admin Test Organization",
            contact_name="Admin Reviewer",
            business_email="reviewer@example.org",
            phone_number="",
            interest=PartnershipInquiry.Interest.STRATEGIC,
            message="Please review our partnership inquiry.",
            consent_given=True,
        )

    def test_anonymous_user_cannot_access_partnership_admin(self):
        response = self.client.get(reverse("admin:partners_partnershipinquiry_changelist"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/ysse2014/login/", response.url)

    def test_authorized_staff_can_review_inquiries_and_manage_sponsors(self):
        user = get_user_model().objects.create_user(
            username="partner-editor",
            password="safe-test-password",
            is_staff=True,
        )
        user.user_permissions.add(
            Permission.objects.get(codename="add_partnershipsettings"),
            Permission.objects.get(codename="change_partnershipinquiry"),
            Permission.objects.get(codename="view_partnershipinquiry"),
            Permission.objects.get(codename="add_sponsor"),
            Permission.objects.get(codename="change_sponsor"),
            Permission.objects.get(codename="view_sponsor"),
            Permission.objects.get(codename="view_sponsorcategory"),
            Permission.objects.get(codename="change_partnershipsettings"),
            Permission.objects.get(codename="view_partnershipsettings"),
        )
        self.client.force_login(user)

        inquiry_response = self.client.get(
            reverse("admin:partners_partnershipinquiry_change", args=[self.inquiry.pk])
        )
        sponsor_response = self.client.get(
            reverse("admin:partners_sponsor_change", args=[self.sponsor.pk])
        )
        settings_response = self.client.get(
            reverse("admin:partners_partnershipsettings_add")
        )

        self.assertEqual(inquiry_response.status_code, 200)
        self.assertEqual(sponsor_response.status_code, 200)
        self.assertEqual(settings_response.status_code, 200)

    def test_staff_without_permissions_cannot_view_inquiries(self):
        user = get_user_model().objects.create_user(
            username="no-partner-access",
            password="safe-test-password",
            is_staff=True,
        )
        self.client.force_login(user)

        response = self.client.get(
            reverse("admin:partners_partnershipinquiry_changelist")
        )

        self.assertEqual(response.status_code, 403)

    def test_review_status_change_is_audited_without_auto_approving(self):
        user = get_user_model().objects.create_superuser(
            username="partnership-admin",
            email="admin@example.org",
            password="safe-test-password",
        )
        self.client.force_login(user)
        change_url = reverse(
            "admin:partners_partnershipinquiry_change",
            args=[self.inquiry.pk],
        )
        response = self.client.post(
            change_url,
            {
                "organization_name": self.inquiry.organization_name,
                "contact_name": self.inquiry.contact_name,
                "business_email": self.inquiry.business_email,
                "phone_number": self.inquiry.phone_number,
                "interest": self.inquiry.interest,
                "message": self.inquiry.message,
                "consent_given": "on",
                "status": PartnershipInquiry.Status.IN_REVIEW,
                "review_notes": "Reviewing the proposal.",
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.inquiry.refresh_from_db()
        self.assertEqual(self.inquiry.status, PartnershipInquiry.Status.IN_REVIEW)
        self.assertIsNotNone(self.inquiry.reviewed_at)
