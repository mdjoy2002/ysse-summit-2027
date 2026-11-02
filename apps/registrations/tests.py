"""Registration, pass availability, privacy, and payment workflow tests."""

import uuid
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.core import mail
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.home.models import SummitEvent

from .forms import RegistrationForm
from .models import (
    PaymentSettings,
    PassType,
    Registration,
    RegistrationEmailSettings,
)
from .services import (
    RegistrationError,
    cancel_registration,
    confirm_registration,
    create_registration,
    reject_registration,
    send_registration_notification,
    verify_payment,
)


class RegistrationFixture(TestCase):
    """Provide one confirmed-price pass and reusable participant accounts."""

    @classmethod
    def setUpTestData(cls):
        cls.event = SummitEvent.objects.get(key=SummitEvent.Key.MAIN)
        cls.event.registration_available = True
        cls.event.status = SummitEvent.Status.PLANNED
        cls.event.save()
        cls.pass_type = PassType.objects.get(category=PassType.Category.GENERAL)
        cls.pass_type.name = "General Delegate Pass"
        cls.pass_type.description = "Standard summit access."
        cls.pass_type.price = Decimal("1200.00")
        cls.pass_type.price_confirmed = True
        cls.pass_type.included_benefits = (
            "Standard entry\nEvent Kit\nLunch\nCertificate"
        )
        cls.pass_type.is_published = True
        cls.pass_type.is_available = True
        cls.pass_type.capacity = 5
        cls.pass_type.save()
        PaymentSettings.objects.update_or_create(
            key=PaymentSettings.Key.MAIN,
            defaults={
                "bkash_number": "01700000001",
                "nagad_number": "01700000002",
                "rocket_number": "01700000003",
            },
        )
        cls.participant = get_user_model().objects.create_user(
            username="delegate",
            email="delegate@example.com",
            password="test-password-strong",
        )

    def create_form(self, user=None, **overrides):
        participant = user or self.participant
        data = {
            "pass_type": self.pass_type.pk,
            "full_name": "Amina Delegate",
            "contact_email": participant.email,
            "phone_number": "",
            "consent": "on",
        }
        data.update(overrides)
        form = RegistrationForm(data, event=self.event)
        self.assertTrue(form.is_valid(), form.errors)
        return form

    def create_registration(self, user=None, **overrides):
        participant = user or self.participant
        return create_registration(
            participant=participant,
            form=self.create_form(participant, **overrides),
            event=self.event,
        )

    def make_staff_with_permission(self, permission_codename):
        staff = get_user_model().objects.create_user(
            username=f"staff-{permission_codename}",
            email="staff@example.com",
            password="staff-password-strong",
            is_staff=True,
        )
        permission = Permission.objects.get(
            content_type__app_label="registrations",
            codename=permission_codename,
        )
        staff.user_permissions.add(permission)
        return staff


class PassTypeTests(RegistrationFixture):
    def test_seed_passes_are_drafts_without_invented_prices(self):
        for category in (PassType.Category.MEMBER, PassType.Category.VIP):
            pass_type = PassType.objects.get(category=category)
            self.assertIsNone(pass_type.price)
            self.assertFalse(pass_type.price_confirmed)
            self.assertFalse(pass_type.is_published)
            self.assertFalse(pass_type.is_available)

    def test_unconfirmed_or_missing_prices_cannot_be_published(self):
        pass_type = PassType(
            name="Draft",
            category=PassType.Category.VIP,
            is_published=True,
            price=Decimal("100.00"),
            price_confirmed=False,
        )
        with self.assertRaises(ValidationError):
            pass_type.full_clean()

    def test_negative_prices_and_invalid_sale_windows_are_rejected(self):
        pass_type = self.pass_type
        pass_type.price = Decimal("-1.00")
        with self.assertRaises(ValidationError):
            pass_type.full_clean()

    def test_public_directory_hides_passes_until_configured(self):
        self.pass_type.is_published = False
        self.pass_type.save()
        response = self.client.get(reverse("registrations:passes"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.pass_type.name)
        self.assertContains(response, "awaiting organizer confirmation")


class RegistrationCreationTests(RegistrationFixture):
    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="summit@example.org",
    )
    def test_instantly_confirmed_free_pass_sends_one_branded_ticket_email(self):
        self.pass_type.price = Decimal("0.00")
        self.pass_type.requires_manual_approval = False
        self.pass_type.save()

        with self.captureOnCommitCallbacks(execute=True):
            registration = self.create_registration()

        self.assertEqual(registration.status, Registration.Status.CONFIRMED)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].subject,
            "Your YSSE Summit 2027 registration is confirmed",
        )
        self.assertIn("GENERAL DELEGATE PASS", mail.outbox[0].alternatives[0][0])
        self.assertIn("cid:ysse-logo", mail.outbox[0].alternatives[0][0])

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="summit@example.org",
    )
    def test_guest_can_register_without_sign_in_and_review_in_same_session(self):
        response = self.client.get(
            reverse("registrations:new"),
            {"pass": self.pass_type.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No account or sign-in is required")

        response = self.client.post(
            reverse("registrations:new"),
            {
                "pass_type": self.pass_type.pk,
                "full_name": "Guest Delegate",
                "contact_email": "guest@example.org",
                "phone_number": "+8801700000000",
                "consent": "on",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose your payment method")
        self.assertContains(response, "Complete your registration")
        self.assertFalse(
            Registration.objects.filter(contact_email="guest@example.org").exists()
        )
        self.assertEqual(mail.outbox, [])
        self.assertNotIn("guest_registration_ids", self.client.session)

        payment_method_url = response.request["PATH_INFO"]
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                payment_method_url,
                {"method": "bkash"},
                follow=True,
            )

        registration = Registration.objects.get(contact_email="guest@example.org")
        self.assertIsNone(registration.participant_id)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, registration.reference)
        self.assertContains(response, "Submit payment details")
        self.assertEqual(
            registration.manual_payment_method,
            Registration.ManualPaymentMethod.BKASH,
        )
        self.assertIn(
            registration.pk,
            self.client.session["guest_registration_ids"],
        )
        self.assertEqual(len(mail.outbox), 1)
        receipt = mail.outbox[0]
        self.assertEqual(receipt.to, ["guest@example.org"])
        self.assertIn(str(registration.reference), receipt.body)
        self.assertIn(registration.full_name, receipt.body)
        self.assertIn(registration.contact_email, receipt.body)
        self.assertIn(registration.phone_number, receipt.body)
        self.assertIn(registration.pass_name_snapshot, receipt.body)
        html_body, content_type = receipt.alternatives[0]
        self.assertEqual(content_type, "text/html")
        self.assertIn(str(registration.reference), html_body)
        self.assertIn(registration.full_name, html_body)
        self.assertIn(registration.contact_email, html_body)
        self.assertIn(registration.phone_number, html_body)

        other_browser = self.client_class()
        self.assertEqual(
            other_browser.get(
                reverse("registrations:detail", args=(registration.reference,))
            ).status_code,
            404,
        )

    def test_guest_duplicate_registration_is_rejected_by_email_and_event(self):
        form = RegistrationForm(
            {
                "pass_type": self.pass_type.pk,
                "full_name": "Guest Delegate",
                "contact_email": "guest@example.org",
                "phone_number": "",
                "consent": "on",
            },
            event=self.event,
        )
        self.assertTrue(form.is_valid(), form.errors)
        create_registration(participant=None, form=form, event=self.event)

        with self.assertRaises(RegistrationError):
            create_registration(participant=None, form=form, event=self.event)

    def test_paid_registration_is_not_saved_until_a_payment_method_is_submitted(self):
        registration_response = self.client.post(
            reverse("registrations:new"),
            {
                "pass_type": self.pass_type.pk,
                "full_name": "Draft Delegate",
                "contact_email": "draft@example.org",
                "phone_number": "",
                "consent": "on",
            },
            follow=True,
        )
        payment_method_url = registration_response.request["PATH_INFO"]

        self.assertEqual(registration_response.status_code, 200)
        self.assertFalse(
            Registration.objects.filter(contact_email="draft@example.org").exists()
        )

        invalid_response = self.client.post(
            payment_method_url,
            {"method": "unconfigured"},
        )
        self.assertEqual(invalid_response.status_code, 400)
        self.assertFalse(
            Registration.objects.filter(contact_email="draft@example.org").exists()
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                payment_method_url,
                {"method": Registration.ManualPaymentMethod.NAGAD},
            )

        self.assertEqual(response.status_code, 302)
        registration = Registration.objects.get(contact_email="draft@example.org")
        self.assertEqual(
            registration.manual_payment_method,
            Registration.ManualPaymentMethod.NAGAD,
        )

        self.assertEqual(
            self.client.post(
                payment_method_url,
                {"method": Registration.ManualPaymentMethod.NAGAD},
            ).status_code,
            404,
        )
        self.assertEqual(
            Registration.objects.filter(contact_email="draft@example.org").count(),
            1,
        )

    def test_valid_registration_is_pending_without_payment_gateway(self):
        registration = self.create_registration()
        self.assertEqual(registration.status, Registration.Status.PENDING)
        self.assertEqual(registration.payment_status, Registration.PaymentStatus.PENDING)
        self.assertEqual(registration.amount_due, Decimal("1200.00"))
        self.assertIsNone(registration.ticket_reference)
        self.assertEqual(registration.pass_name_snapshot, self.pass_type.name)

    def test_invalid_email_and_missing_consent_are_rejected(self):
        form = RegistrationForm(
            {
                "pass_type": self.pass_type.pk,
                "full_name": "Test Delegate",
                "contact_email": "not-an-email",
                "phone_number": "",
            },
            event=self.event,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("contact_email", form.errors)
        self.assertIn("consent", form.errors)

    def test_one_registration_per_user_and_event_is_enforced(self):
        self.create_registration()
        with self.assertRaises(RegistrationError):
            self.create_registration()
        self.assertEqual(
            Registration.objects.filter(
                event=self.event, participant=self.participant
            ).count(),
            1,
        )

    def test_database_constraint_prevents_duplicate_registrations(self):
        self.create_registration()
        duplicate = Registration(
            event=self.event,
            participant=self.participant,
            pass_type=self.pass_type,
            full_name="Duplicate",
            contact_email="delegate@example.com",
            pass_name_snapshot=self.pass_type.name,
            amount_due=self.pass_type.price,
        )
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_registration_closed_and_sold_out_passes_are_rejected(self):
        self.event.registration_available = False
        self.event.save()
        form = RegistrationForm(
            {
                "pass_type": self.pass_type.pk,
                "full_name": "Amina Delegate",
                "contact_email": self.participant.email,
                "consent": "on",
            },
            event=self.event,
        )
        self.assertFalse(form.is_valid())

        self.event.registration_available = True
        self.event.save()
        self.pass_type.capacity = 1
        self.pass_type.save()
        other = get_user_model().objects.create_user(
            username="capacity-delegate",
            email="capacity@example.com",
            password="capacity-password-strong",
        )
        form = self.create_form(other)
        self.create_registration()
        with self.assertRaises(RegistrationError):
            create_registration(
                participant=other,
                form=form,
                event=self.event,
            )

    def test_pass_availability_and_capacity_are_rechecked_on_submit(self):
        self.pass_type.is_available = False
        self.pass_type.save()
        form = RegistrationForm(
            {
                "pass_type": self.pass_type.pk,
                "full_name": "Amina Delegate",
                "contact_email": self.participant.email,
                "consent": "on",
            },
            event=self.event,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("pass_type", form.errors)

    def test_zero_price_pass_confirms_and_issues_ticket_without_payment(self):
        free_pass = self.pass_type
        free_pass.name = "No-cost pass"
        free_pass.price = Decimal("0.00")
        free_pass.price_confirmed = True
        free_pass.is_published = True
        free_pass.is_available = True
        free_pass.save()
        user = get_user_model().objects.create_user(
            username="free-delegate",
            email="free@example.com",
            password="test-password-strong",
        )
        form = RegistrationForm(
            {
                "pass_type": free_pass.pk,
                "full_name": "Free Delegate",
                "contact_email": user.email,
                "consent": "on",
            },
            event=self.event,
        )
        self.assertTrue(form.is_valid(), form.errors)
        registration = create_registration(
            participant=user, form=form, event=self.event
        )
        self.assertEqual(registration.status, Registration.Status.CONFIRMED)
        self.assertEqual(
            registration.payment_status,
            Registration.PaymentStatus.NOT_REQUIRED,
        )
        self.assertIsNotNone(registration.ticket_reference)
        self.assertTrue(registration.ticket_is_valid)


class RegistrationPermissionAndTicketTests(RegistrationFixture):
    def test_registration_references_include_name_prefix_and_are_unique(self):
        first = self.create_registration()
        self.assertRegex(first.reference, r"^AMIN-[A-Z0-9]{6}$")
        self.assertEqual(
            Registration.objects.filter(reference=first.reference).count(), 1
        )
        self.assertNotIn("@", first.reference)

    def test_name_based_registration_references_are_unique_between_participants(self):
        first = self.create_registration()
        another_participant = get_user_model().objects.create_user(
            username="second-delegate",
            email="second-delegate@example.com",
            password="test-password",
        )
        second = self.create_registration(
            user=another_participant,
            full_name="Amina Delegate",
        )

        self.assertTrue(first.reference.startswith("AMIN-"))
        self.assertTrue(second.reference.startswith("AMIN-"))
        self.assertNotEqual(first.reference, second.reference)
        self.assertEqual(
            Registration.objects.filter(
                reference__in=(first.reference, second.reference)
            ).count(),
            2,
        )

    def test_non_ascii_names_use_safe_reference_prefix(self):
        participant = get_user_model().objects.create_user(
            username="bangla-name-delegate",
            email="bangla-delegate@example.com",
            password="test-password",
        )
        registration = self.create_registration(
            user=participant,
            full_name="বাংলা প্রতিনিধি",
        )

        self.assertRegex(registration.reference, r"^REG-[A-Z0-9]{6}$")

    def test_participant_history_is_login_protected_and_scoped(self):
        response = self.client.get(reverse("registrations:history"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])
        self.client.force_login(self.participant)
        self.create_registration()
        response = self.client.get(reverse("registrations:history"))
        self.assertContains(response, "General Delegate Pass")
        self.assertContains(response, "My registrations")

    def test_registration_tracking_requires_matching_reference_and_email(self):
        registration = self.create_registration(phone_number="+880 1700-000000")
        track_url = reverse("registrations:track")

        page = self.client.get(track_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Track registration")
        self.assertContains(page, "Registration ID")

        found = self.client.post(
            track_url,
            {
                "reference": registration.reference.lower(),
                "email": registration.contact_email.upper(),
            },
        )
        self.assertEqual(found.status_code, 200)
        self.assertContains(found, registration.reference)
        self.assertContains(found, "Payment status:")
        self.assertContains(found, "Pending")

        found_by_phone = self.client.post(
            track_url,
            {
                "reference": registration.reference,
                "email": "8801700000000",
            },
        )
        self.assertContains(found_by_phone, registration.reference)

        not_found = self.client.post(
            track_url,
            {
                "reference": registration.reference,
                "email": "wrong@example.com",
            },
        )
        self.assertContains(not_found, "We could not find a registration")


    def test_another_participant_cannot_open_registration_or_ticket(self):
        registration = self.create_registration()
        other = get_user_model().objects.create_user(
            username="other",
            email="other@example.com",
            password="other-password-strong",
        )
        self.client.force_login(other)
        response = self.client.get(
            reverse("registrations:detail", args=(registration.reference,))
        )
        self.assertEqual(response.status_code, 404)

    def test_owner_can_view_private_registration_reference(self):
        registration = self.create_registration()
        self.client.force_login(self.participant)
        response = self.client.get(
            reverse("registrations:detail", args=(registration.reference,))
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, str(registration.reference))
        self.assertContains(response, "Payment: <strong>Pending</strong>")

    def test_ticket_is_issued_after_trusted_staff_payment_verification(self):
        registration = self.create_registration()
        staff = self.make_staff_with_permission("reconcile_registration_payments")
        paid = verify_payment(
            registration_id=registration.pk,
            new_status=Registration.PaymentStatus.PAID,
            actor=staff,
            audit_note="Verified against organizer payment ledger REF-123.",
        )
        self.assertEqual(paid.status, Registration.Status.CONFIRMED)
        self.assertEqual(paid.payment_status, Registration.PaymentStatus.PAID)
        self.assertIsNotNone(paid.ticket_reference)
        self.assertTrue(paid.ticket_is_valid)

    def test_ticket_access_is_owner_only_and_printable(self):
        registration = self.create_registration()
        staff = self.make_staff_with_permission("reconcile_registration_payments")
        registration = verify_payment(
            registration_id=registration.pk,
            new_status=Registration.PaymentStatus.PAID,
            actor=staff,
            audit_note="Payment matched in organizer records.",
        )
        other = get_user_model().objects.create_user(
            username="ticket-other",
            email="ticket-other@example.com",
            password="other-password-strong",
        )
        self.client.force_login(other)
        ticket_url = reverse(
            "registrations:ticket", args=(registration.ticket_reference,)
        )
        self.assertEqual(self.client.get(ticket_url).status_code, 404)
        self.client.force_login(self.participant)
        response = self.client.get(ticket_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ticket reference")
        self.assertContains(response, "Print ticket")
        self.assertNotContains(response, registration.contact_email)

    def test_entry_ticket_verification_requires_explicit_staff_permission(self):
        registration = self.create_registration()
        staff = self.make_staff_with_permission("reconcile_registration_payments")
        registration = verify_payment(
            registration_id=registration.pk,
            new_status=Registration.PaymentStatus.PAID,
            actor=staff,
            audit_note="Payment matched in organizer records.",
        )
        self.client.force_login(staff)
        response = self.client.get(
            reverse(
                "registrations:verify-ticket",
                args=(registration.ticket_reference,),
            )
        )
        self.assertEqual(response.status_code, 403)
        entry_staff = self.make_staff_with_permission("verify_tickets_at_entry")
        self.client.force_login(entry_staff)
        response = self.client.get(
            reverse(
                "registrations:verify-ticket",
                args=(registration.ticket_reference,),
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ticket valid")


class RegistrationStatusTransitionTests(RegistrationFixture):
    def test_payment_transition_requires_staff_permission_and_audit_note(self):
        registration = self.create_registration()
        participant = self.participant
        with self.assertRaises(PermissionError):
            verify_payment(
                registration_id=registration.pk,
                new_status=Registration.PaymentStatus.PAID,
                actor=participant,
                audit_note="No.",
            )
        staff = self.make_staff_with_permission("reconcile_registration_payments")
        with self.assertRaises(RegistrationError):
            verify_payment(
                registration_id=registration.pk,
                new_status=Registration.PaymentStatus.PAID,
                actor=staff,
                audit_note="",
            )
        payment_operator = self.make_staff_with_permission(
            "verify_registration_payments"
        )
        with self.assertRaises(PermissionError):
            verify_payment(
                registration_id=registration.pk,
                new_status=Registration.PaymentStatus.PAID,
                actor=payment_operator,
                audit_note="A basic payment permission cannot reconcile to paid.",
            )

    def test_invalid_payment_transitions_are_rejected(self):
        registration = self.create_registration()
        staff = self.make_staff_with_permission("reconcile_registration_payments")
        staff.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="registrations",
                codename="verify_registration_payments",
            )
        )
        with self.assertRaises(RegistrationError):
            verify_payment(
                registration_id=registration.pk,
                new_status=Registration.PaymentStatus.REFUNDED,
                actor=staff,
                audit_note="Refund.",
            )

    def test_refund_cancels_ticket_validity_and_records_status(self):
        registration = self.create_registration()
        staff = self.make_staff_with_permission("reconcile_registration_payments")
        staff.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="registrations",
                codename="verify_registration_payments",
            )
        )
        registration = verify_payment(
            registration_id=registration.pk,
            new_status=Registration.PaymentStatus.PAID,
            actor=staff,
            audit_note="Verified from bank settlement record.",
        )
        ticket_reference = registration.ticket_reference
        refunded = verify_payment(
            registration_id=registration.pk,
            new_status=Registration.PaymentStatus.REFUNDED,
            actor=staff,
            audit_note="Refund transaction REF-REFUND-8 completed.",
        )
        self.assertEqual(refunded.status, Registration.Status.CANCELLED)
        self.assertEqual(refunded.payment_status, Registration.PaymentStatus.REFUNDED)
        self.assertFalse(refunded.ticket_is_valid)
        self.assertEqual(refunded.ticket_reference, ticket_reference)

    def test_manual_approval_is_required_before_ticket_creation(self):
        self.pass_type.requires_manual_approval = True
        self.pass_type.save()
        registration = self.create_registration()
        staff = self.make_staff_with_permission("reconcile_registration_payments")
        registration = verify_payment(
            registration_id=registration.pk,
            new_status=Registration.PaymentStatus.PAID,
            actor=staff,
            audit_note="Payment confirmed by finance.",
        )
        self.assertEqual(registration.status, Registration.Status.PENDING)
        self.assertIsNone(registration.ticket_reference)
        manager = self.make_staff_with_permission("manage_registration_status")
        registration = confirm_registration(
            registration_id=registration.pk,
            actor=manager,
            audit_note="Approval reviewed against eligibility policy.",
        )
        self.assertEqual(registration.status, Registration.Status.CONFIRMED)
        self.assertIsNotNone(registration.ticket_reference)

    def test_cancellation_requires_audit_note_and_does_not_imply_refund(self):
        registration = self.create_registration()
        manager = self.make_staff_with_permission("manage_registration_status")
        with self.assertRaises(RegistrationError):
            cancel_registration(
                registration_id=registration.pk,
                actor=manager,
                audit_note="",
            )
        cancelled = cancel_registration(
            registration_id=registration.pk,
            actor=manager,
            audit_note="Participant requested cancellation.",
        )
        self.assertEqual(cancelled.status, Registration.Status.CANCELLED)
        self.assertEqual(
            cancelled.payment_status,
            Registration.PaymentStatus.CANCELLED,
        )

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="summit@example.org",
    )
    def test_paid_registration_creation_sends_receipt_email(self):
        with self.captureOnCommitCallbacks(execute=True):
            registration = self.create_registration()

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [registration.contact_email])
        self.assertEqual(mail.outbox[0].subject, "Summit registration received")
        self.assertIn(registration.reference, mail.outbox[0].body)
        self.assertIn("Payment: awaiting submission.", mail.outbox[0].body)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="summit@example.org",
    )
    def test_rejection_stores_comment_and_emails_plain_text_and_html(self):
        registration = self.create_registration()
        reviewer = self.make_staff_with_permission("manage_registration_status")
        comment = "Please correct the eligibility details and register again."

        with self.captureOnCommitCallbacks(execute=True):
            rejected = reject_registration(
                registration_id=registration.pk,
                actor=reviewer,
                audit_note=comment,
            )

        self.assertEqual(rejected.status, Registration.Status.REJECTED)
        self.assertEqual(
            rejected.payment_status,
            Registration.PaymentStatus.CANCELLED,
        )
        self.assertEqual(rejected.status_audit_note, comment)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].subject,
            "Update about your YSSE Summit 2027 registration",
        )
        self.assertIn(comment, mail.outbox[0].body)
        self.assertIn(comment, mail.outbox[0].alternatives[0][0])

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="summit@example.org",
    )
    def test_registration_notification_sends_participant_summary(self):
        registration = self.create_registration(phone_number="+8801700000000")
        send_registration_notification(registration.pk, "registration")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [registration.contact_email])
        self.assertEqual(mail.outbox[0].from_email, "summit@example.org")
        self.assertIn(str(registration.reference), mail.outbox[0].body)
        self.assertIn(registration.phone_number, mail.outbox[0].body)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="summit@example.org",
    )
    def test_payment_submission_notification_recaps_transaction_id_and_registration_data(self):
        registration = self.create_registration(phone_number="+8801700000000")
        registration.manual_payment_method = Registration.ManualPaymentMethod.NAGAD
        registration.payment_transaction_id = "NAGAD-TXN-77881"
        registration.payment_proof = "registrations/payment-proofs/receipt.png"
        registration.payment_submitted_at = timezone.now()
        registration.save(
            update_fields=(
                "manual_payment_method",
                "payment_transaction_id",
                "payment_proof",
                "payment_submitted_at",
            )
        )

        send_registration_notification(registration.pk, "registration")

        self.assertEqual(mail.outbox[0].to, [registration.contact_email])
        self.assertEqual(mail.outbox[0].from_email, "summit@example.org")
        self.assertIn(registration.reference, mail.outbox[0].body)
        self.assertIn(registration.full_name, mail.outbox[0].body)
        self.assertIn(registration.contact_email, mail.outbox[0].body)
        self.assertIn(registration.phone_number, mail.outbox[0].body)
        self.assertIn("Nagad", mail.outbox[0].body)
        self.assertIn("NAGAD-TXN-77881", mail.outbox[0].body)
        self.assertIn("Payment screenshot: uploaded and received", mail.outbox[0].body)
        html_body, content_type = mail.outbox[0].alternatives[0]
        self.assertEqual(content_type, "text/html")
        self.assertIn(registration.reference, html_body)
        self.assertIn("NAGAD-TXN-77881", html_body)
        self.assertIn("Payment screenshot", html_body)
        self.assertIn(registration.contact_email, html_body)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="summit@example.org",
    )
    def test_accepting_registration_sends_confirmation_email(self):
        registration = self.create_registration()
        registration.payment_status = Registration.PaymentStatus.PAID
        registration.save(update_fields=("payment_status",))
        reviewer = self.make_staff_with_permission(
            "manage_registration_status"
        )

        with self.captureOnCommitCallbacks(execute=True):
            confirm_registration(
                registration_id=registration.pk,
                actor=reviewer,
                audit_note="Payment and participant details reviewed.",
            )

        self.assertEqual(len(mail.outbox), 1)
        confirmation = mail.outbox[0]
        self.assertEqual(confirmation.to, [registration.contact_email])
        self.assertEqual(confirmation.from_email, "summit@example.org")
        self.assertEqual(
            confirmation.subject,
            "Your YSSE Summit 2027 registration is confirmed",
        )
        self.assertIn(registration.reference, confirmation.body)
        self.assertIn("Registration status: Confirmed", confirmation.body)
        self.assertIn("Payment and participant details reviewed.", confirmation.body)
        self.assertIn(
            "Payment and participant details reviewed.",
            confirmation.alternatives[0][0],
        )
        self.assertIn("GENERAL DELEGATE PASS", confirmation.alternatives[0][0])
        self.assertIn("cid:ysse-logo", confirmation.alternatives[0][0])
        self.assertIn("cid:ysse-anniversary", confirmation.alternatives[0][0])
        message_parts = list(confirmation.message().walk())
        content_ids = {
            part.get("Content-ID")
            for part in message_parts
            if part.get("Content-ID")
        }
        self.assertEqual(
            content_ids,
            {"<ysse-logo>", "<ysse-anniversary>"},
        )

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="summit@example.org",
    )
    def test_paid_pass_confirmation_and_ticket_are_one_email(self):
        registration = self.create_registration()
        reviewer = self.make_staff_with_permission("reconcile_registration_payments")

        with self.captureOnCommitCallbacks(execute=True):
            verify_payment(
                registration_id=registration.pk,
                new_status=Registration.PaymentStatus.PAID,
                actor=reviewer,
                audit_note="Payment verified against the settlement record.",
            )

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].subject,
            "Your YSSE Summit 2027 registration is confirmed",
        )
        registration.refresh_from_db()
        self.assertIn(
            f"Ticket reference: {registration.ticket_reference}",
            mail.outbox[0].body,
        )
        self.assertIn("Your digital pass is included below.", mail.outbox[0].body)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="summit@example.org",
    )
    def test_confirmation_message_can_be_customized(self):
        email_settings = RegistrationEmailSettings.objects.get()
        email_settings.confirmation_subject = "Welcome, {full_name}!"
        email_settings.confirmation_message = (
            "Welcome to {event_title}, {full_name}. "
            "Your pass is {pass_name}; ID: {registration_id}."
        )
        email_settings.save()
        registration = self.create_registration()
        registration.payment_status = Registration.PaymentStatus.PAID
        registration.save(update_fields=("payment_status",))
        reviewer = self.make_staff_with_permission("manage_registration_status")

        with self.captureOnCommitCallbacks(execute=True):
            confirm_registration(
                registration_id=registration.pk,
                actor=reviewer,
                audit_note="Reviewed.",
            )

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Welcome, Amina Delegate!")
        self.assertIn(
            f"Welcome to {registration.event.event_title}",
            mail.outbox[0].body,
        )
        self.assertIn(
            f"Welcome to {registration.event.event_title}",
            mail.outbox[0].alternatives[0][0],
        )

    def test_registration_email_settings_reject_unknown_placeholders(self):
        email_settings = RegistrationEmailSettings.objects.get()
        email_settings.rejection_message = "Hello {full_name.__class__}"

        with self.assertRaises(ValidationError):
            email_settings.full_clean()

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="summit@example.org",
    )
    def test_rejection_email_uses_customizable_subject_and_message(self):
        email_settings = RegistrationEmailSettings.objects.get()
        email_settings.rejection_subject = "Registration update: {registration_id}"
        email_settings.rejection_message = (
            "Hello {full_name}, your {pass_name} needs attention. "
            "Organizer note: {status_comment}"
        )
        email_settings.save()
        registration = self.create_registration()
        reviewer = self.make_staff_with_permission("manage_registration_status")

        with self.captureOnCommitCallbacks(execute=True):
            reject_registration(
                registration_id=registration.pk,
                actor=reviewer,
                audit_note="Please update your eligibility details.",
            )

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].subject,
            f"Registration update: {registration.reference}",
        )
        self.assertIn("Please update your eligibility details.", mail.outbox[0].body)
        self.assertIn(
            "Please update your eligibility details.",
            mail.outbox[0].alternatives[0][0],
        )

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        PUBLIC_BASE_URL="https://summit.example.org/",
    )
    def test_ticket_email_contains_guest_safe_private_ticket_link(self):
        registration = self.create_registration()
        registration.ticket_reference = uuid.uuid4()
        registration.save(update_fields=("ticket_reference",))

        send_registration_notification(registration.pk, "ticket")

        self.assertIn(
            f"https://summit.example.org/registrations/ticket/"
            f"{registration.ticket_reference}/",
            mail.outbox[0].body,
        )


class RegistrationAdminTests(RegistrationFixture):
    def _grant_admin_record_permissions(self, staff):
        staff.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="registrations",
                codename="view_registration",
            ),
            Permission.objects.get(
                content_type__app_label="registrations",
                codename="change_registration",
            ),
        )

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="summit@example.org",
    )
    def test_admin_can_reject_from_profile_status_field_and_save(self):
        registration = self.create_registration()
        staff = self.make_staff_with_permission("manage_registration_status")
        self._grant_admin_record_permissions(staff)
        self.client.force_login(staff)
        change_url = reverse(
            "admin:registrations_registration_change",
            args=(registration.pk,),
        )
        comment = "Eligibility requirements were not met."

        page = self.client.get(change_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'name="status"')
        self.assertContains(page, 'name="status_audit_note"')
        self.assertContains(page, "Payment status:")

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                change_url,
                {
                    "status": Registration.Status.REJECTED,
                    "status_audit_note": comment,
                    "_save": "Save",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Registration rejected and participant notified.")
        registration.refresh_from_db()
        self.assertEqual(registration.status, Registration.Status.REJECTED)
        self.assertEqual(
            registration.payment_status,
            Registration.PaymentStatus.CANCELLED,
        )
        self.assertEqual(registration.status_audit_note, comment)
        self.assertEqual(registration.status_reviewed_by, staff)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [registration.contact_email])
        self.assertIn(registration.reference, mail.outbox[0].body)
        self.assertIn(comment, mail.outbox[0].body)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="summit@example.org",
    )
    def test_admin_can_update_payment_status_from_profile_and_save(self):
        registration = self.create_registration()
        staff = self.make_staff_with_permission("verify_registration_payments")
        self._grant_admin_record_permissions(staff)
        self.client.force_login(staff)
        change_url = reverse(
            "admin:registrations_registration_change",
            args=(registration.pk,),
        )
        audit_note = "Transaction checked; payment was not received."

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                change_url,
                {
                    "payment_status": Registration.PaymentStatus.FAILED,
                    "payment_audit_note": audit_note,
                    "_save": "Save",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Payment status updated and participant notified.")
        registration.refresh_from_db()
        self.assertEqual(
            registration.payment_status,
            Registration.PaymentStatus.FAILED,
        )
        self.assertEqual(registration.payment_audit_note, audit_note)
        self.assertEqual(registration.payment_reviewed_by, staff)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [registration.contact_email])
        self.assertIn(registration.reference, mail.outbox[0].body)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="summit@example.org",
    )
    def test_admin_can_confirm_and_mark_paid_in_one_profile_save(self):
        registration = self.create_registration()
        staff = self.make_staff_with_permission("manage_registration_status")
        staff.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="registrations",
                codename="reconcile_registration_payments",
            )
        )
        self._grant_admin_record_permissions(staff)
        self.client.force_login(staff)
        change_url = reverse(
            "admin:registrations_registration_change",
            args=(registration.pk,),
        )
        status_note = "Eligibility and attendee details reviewed."
        payment_note = "Mobile transaction matched the merchant record."

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                change_url,
                {
                    "status": Registration.Status.CONFIRMED,
                    "status_audit_note": status_note,
                    "payment_status": Registration.PaymentStatus.PAID,
                    "payment_audit_note": payment_note,
                    "_save": "Save",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Registration and payment status updated; participant notified.",
        )
        registration.refresh_from_db()
        self.assertEqual(registration.status, Registration.Status.CONFIRMED)
        self.assertEqual(registration.payment_status, Registration.PaymentStatus.PAID)
        self.assertEqual(registration.status_audit_note, status_note)
        self.assertEqual(registration.payment_audit_note, payment_note)
        self.assertEqual(registration.status_reviewed_by, staff)
        self.assertEqual(registration.payment_reviewed_by, staff)
        self.assertTrue(registration.ticket_is_valid)
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(
            all(message.to == [registration.contact_email] for message in mail.outbox)
        )
        self.assertTrue(
            any(registration.reference in message.body for message in mail.outbox)
        )

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="summit@example.org",
    )
    def test_admin_can_confirm_eligible_registration_from_its_profile(self):
        registration = self.create_registration()
        registration.payment_status = Registration.PaymentStatus.PAID
        registration.save(update_fields=("payment_status",))
        staff = self.make_staff_with_permission("manage_registration_status")
        staff.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="registrations",
                codename="view_registration",
            ),
            Permission.objects.get(
                content_type__app_label="registrations",
                codename="change_registration",
            ),
        )
        self.client.force_login(staff)
        change_url = reverse(
            "admin:registrations_registration_change",
            args=(registration.pk,),
        )

        page = self.client.get(change_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Confirm this registration and email the participant")
        self.assertContains(page, "Reject this registration and email the participant")
        comment = "Payment and attendee details checked individually."

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                change_url,
                {
                    "status": Registration.Status.PENDING,
                    "status_audit_note": comment,
                    "_confirm_registration": "1",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Registration confirmed and participant notified.")
        registration.refresh_from_db()
        self.assertEqual(registration.status, Registration.Status.CONFIRMED)
        self.assertEqual(registration.status_audit_note, comment)
        confirmation = next(
            message
            for message in mail.outbox
            if message.subject == "Your YSSE Summit 2027 registration is confirmed"
        )
        self.assertIn(comment, confirmation.body)

    def test_admin_can_reject_registration_with_email_comment(self):
        registration = self.create_registration()
        staff = self.make_staff_with_permission("manage_registration_status")
        staff.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="registrations",
                codename="view_registration",
            )
        )
        self.client.force_login(staff)
        comment = "This pass category is not eligible for this application."

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("admin:registrations_registration_changelist"),
                {
                    "action": "reject_selected",
                    "_selected_action": [str(registration.pk)],
                    "status_comment": comment,
                    "index": "0",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Rejected 1 registration(s).")
        registration.refresh_from_db()
        self.assertEqual(registration.status, Registration.Status.REJECTED)
        self.assertEqual(registration.status_audit_note, comment)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(comment, mail.outbox[0].body)

    def test_staff_can_configure_mobile_payment_numbers_in_django_admin(self):
        admin_user = get_user_model().objects.create_superuser(
            username="mobile-payment-admin",
            email="mobile-payment-admin@example.org",
            password="test-mobile-payment-admin-password",
        )
        self.client.force_login(admin_user)
        settings = PaymentSettings.objects.get(key=PaymentSettings.Key.MAIN)
        edit_url = reverse(
            "admin:registrations_paymentsettings_change",
            args=(settings.pk,),
        )

        page = self.client.get(edit_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Bkash number")
        response = self.client.post(
            edit_url,
            {
                "bkash_number": "01910000001",
                "nagad_number": "01910000002",
                "rocket_number": "01910000003",
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 302)
        settings.refresh_from_db()
        self.assertEqual(settings.bkash_number, "01910000001")
        self.assertEqual(settings.nagad_number, "01910000002")
        self.assertEqual(settings.rocket_number, "01910000003")

    def test_staff_can_edit_registration_decision_email_settings(self):
        admin_user = get_user_model().objects.create_superuser(
            username="registration-email-admin",
            email="registration-email-admin@example.org",
            password="test-registration-email-admin-password",
        )
        self.client.force_login(admin_user)
        email_settings = RegistrationEmailSettings.objects.get()
        edit_url = reverse(
            "admin:registrations_registrationemailsettings_change",
            args=(email_settings.pk,),
        )
        page = self.client.get(edit_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "confirmation_message")
        self.assertContains(page, "rejection_message")

        response = self.client.post(
            edit_url,
            {
                "confirmation_subject": "Welcome {full_name}",
                "confirmation_message": "You are confirmed for {event_title}.",
                "rejection_subject": "Update {registration_id}",
                "rejection_message": "Please review: {status_comment}",
                "_save": "Save",
            },
        )
        self.assertEqual(response.status_code, 302)
        email_settings.refresh_from_db()
        self.assertEqual(email_settings.confirmation_subject, "Welcome {full_name}")
        self.assertEqual(
            email_settings.rejection_message,
            "Please review: {status_comment}",
        )

    def test_admin_pass_and_registration_pages_require_permissions(self):
        user = get_user_model().objects.create_user(
            username="no-admin-permissions",
            email="no-admin@example.com",
            password="strong-admin-test-password",
            is_staff=True,
        )
        self.client.force_login(user)
        self.assertEqual(
            self.client.get(
                reverse("admin:registrations_passtype_changelist")
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                reverse("admin:registrations_registration_changelist")
            ).status_code,
            403,
        )

    def test_admin_can_manage_passes_and_review_private_registration(self):
        registration = self.create_registration()
        admin_user = get_user_model().objects.create_superuser(
            username="ticket-admin",
            email="ticket-admin@example.com",
            password="strong-admin-test-password",
        )
        self.client.force_login(admin_user)
        pass_response = self.client.get(
            reverse("admin:registrations_passtype_changelist")
        )
        registration_response = self.client.get(
            reverse(
                "admin:registrations_registration_change",
                args=(registration.pk,),
            )
        )
        self.assertEqual(pass_response.status_code, 200)
        self.assertContains(pass_response, "price_confirmed")
        self.assertEqual(registration_response.status_code, 200)
        self.assertContains(registration_response, registration.contact_email)
        self.assertContains(registration_response, "payment_audit_note")
        self.assertContains(registration_response, "payment_reviewed_at")
        self.assertContains(registration_response, "payment_reviewed_by")

    def test_payment_review_action_records_staff_and_review_timestamp(self):
        registration = self.create_registration()
        staff = self.make_staff_with_permission(
            "reconcile_registration_payments"
        )
        staff.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="registrations",
                codename="view_registration",
            )
        )
        registration.payment_audit_note = (
            "Verified against the demo settlement record."
        )
        registration.save(update_fields=("payment_audit_note",))
        self.client.force_login(staff)

        response = self.client.post(
            reverse("admin:registrations_registration_changelist"),
            {
                "action": "mark_selected_paid",
                "_selected_action": [str(registration.pk)],
                "index": "0",
            },
        )

        self.assertEqual(response.status_code, 302)
        registration.refresh_from_db()
        self.assertEqual(
            registration.payment_status,
            Registration.PaymentStatus.PAID,
        )
        self.assertEqual(registration.payment_reviewed_by, staff)
        self.assertIsNotNone(registration.payment_reviewed_at)
        self.assertTrue(registration.ticket_is_valid)

    def test_manual_payment_action_requires_complete_evidence_and_confirms_payment(self):
        registration = self.create_registration()
        incomplete_participant = get_user_model().objects.create_user(
            username="manual-payment-without-proof",
            email="manual-payment-without-proof@example.com",
            password="test-manual-payment-password",
        )
        incomplete = self.create_registration(user=incomplete_participant)
        registration.manual_payment_method = Registration.ManualPaymentMethod.BKASH
        registration.payment_transaction_id = "TXN-839102"
        registration.payment_proof = "registrations/payment-proofs/proof.png"
        registration.payment_submitted_at = timezone.now()
        registration.payment_audit_note = (
            "Matched against the bKash merchant statement."
        )
        registration.save(
            update_fields=(
                "manual_payment_method",
                "payment_transaction_id",
                "payment_proof",
                "payment_submitted_at",
                "payment_audit_note",
            )
        )
        staff = self.make_staff_with_permission(
            "reconcile_registration_payments"
        )
        staff.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="registrations",
                codename="view_registration",
            )
        )
        self.client.force_login(staff)

        response = self.client.post(
            reverse("admin:registrations_registration_changelist"),
            {
                "action": "confirm_manual_payments",
                "_selected_action": [
                    str(registration.pk),
                    str(incomplete.pk),
                ],
                "index": "0",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirmed 1 submitted mobile payment")
        self.assertContains(
            response,
            "A mobile banking method, transaction ID, screenshot, and submission time are required.",
        )
        registration.refresh_from_db()
        self.assertEqual(
            registration.payment_status,
            Registration.PaymentStatus.PAID,
        )
        self.assertEqual(registration.payment_reviewed_by, staff)
        self.assertTrue(registration.ticket_is_valid)
        incomplete.refresh_from_db()
        self.assertEqual(
            incomplete.payment_status,
            Registration.PaymentStatus.PENDING,
        )

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="summit@example.org",
    )
    def test_manual_payment_submission_and_proof_download_are_owner_protected(self):
        registration = self.create_registration()
        buffer = BytesIO()
        Image.new("RGB", (2, 2), color="white").save(buffer, format="PNG")
        proof = SimpleUploadedFile(
            "receipt.png",
            buffer.getvalue(),
            content_type="image/png",
        )
        self.client.force_login(self.participant)

        detail = self.client.get(
            reverse("registrations:detail", args=(registration.reference,))
        )
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "bKash")
        self.assertNotContains(detail, "Optional online checkout")
        self.assertNotContains(detail, "Demo available")

        choose_url = reverse(
            "registrations:payment-method",
            args=(registration.reference,),
        )
        choice_page = self.client.get(choose_url)
        self.assertContains(choice_page, "01700000001")
        self.assertContains(choice_page, "01700000002")
        self.assertContains(choice_page, "01700000003")
        self.assertContains(choice_page, 'type="radio"')
        self.assertContains(choice_page, "Amount to send")
        self.assertContains(choice_page, "Select the payment service you used")
        self.assertContains(choice_page, "Official merchant number")
        self.assertContains(choice_page, "payment-choice-step")
        content = choice_page.content.decode()
        self.assertLess(
            content.index("payment-brand-bkash"),
            content.index("payment-brand-nagad"),
        )
        self.assertLess(
            content.index("payment-brand-nagad"),
            content.index("payment-brand-rocket"),
        )
        choice_response = self.client.post(choose_url, {"method": "bkash"})
        detail_url = reverse(
            "registrations:payment-details",
            args=(registration.reference, "bkash"),
        )
        self.assertRedirects(choice_response, detail_url)
        payment_page = self.client.get(detail_url)
        self.assertContains(payment_page, "Transaction ID")
        self.assertContains(payment_page, "Payment screenshot")
        self.assertContains(payment_page, "01700000001")

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                detail_url,
                {
                    "payment_transaction_id": "TXN-839102",
                    "payment_proof": proof,
                },
            )
        self.assertRedirects(
            response,
            reverse("registrations:thank-you", args=(registration.reference,)),
        )
        thank_you = self.client.get(
            reverse("registrations:thank-you", args=(registration.reference,))
        )
        self.assertContains(thank_you, "Thank you")
        self.assertContains(thank_you, registration.reference)
        self.assertContains(thank_you, registration.contact_email)
        self.assertContains(thank_you, "payment is pending verification")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("TXN-839102", mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].from_email, "summit@example.org")
        self.assertIn("Payment screenshot: uploaded and received", mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].alternatives[0][1], "text/html")

        registration.refresh_from_db()
        self.addCleanup(lambda: registration.payment_proof.delete(save=False))
        self.assertEqual(
            registration.manual_payment_method,
            Registration.ManualPaymentMethod.BKASH,
        )
        self.assertEqual(registration.payment_transaction_id, "TXN-839102")
        self.assertTrue(registration.payment_proof)
        self.assertIsNotNone(registration.payment_submitted_at)
        self.assertEqual(registration.payment_status, Registration.PaymentStatus.PENDING)
        with self.assertRaises(ValueError):
            registration.payment_proof.url

        proof_response = self.client.get(
            reverse(
                "registrations:payment-proof",
                args=(registration.reference,),
            )
        )
        self.assertEqual(proof_response.status_code, 200)
        self.assertEqual(proof_response["Cache-Control"], "private, no-store")
        self.assertEqual(
            proof_response["X-Content-Type-Options"],
            "nosniff",
        )
        self.assertIn(b"PNG", b"".join(proof_response.streaming_content))

        other_user = get_user_model().objects.create_user(
            username="payment-proof-other-user",
            email="payment-proof-other@example.com",
            password="test-payment-proof-password",
        )
        self.client.force_login(other_user)
        self.assertEqual(
            self.client.get(
                reverse(
                    "registrations:payment-proof",
                    args=(registration.reference,),
                )
            ).status_code,
            404,
        )

    def test_manual_payment_form_rejects_non_images_and_missing_transaction_id(self):
        from .payment_forms import ManualPaymentSubmissionForm

        registration = self.create_registration()
        invalid_file = SimpleUploadedFile(
            "receipt.png",
            b"not an image",
            content_type="image/png",
        )
        form = ManualPaymentSubmissionForm(
            {
                "manual_payment_method": Registration.ManualPaymentMethod.NAGAD,
                "payment_transaction_id": "",
            },
            {"payment_proof": invalid_file},
            instance=registration,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("payment_transaction_id", form.errors)
        self.assertIn("payment_proof", form.errors)

    def test_registration_can_be_deleted_by_staff_with_delete_permission(self):
        registration = self.create_registration()
        staff = self.make_staff_with_permission("delete_registration")
        self.client.force_login(staff)

        delete_url = reverse(
            "admin:registrations_registration_delete",
            args=(registration.pk,),
        )
        self.assertEqual(self.client.get(delete_url).status_code, 200)
        response = self.client.post(delete_url, {"post": "yes"})

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            Registration.objects.filter(pk=registration.pk).exists()
        )

    def test_registration_cannot_be_deleted_without_delete_permission(self):
        registration = self.create_registration()
        staff = get_user_model().objects.create_user(
            username="registration-viewer",
            email="registration-viewer@example.com",
            password="test-password",
            is_staff=True,
        )
        staff.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="registrations",
                codename="view_registration",
            )
        )
        self.client.force_login(staff)

        response = self.client.get(
            reverse(
                "admin:registrations_registration_delete",
                args=(registration.pk,),
            )
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            Registration.objects.filter(pk=registration.pk).exists()
        )
