"""Tests for public information, private inquiries, and newsletter consent."""

import csv
from io import BytesIO
from io import StringIO
import smtplib
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse
from PIL import Image
from .forms import ContactInquiryForm, NewsletterForm
from .models import (
    ContactInquiry,
    FAQ,
    LegalDocument,
    NewsletterSubscription,
    PressRelease,
    PublicInformationSettings,
)


def contact_form_data(**overrides):
    data = {
        "name": "Amina Example",
        "email": "amina@example.org",
        "subject": "Summit information",
        "message": "Please share more information about the event.",
        "consent_given": "on",
        "website": "",
    }
    data.update(overrides)
    return data


def newsletter_form_data(**overrides):
    data = {
        "email": "updates@example.org",
        "consent_given": "on",
        "website": "",
    }
    data.update(overrides)
    return data


def make_png_upload(name="cover.png"):
    """Create a valid tiny image for private press-cover storage tests."""
    image = Image.new("RGB", (2, 2), color=(20, 80, 40))
    output = BytesIO()
    image.save(output, format="PNG")
    return SimpleUploadedFile(name, output.getvalue(), content_type="image/png")


class ContactFormTests(TestCase):
    def test_valid_contact_form_saves_private_new_inquiry(self):
        form = ContactInquiryForm(data=contact_form_data())
        self.assertTrue(form.is_valid(), form.errors)
        inquiry = form.save()
        self.assertEqual(inquiry.status, ContactInquiry.Status.NEW)
        self.assertTrue(inquiry.consent_given)

    def test_invalid_email_missing_consent_and_honeypot_are_rejected(self):
        for changes, expected_field in (
            ({"email": "not-an-email"}, "email"),
            ({"consent_given": ""}, "consent_given"),
            ({"website": "https://spam.example"}, "website"),
        ):
            with self.subTest(expected_field=expected_field):
                form = ContactInquiryForm(data=contact_form_data(**changes))
                self.assertFalse(form.is_valid())
                self.assertIn(expected_field, form.errors)

    def test_newsletter_form_normalizes_email_and_requires_consent(self):
        form = NewsletterForm(
            data=newsletter_form_data(email="  Updates@Example.org  ")
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["email"], "updates@example.org")
        invalid = NewsletterForm(data=newsletter_form_data(consent_given=""))
        self.assertFalse(invalid.is_valid())
        self.assertIn("consent_given", invalid.errors)


class ContactPublicViewTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_contact_page_uses_the_global_footer_and_grouped_navigation(self):
        response = self.client.get(reverse("contact:contact"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<summary>Summit</summary>")
        self.assertContains(response, "<summary>Get involved</summary>")
        self.assertContains(response, 'class="footer-links-grid"')
        self.assertContains(response, "Summit newsletter")
        self.assertContains(response, "Privacy details")

    def test_contact_page_uses_official_email_and_configured_contact_details(self):
        settings = PublicInformationSettings.objects.get(key="main")
        settings.hotline_numbers = "+880 1000 000000"
        settings.office_address = "Dhaka, Bangladesh"
        settings.facebook_url = "https://facebook.com/official-example"
        settings.save()

        response = self.client.get(reverse("contact:contact"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ysse.hrd@gmail.com")
        self.assertContains(response, "+880 1000 000000")
        self.assertContains(response, "Dhaka, Bangladesh")
        self.assertContains(response, "https://facebook.com/official-example")
        self.assertNotContains(response, "linkedin.com/")

    def test_contact_submission_is_saved_as_private_inquiry(self):
        response = self.client.post(
            reverse("contact:contact"),
            contact_form_data(),
        )
        inquiry = ContactInquiry.objects.get()
        self.assertRedirects(response, reverse("contact:contact"))
        self.assertEqual(inquiry.status, ContactInquiry.Status.NEW)
        self.assertEqual(inquiry.email, "amina@example.org")
        self.assertNotContains(
            self.client.get(reverse("contact:contact")),
            "amina@example.org",
        )

    def test_contact_rate_limit_prevents_excessive_submissions(self):
        for index in range(5):
            response = self.client.post(
                reverse("contact:contact"),
                contact_form_data(
                    email=f"person{index}@example.org",
                    subject=f"Message {index}",
                ),
            )
            self.assertEqual(response.status_code, 302)
        response = self.client.post(
            reverse("contact:contact"),
            contact_form_data(email="blocked@example.org"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ContactInquiry.objects.count(), 5)

    def test_faq_and_legal_pages_hide_unpublished_content(self):
        draft_faq = FAQ.objects.create(
            question="Draft question",
            answer="Draft answer",
            is_published=False,
        )
        published_faq = FAQ.objects.create(
            question="Published question",
            answer="Published answer",
            is_published=True,
        )
        faq_response = self.client.get(reverse("contact:faq"))
        self.assertContains(faq_response, published_faq.question)
        self.assertNotContains(faq_response, draft_faq.question)

        privacy = LegalDocument.objects.get(kind=LegalDocument.Kind.PRIVACY)
        self.assertFalse(privacy.organizer_reviewed)
        self.assertFalse(privacy.is_published)
        privacy_response = self.client.get(reverse("contact:privacy"))
        self.assertEqual(privacy_response.status_code, 200)
        self.assertNotContains(privacy_response, "Replace this draft")
        self.assertContains(privacy_response, "not yet available")

        privacy.is_published = True
        with self.assertRaises(ValidationError):
            privacy.full_clean()

    def test_published_press_release_only_is_visible_and_attachment_is_private(self):
        with tempfile.TemporaryDirectory() as media_root, tempfile.TemporaryDirectory() as private_root:
            with override_settings(MEDIA_ROOT=media_root, PRIVATE_MEDIA_ROOT=private_root):
                hidden = PressRelease.objects.create(
                    title="Hidden release",
                    slug="hidden-release",
                    content="Do not show.",
                    is_published=False,
                )
                release = PressRelease(
                    title="Public release",
                    slug="public-release",
                    content="Official public announcement.",
                    is_published=True,
                    cover_image=make_png_upload(),
                )
                release.attachment = SimpleUploadedFile(
                    "announcement.pdf",
                    b"%PDF-1.4\nYSSE press release\n%%EOF",
                    content_type="application/pdf",
                )
                release.full_clean()
                release.save()
                listing = self.client.get(reverse("contact:press-list"))
                self.assertContains(listing, release.title)
                self.assertNotContains(listing, hidden.title)
                detail = self.client.get(
                    reverse("contact:press-detail", args=(release.slug,))
                )
                attachment_url = reverse(
                    "contact:press-attachment",
                    args=(release.slug,),
                )
                self.assertContains(detail, attachment_url)
                self.assertFalse(
                    release.attachment.path.startswith(media_root)
                )
                self.assertFalse(release.cover_image.path.startswith(media_root))
                cover_url = reverse("contact:press-cover", args=(release.slug,))
                self.assertContains(detail, cover_url)
                cover_response = self.client.get(cover_url)
                self.assertEqual(cover_response.status_code, 200)
                self.assertEqual(cover_response["Content-Type"], "image/png")
                attachment_response = self.client.get(attachment_url)
                self.assertEqual(attachment_response.status_code, 200)
                self.assertEqual(
                    attachment_response["Content-Type"],
                    "application/pdf",
                )
                self.assertEqual(
                    attachment_response["X-Content-Type-Options"],
                    "nosniff",
                )
                release.is_published = False
                release.save(update_fields=("is_published",))
                self.assertEqual(self.client.get(cover_url).status_code, 404)
                self.assertEqual(
                    self.client.get(attachment_url).status_code,
                    404,
                )

    def test_invalid_press_attachments_are_rejected(self):
        release = PressRelease(
            title="Bad attachment",
            slug="bad-attachment",
            content="Content",
        )
        release.attachment = SimpleUploadedFile(
            "payload.exe",
            b"MZ not a pdf",
            content_type="application/octet-stream",
        )
        with self.assertRaises(ValidationError):
            release.full_clean()


class NewsletterWorkflowTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_subscription_requires_confirmation_and_does_not_disclose_email(self):
        response = self.client.post(
            reverse("contact:newsletter-subscribe"),
            newsletter_form_data(),
        )
        subscription = NewsletterSubscription.objects.get()
        self.assertRedirects(response, reverse("contact:newsletter-result"))
        self.assertEqual(subscription.status, NewsletterSubscription.Status.PENDING)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(str(subscription.confirmation_token), mail.outbox[0].body)
        self.assertIn(str(subscription.unsubscribe_token), mail.outbox[0].body)
        self.assertNotContains(
            self.client.get(reverse("contact:newsletter-result")),
            subscription.email,
        )

    def test_duplicate_active_subscriptions_are_normalized_and_not_resent(self):
        first = self.client.post(
            reverse("contact:newsletter-subscribe"),
            newsletter_form_data(email="Updates@Example.org"),
        )
        subscription = NewsletterSubscription.objects.get()
        self.client.get(
            reverse(
                "contact:newsletter-confirm",
                args=(subscription.confirmation_token,),
            )
        )
        response = self.client.post(
            reverse("contact:newsletter-subscribe"),
            newsletter_form_data(email="updates@example.org"),
        )
        self.assertEqual(first.status_code, 302)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(NewsletterSubscription.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        subscription.refresh_from_db()
        self.assertEqual(subscription.status, NewsletterSubscription.Status.ACTIVE)
        self.assertContains(
            self.client.get(response.url),
            "If this address can be subscribed",
        )

    def test_confirmation_and_unsubscribe_are_token_protected_and_idempotent(self):
        self.client.post(
            reverse("contact:newsletter-subscribe"),
            newsletter_form_data(),
        )
        subscription = NewsletterSubscription.objects.get()
        confirm_url = reverse(
            "contact:newsletter-confirm",
            args=(subscription.confirmation_token,),
        )
        self.client.get(confirm_url)
        self.client.get(confirm_url)
        subscription.refresh_from_db()
        self.assertEqual(subscription.status, NewsletterSubscription.Status.ACTIVE)

        unsubscribe_url = reverse(
            "contact:newsletter-unsubscribe",
            args=(subscription.unsubscribe_token,),
        )
        self.client.get(unsubscribe_url)
        subscription.refresh_from_db()
        self.assertEqual(subscription.status, NewsletterSubscription.Status.ACTIVE)
        self.client.post(unsubscribe_url)
        self.client.post(unsubscribe_url)
        subscription.refresh_from_db()
        self.assertEqual(
            subscription.status,
            NewsletterSubscription.Status.UNSUBSCRIBED,
        )
        self.assertIsNotNone(subscription.unsubscribed_at)

    def test_invalid_newsletter_data_does_not_create_subscription(self):
        self.client.post(
            reverse("contact:newsletter-subscribe"),
            newsletter_form_data(email="not-an-email"),
        )
        self.assertFalse(NewsletterSubscription.objects.exists())

    @patch("apps.contact.services.send_mail", side_effect=smtplib.SMTPException)
    def test_mail_delivery_failure_is_reported_and_keeps_pending_retry_record(
        self, send_mail_mock
    ):
        response = self.client.post(
            reverse("contact:newsletter-subscribe"),
            newsletter_form_data(),
        )
        self.assertEqual(response.status_code, 503)
        self.assertTrue(send_mail_mock.called)
        self.assertEqual(
            NewsletterSubscription.objects.get().status,
            NewsletterSubscription.Status.PENDING,
        )

    def test_newsletter_rate_limit_is_enforced(self):
        for index in range(5):
            self.client.post(
                reverse("contact:newsletter-subscribe"),
                newsletter_form_data(email=f"updates{index}@example.org"),
            )
        response = self.client.post(
            reverse("contact:newsletter-subscribe"),
            newsletter_form_data(email="blocked@example.org"),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(NewsletterSubscription.objects.count(), 5)


class ContactAdminPermissionTests(TestCase):
    def test_inquiry_and_subscriber_records_need_view_permissions(self):
        staff = get_user_model().objects.create_user(
            username="contact-reviewer",
            email="reviewer@example.org",
            password="long-test-password",
            is_staff=True,
        )
        inquiry_permission = Permission.objects.get(
            content_type__app_label="contact",
            codename="view_contactinquiry",
        )
        staff.user_permissions.add(inquiry_permission)
        self.client.force_login(staff)

        inquiries = self.client.get(
            reverse("admin:contact_contactinquiry_changelist")
        )
        subscribers = self.client.get(
            reverse("admin:contact_newslettersubscription_changelist")
        )
        self.assertEqual(inquiries.status_code, 200)
        self.assertEqual(subscribers.status_code, 403)

    def test_authorized_staff_can_export_selected_inquiries_as_csv(self):
        inquiry = ContactInquiry.objects.create(
            name="CSV Example",
            email="csv@example.org",
            subject="=HYPERLINK('https://example.org')",
            message="Please contact me about the Summit.",
            consent_given=True,
        )
        staff = get_user_model().objects.create_user(
            username="contact-exporter",
            email="exporter@example.org",
            password="test-password",
            is_staff=True,
        )
        staff.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="contact",
                codename="view_contactinquiry",
            )
        )
        self.client.force_login(staff)

        response = self.client.post(
            reverse("admin:contact_contactinquiry_changelist"),
            {
                "action": "export_selected_inquiries",
                "_selected_action": [str(inquiry.pk)],
                "index": "0",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            'attachment; filename="ysse-contact-inquiries.csv"',
            response["Content-Disposition"],
        )
        rows = list(
            csv.reader(StringIO(response.content.decode("utf-8-sig")))
        )
        self.assertEqual(rows[0][0:4], ["name", "email", "subject", "message"])
        self.assertEqual(rows[1][0], "CSV Example")
        self.assertEqual(rows[1][1], "csv@example.org")
        self.assertTrue(rows[1][2].startswith("'="))
