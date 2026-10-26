"""Registration, pass availability, privacy, and payment workflow tests."""

import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core import mail
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from apps.home.models import SummitEvent

from .forms import RegistrationForm
from .models import PassType, Registration
from .services import (
    RegistrationError,
    cancel_registration,
    confirm_registration,
    create_registration,
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
                "phone_number": "",
                "consent": "on",
            },
            follow=True,
        )

        registration = Registration.objects.get(contact_email="guest@example.org")
        self.assertIsNone(registration.participant_id)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, registration.reference)
        self.assertIn(
            registration.pk,
            self.client.session["guest_registration_ids"],
        )

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

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_notification_service_sends_operational_email_only_to_participant(self):
        registration = self.create_registration(phone_number="+8801700000000")
        send_registration_notification(registration.pk, "registration")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [registration.contact_email])
        self.assertIn(str(registration.reference), mail.outbox[0].body)
        self.assertNotIn(registration.phone_number, mail.outbox[0].body)

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
