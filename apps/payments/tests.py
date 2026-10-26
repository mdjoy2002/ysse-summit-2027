"""Provider-mocked payment checkout, callback, and permission tests."""

import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.home.models import SummitEvent
from apps.registrations.models import PassType, Registration
from apps.registrations.services import verify_payment
from apps.registrations.tests import RegistrationFixture

from .models import PaymentEvent, PaymentMethod, PaymentTransaction
from .providers.base import CheckoutSession, ProviderError, VerifiedPayment
from .services import (
    PaymentIntegrationError,
    _apply_verified_result,
    create_checkout,
    get_provider,
    process_provider_callback,
)


class FakeGateway:
    """Provider test double; it never makes a network request."""

    name = "fake-sandbox"
    checkout_hosts = ("checkout.example.test",)
    supported_methods = tuple(PaymentMethod.values)
    result = None
    error = None

    def __init__(self):
        self.initiate_calls = []
        self.verify_calls = []

    def initiate(self, *, transaction, callback_url, return_url, cancel_url):
        self.initiate_calls.append(
            (transaction, callback_url, return_url, cancel_url)
        )
        return CheckoutSession(
            redirect_url="https://checkout.example.test/session/opaque",
            provider_reference=f"provider-{transaction.reference}",
            expires_at=timezone.now() + timedelta(minutes=15),
        )

    def extract_transaction_reference(self, payload, headers):
        if payload.get("invalid_reference"):
            raise ProviderError("invalid_callback")
        return payload.get("reference", "")

    def verify_callback(self, *, transaction, payload, headers):
        self.verify_calls.append((transaction, payload))
        if self.error:
            raise self.error
        return self.result


def make_registration(participant):
    """Create a pending paid registration using the existing workflow model."""
    event = SummitEvent.objects.get(key=SummitEvent.Key.MAIN)
    event.registration_available = True
    event.status = SummitEvent.Status.PLANNED
    event.save()
    pass_type = PassType.objects.get(category=PassType.Category.GENERAL)
    pass_type.price = Decimal("1200.00")
    pass_type.price_confirmed = True
    pass_type.is_published = True
    pass_type.is_available = True
    pass_type.capacity = None
    pass_type.save()
    return Registration.objects.create(
        event=event,
        participant=participant,
        pass_type=pass_type,
        full_name="Payment Test Participant",
        contact_email=(
            participant.email if participant else "payment-guest@example.com"
        ),
        pass_name_snapshot=pass_type.name,
        benefits_snapshot=pass_type.included_benefits,
        amount_due=Decimal("1200.00"),
        status=Registration.Status.PENDING,
        payment_status=Registration.PaymentStatus.PENDING,
    )


class PaymentWorkflowTests(TestCase):
    """Exercise payment flows against a deterministic, network-free provider."""

    @classmethod
    def setUpTestData(cls):
        cls.participant = get_user_model().objects.create_user(
            username="payment-participant",
            email="payment@example.com",
            password="strong-payment-test-password",
        )
        cls.registration = make_registration(cls.participant)

    def setUp(self):
        settings_context = override_settings(
            PUBLIC_BASE_URL="https://summit.example.test"
        )
        settings_context.enable()
        self.addCleanup(settings_context.disable)
        self.provider = FakeGateway()
        self.registration = Registration.objects.get(pk=self.registration.pk)

    def valid_result(self, **overrides):
        data = {
            "is_verified": True,
            "status": PaymentTransaction.Status.SUCCEEDED,
            "provider_reference": "provider-settled-123",
            "amount": Decimal("1200.00"),
            "currency": "BDT",
            "event_reference": "event-123",
            "diagnostic_code": "",
        }
        data.update(overrides)
        return VerifiedPayment(**data)

    def start_checkout(self, registration=None, method=PaymentMethod.BKASH):
        return create_checkout(
            registration=registration or self.registration,
            method=method,
        )

    @override_settings(
        DEBUG=True,
        PAYMENT_PROVIDER_CLASS="apps.payments.providers.demo.DemoProvider",
        PUBLIC_BASE_URL="",
    )
    def test_development_demo_checkout_simulates_success_without_gateway(self):
        from apps.payments.services import get_supported_methods

        self.assertEqual(
            {value for value, _label in get_supported_methods()},
            set(PaymentMethod.values),
        )
        self.client.force_login(self.participant)
        start_url = reverse(
            "payments:start",
            args=(self.registration.reference,),
        )
        response = self.client.post(
            start_url,
            {"method": PaymentMethod.BKASH},
        )

        payment = PaymentTransaction.objects.get(
            registration=self.registration,
            provider="demo-sandbox",
        )
        demo_url = reverse("payments:demo-checkout", args=(payment.reference,))
        self.assertRedirects(response, demo_url)

        other = get_user_model().objects.create_user(
            username="not-demo-owner",
            email="not-demo-owner@example.com",
            password="test-password",
        )
        self.client.force_login(other)
        self.assertEqual(self.client.get(demo_url).status_code, 404)
        self.client.force_login(self.participant)

        demo_page = self.client.get(demo_url)
        self.assertContains(demo_page, "simulated checkout")
        self.assertContains(demo_page, "No bKash, Nagad, Rocket, card")

        result = self.client.post(demo_url, {"result": "success"})

        self.assertRedirects(
            result,
            reverse("payments:return", args=(payment.reference,)),
        )
        payment.refresh_from_db()
        self.registration.refresh_from_db()
        self.assertEqual(payment.status, PaymentTransaction.Status.SUCCEEDED)
        self.assertEqual(
            self.registration.payment_status,
            Registration.PaymentStatus.PAID,
        )
        self.assertTrue(self.registration.ticket_is_valid)
        self.assertIsNotNone(self.registration.payment_reviewed_at)

    @override_settings(
        DEBUG=True,
        PAYMENT_PROVIDER_CLASS="apps.payments.providers.demo.DemoProvider",
        PAYMENT_DEMO_ENABLED=True,
        PUBLIC_BASE_URL="",
    )
    def test_guest_can_complete_demo_payment_and_open_bearer_ticket(self):
        event = SummitEvent.objects.get(key=SummitEvent.Key.MAIN)
        pass_type = PassType.objects.get(category=PassType.Category.GENERAL)
        registration_response = self.client.post(
            reverse("registrations:new"),
            {
                "pass_type": pass_type.pk,
                "full_name": "Guest Payment Delegate",
                "contact_email": "guest-payment@example.com",
                "phone_number": "",
                "consent": "on",
            },
        )
        self.assertEqual(registration_response.status_code, 302)
        guest_registration = Registration.objects.get(
            event=event,
            contact_email="guest-payment@example.com",
        )

        response = self.client.post(
            reverse("payments:start", args=(guest_registration.reference,)),
            {"method": PaymentMethod.BKASH},
        )
        self.assertEqual(response.status_code, 302, response.content.decode())
        self.assertIn("/payments/demo/", response["Location"])
        payment = PaymentTransaction.objects.get(registration=guest_registration)
        demo_url = reverse("payments:demo-checkout", args=(payment.reference,))
        self.assertRedirects(response, demo_url)
        self.assertEqual(self.client.get(demo_url).status_code, 200)

        response = self.client.post(demo_url, {"result": "success"})

        self.assertRedirects(
            response,
            reverse("payments:return", args=(payment.reference,)),
        )
        guest_registration.refresh_from_db()
        self.assertTrue(guest_registration.ticket_is_valid)
        ticket_response = self.client.get(
            reverse(
                "registrations:ticket",
                args=(guest_registration.ticket_reference,),
            )
        )
        self.assertEqual(ticket_response.status_code, 200)

    @override_settings(
        DEBUG=True,
        PAYMENT_PROVIDER_CLASS="apps.payments.providers.demo.DemoProvider",
        PUBLIC_BASE_URL="",
    )
    def test_development_demo_checkout_can_simulate_failure(self):
        self.client.force_login(self.participant)
        response = self.client.post(
            reverse("payments:start", args=(self.registration.reference,)),
            {"method": PaymentMethod.NAGAD},
        )
        payment = PaymentTransaction.objects.get(provider="demo-sandbox")
        demo_url = reverse("payments:demo-checkout", args=(payment.reference,))
        self.assertRedirects(response, demo_url)

        self.client.post(demo_url, {"result": "failed"})

        payment.refresh_from_db()
        self.registration.refresh_from_db()
        self.assertEqual(payment.status, PaymentTransaction.Status.FAILED)
        self.assertEqual(
            self.registration.payment_status,
            Registration.PaymentStatus.FAILED,
        )
        self.assertFalse(self.registration.ticket_is_valid)

    @override_settings(
        PAYMENT_DEMO_ENABLED=False,
        PAYMENT_PROVIDER_CLASS="apps.payments.providers.demo.DemoProvider",
    )
    def test_demo_provider_is_rejected_when_demo_is_disabled(self):
        from django.core.exceptions import ImproperlyConfigured

        with self.assertRaises(ImproperlyConfigured):
            get_provider()

    def callback(self, payment, payload_overrides=None):
        payload = {"reference": str(payment.reference), "event": "cb-123"}
        payload.update(payload_overrides or {})
        body = json.dumps(payload).encode()
        return process_provider_callback(
            payload=payload,
            headers={"Content-Type": "application/json"},
            raw_body=body,
        )

    @patch("apps.payments.services.get_provider")
    def test_checkout_is_created_from_server_amount_and_safe_redirect(self, get_provider_mock):
        get_provider_mock.return_value = self.provider
        payment = self.start_checkout()
        self.assertEqual(payment.status, PaymentTransaction.Status.PENDING)
        self.assertEqual(payment.amount, Decimal("1200.00"))
        self.assertEqual(payment.currency, "BDT")
        self.assertEqual(payment.method, PaymentMethod.BKASH)
        self.assertEqual(payment.provider, self.provider.name)
        self.assertEqual(
            payment.provider_transaction_reference,
            f"provider-{payment.reference}",
        )
        self.assertTrue(payment.checkout_url.startswith("https://checkout.example.test/"))
        self.assertEqual(
            self.provider.initiate_calls[0][1],
            "https://summit.example.test/payments/callback/",
        )
        self.assertEqual(payment.events.count(), 2)

    @patch("apps.payments.services.get_provider")
    def test_only_one_unexpired_checkout_can_be_active_per_registration(self, get_provider_mock):
        get_provider_mock.return_value = self.provider
        payment = self.start_checkout()
        with self.assertRaises(PaymentIntegrationError) as error:
            self.start_checkout()
        self.assertEqual(error.exception.code, "payment_attempt_already_pending")
        self.assertEqual(
            PaymentTransaction.objects.filter(
                registration=self.registration,
                status=PaymentTransaction.Status.PENDING,
            ).count(),
            1,
        )

        payment.expires_at = timezone.now() - timedelta(seconds=1)
        payment.save(update_fields=("expires_at",))
        replacement = self.start_checkout()
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentTransaction.Status.EXPIRED)
        self.assertEqual(replacement.status, PaymentTransaction.Status.PENDING)

    @patch("apps.payments.services.get_provider")
    def test_database_constraints_reject_nonpositive_and_duplicate_pending_attempts(self, get_provider_mock):
        get_provider_mock.return_value = self.provider
        self.start_checkout()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PaymentTransaction.objects.create(
                    registration=self.registration,
                    provider=self.provider.name,
                    method=PaymentMethod.BKASH,
                    amount=Decimal("1200.00"),
                    currency="BDT",
                )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PaymentTransaction.objects.create(
                    registration=self.registration,
                    provider=self.provider.name,
                    method=PaymentMethod.ROCKET,
                    status=PaymentTransaction.Status.FAILED,
                    amount=Decimal("0.00"),
                    currency="BDT",
                )

    @patch("apps.payments.services.get_provider")
    def test_unconfigured_provider_fails_closed_and_advertises_no_methods(self, get_provider_mock):
        from .providers.unconfigured import UnconfiguredProvider

        get_provider_mock.return_value = UnconfiguredProvider()
        self.assertEqual(
            self.client.get(reverse("registrations:passes")).status_code,
            200,
        )
        self.assertFalse(
            __import__("apps.payments.services", fromlist=["get_supported_methods"])
            .get_supported_methods()
        )
        with self.assertRaises(PaymentIntegrationError):
            self.start_checkout()

    @patch("apps.payments.services.get_provider")
    def test_registration_detail_disables_checkout_until_provider_is_configured(
        self, get_provider_mock
    ):
        from .providers.unconfigured import UnconfiguredProvider

        get_provider_mock.return_value = UnconfiguredProvider()
        self.client.force_login(self.participant)
        response = self.client.get(
            reverse(
                "registrations:detail",
                args=(self.registration.reference,),
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Online checkout is not active yet")
        self.assertContains(response, "Choose a payment method")
        self.assertContains(response, "bKash")
        self.assertContains(response, "Nagad")
        self.assertContains(response, "Rocket")
        self.assertContains(response, "Debit or credit card")
        self.assertContains(response, "Not configured")
        self.assertNotContains(response, "Continue to secure checkout")

    @patch("apps.payments.services.get_provider")
    @override_settings(PUBLIC_BASE_URL="")
    def test_checkout_requires_canonical_https_origin(self, get_provider_mock):
        get_provider_mock.return_value = self.provider
        with self.assertRaises(PaymentIntegrationError) as error:
            self.start_checkout()
        self.assertEqual(error.exception.code, "public_base_url_not_configured")
        self.assertFalse(PaymentTransaction.objects.exists())

    @patch("apps.payments.services.get_provider")
    def test_invalid_methods_and_untrusted_checkout_redirects_are_rejected(self, get_provider_mock):
        get_provider_mock.return_value = self.provider
        with self.assertRaises(PaymentIntegrationError):
            self.start_checkout(method="unknown")

        self.provider.initiate = lambda **kwargs: CheckoutSession(
            redirect_url="https://attacker.example/collect",
            provider_reference="unsafe",
        )
        with self.assertRaises(PaymentIntegrationError):
            self.start_checkout()
        payment = PaymentTransaction.objects.latest("created_at")
        self.assertEqual(payment.status, PaymentTransaction.Status.FAILED)
        self.assertEqual(payment.diagnostic_code, "invalid_checkout_response")

    @patch("apps.payments.services.get_provider")
    def test_verified_success_records_paid_and_issues_ticket_once(self, get_provider_mock):
        get_provider_mock.return_value = self.provider
        payment = self.start_checkout()
        self.provider.result = self.valid_result()
        processed = self.callback(payment)
        self.assertEqual(processed.status, PaymentTransaction.Status.SUCCEEDED)
        self.assertIsNotNone(processed.verified_at)
        self.registration.refresh_from_db()
        self.assertEqual(
            self.registration.payment_status,
            Registration.PaymentStatus.PAID,
        )
        self.assertEqual(
            self.registration.status,
            Registration.Status.CONFIRMED,
        )
        self.assertTrue(self.registration.ticket_is_valid)

        self.callback(payment)
        self.assertEqual(
            PaymentEvent.objects.filter(
                transaction=payment,
                event_type="verified_succeeded",
            ).count(),
            1,
        )
        self.assertEqual(
            Registration.objects.get(pk=self.registration.pk).ticket_reference,
            self.registration.ticket_reference,
        )

    @patch("apps.payments.services.get_provider")
    def test_failed_verified_payment_can_be_retried_with_a_new_attempt(self, get_provider_mock):
        get_provider_mock.return_value = self.provider
        payment = self.start_checkout()
        self.provider.result = self.valid_result(
            status=PaymentTransaction.Status.FAILED,
            provider_reference="provider-failed-1",
            event_reference="event-failed-1",
        )
        self.callback(payment)
        self.registration.refresh_from_db()
        self.assertEqual(
            self.registration.payment_status,
            Registration.PaymentStatus.FAILED,
        )

        self.provider.result = None
        retry = self.start_checkout()
        self.assertNotEqual(retry.reference, payment.reference)
        self.assertEqual(retry.status, PaymentTransaction.Status.PENDING)

    @patch("apps.payments.services.get_provider")
    def test_invalid_signature_and_invalid_payload_never_confirm_registration(self, get_provider_mock):
        get_provider_mock.return_value = self.provider
        payment = self.start_checkout()
        self.provider.error = ProviderError("signature_invalid")
        with self.assertRaises(PaymentIntegrationError):
            self.callback(payment)
        with self.assertRaises(PaymentIntegrationError):
            self.callback(payment)
        self.registration.refresh_from_db()
        self.assertEqual(self.registration.payment_status, Registration.PaymentStatus.PENDING)
        self.assertFalse(self.registration.ticket_reference)
        self.assertEqual(
            PaymentEvent.objects.filter(
                transaction=payment,
                event_type="verification_rejected",
            ).count(),
            1,
        )

    @patch("apps.payments.services.get_provider")
    def test_malformed_transaction_reference_is_rejected_as_unverified_input(
        self, get_provider_mock
    ):
        get_provider_mock.return_value = self.provider
        payment = self.start_checkout()
        with self.assertRaises(PaymentIntegrationError) as error:
            self.callback(payment, {"reference": "not-a-uuid"})
        self.assertEqual(error.exception.code, "transaction_not_found")
        self.registration.refresh_from_db()
        self.assertEqual(
            self.registration.payment_status,
            Registration.PaymentStatus.PENDING,
        )

    @patch("apps.payments.services.get_provider")
    def test_amount_and_currency_mismatches_do_not_mark_paid(self, get_provider_mock):
        get_provider_mock.return_value = self.provider
        payment = self.start_checkout()
        for result in (
            self.valid_result(amount=Decimal("1.00")),
            self.valid_result(currency="USD", event_reference="event-currency"),
        ):
            with self.subTest(result=result):
                self.provider.result = result
                with self.assertRaises(PaymentIntegrationError):
                    self.callback(payment, {"event": result.event_reference})
        self.registration.refresh_from_db()
        self.assertEqual(self.registration.payment_status, Registration.PaymentStatus.PENDING)
        self.assertEqual(payment.__class__.objects.get(pk=payment.pk).status, PaymentTransaction.Status.VERIFICATION_FAILED)

    @patch("apps.payments.services.get_provider")
    def test_expired_success_requires_manual_reconciliation_and_issues_no_ticket(self, get_provider_mock):
        get_provider_mock.return_value = self.provider
        payment = self.start_checkout()
        payment.expires_at = timezone.now() - timedelta(seconds=1)
        payment.save(update_fields=("expires_at",))
        self.provider.result = self.valid_result()
        processed = self.callback(payment)
        self.assertEqual(
            processed.status,
            PaymentTransaction.Status.VERIFICATION_FAILED,
        )
        self.assertEqual(
            processed.diagnostic_code,
            "late_success_requires_reconciliation",
        )
        self.registration.refresh_from_db()
        self.assertEqual(
            self.registration.payment_status,
            Registration.PaymentStatus.PENDING,
        )
        self.assertIsNone(self.registration.ticket_reference)
        staff = get_user_model().objects.create_user(
            username="late-payment-reviewer",
            email="late-payment-reviewer@example.com",
            password="test-password",
            is_staff=True,
        )
        permission = Permission.objects.get(
            content_type__app_label="registrations",
            codename="reconcile_registration_payments",
        )
        staff.user_permissions.add(permission)
        verify_payment(
            registration_id=self.registration.pk,
            new_status=Registration.PaymentStatus.PAID,
            actor=staff,
            audit_note="Late provider success independently verified.",
        )
        processed.refresh_from_db()
        self.assertEqual(processed.status, PaymentTransaction.Status.SUCCEEDED)
        self.assertTrue(
            processed.events.filter(event_type="late_success_reconciled").exists()
        )
        self.registration.refresh_from_db()
        self.assertTrue(self.registration.ticket_reference)

    @patch("apps.payments.services.get_provider")
    def test_cancelled_attempt_cannot_issue_ticket_on_late_callback(self, get_provider_mock):
        get_provider_mock.return_value = self.provider
        payment = self.start_checkout()
        payment.status = PaymentTransaction.Status.CANCELLED
        payment.save(update_fields=("status",))
        self.provider.result = self.valid_result()
        processed = self.callback(payment)
        self.assertEqual(processed.status, PaymentTransaction.Status.CANCELLED)
        self.registration.refresh_from_db()
        self.assertFalse(self.registration.ticket_reference)
        self.assertNotEqual(
            self.registration.payment_status,
            Registration.PaymentStatus.PAID,
        )

    @patch("apps.payments.services.get_provider")
    def test_authorized_manual_refund_reconciliation_updates_payment_attempt(self, get_provider_mock):
        get_provider_mock.return_value = self.provider
        payment = self.start_checkout()
        self.provider.result = self.valid_result()
        self.callback(payment)

        staff = get_user_model().objects.create_user(
            username="payment-refund-reviewer",
            email="payment-refund@example.com",
            password="test-password",
            is_staff=True,
        )
        permission = Permission.objects.get(
            content_type__app_label="registrations",
            codename="reconcile_registration_payments",
        )
        staff.user_permissions.add(permission)

        verify_payment(
            registration_id=self.registration.pk,
            new_status=Registration.PaymentStatus.REFUNDED,
            actor=staff,
            audit_note="Refund verified against merchant records.",
        )
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentTransaction.Status.REFUNDED)
        self.assertTrue(
            payment.events.filter(event_type="refund_reconciled").exists()
        )

    @patch("apps.payments.services.get_provider")
    def test_database_failure_is_not_reported_as_success(self, get_provider_mock):
        get_provider_mock.return_value = self.provider
        payment = self.start_checkout()
        self.provider.result = self.valid_result()
        with patch(
            "apps.payments.services._apply_verified_result",
            side_effect=IntegrityError("database unavailable"),
        ):
            with self.assertRaises(PaymentIntegrationError):
                self.callback(payment)
        payment.refresh_from_db()
        self.assertEqual(
            payment.status,
            PaymentTransaction.Status.VERIFICATION_FAILED,
        )
        self.registration.refresh_from_db()
        self.assertNotEqual(
            self.registration.payment_status,
            Registration.PaymentStatus.PAID,
        )

    @patch("apps.payments.services.get_provider")
    def test_browser_return_does_not_mark_payment_paid(self, get_provider_mock):
        get_provider_mock.return_value = self.provider
        payment = self.start_checkout()
        self.client.force_login(self.participant)
        response = self.client.get(
            reverse("payments:return", args=(payment.reference,)),
            {"status": "success", "payment_status": "paid"},
        )
        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.registration.refresh_from_db()
        self.assertEqual(payment.status, PaymentTransaction.Status.PENDING)
        self.assertEqual(self.registration.payment_status, Registration.PaymentStatus.PENDING)

    @patch("apps.payments.services.get_provider")
    def test_payment_attempt_pages_are_private_to_registration_owner(self, get_provider_mock):
        get_provider_mock.return_value = self.provider
        payment = self.start_checkout()
        other = get_user_model().objects.create_user(
            username="payment-private-other",
            email="payment-private-other@example.com",
            password="test-password",
        )
        self.client.force_login(other)
        for route in ("payments:return", "payments:status"):
            response = self.client.get(reverse(route, args=(payment.reference,)))
            self.assertEqual(response.status_code, 403)

    @patch("apps.payments.services.get_provider")
    def test_checkout_start_requires_registration_owner_and_post(self, get_provider_mock):
        get_provider_mock.return_value = self.provider
        other = get_user_model().objects.create_user(
            username="payment-other",
            email="other-payment@example.com",
            password="strong-other-payment",
        )
        self.client.force_login(other)
        url = reverse(
            "payments:start",
            args=(self.registration.reference,),
        )
        self.assertEqual(self.client.post(url, {"method": PaymentMethod.BKASH}).status_code, 404)

    @override_settings(PAYMENT_PROVIDER_CLASS="missing.adapter.Provider")
    def test_invalid_provider_configuration_fails_explicitly(self):
        from django.core.exceptions import ImproperlyConfigured

        with self.assertRaises(ImproperlyConfigured):
            get_provider()

    @patch("apps.payments.services.get_provider")
    def test_callback_endpoint_requires_post_and_does_not_accept_browser_get(self, get_provider_mock):
        get_provider_mock.return_value = self.provider
        response = self.client.get(
            reverse("payments:callback"),
            {"status": "success"},
        )
        self.assertEqual(response.status_code, 405)


class PaymentAdminTests(RegistrationFixture):
    def test_payment_transactions_are_read_only_and_permission_gated(self):
        staff = get_user_model().objects.create_user(
            username="payment-diagnostics",
            email="payment-diagnostics@example.com",
            password="strong-diagnostics-password",
            is_staff=True,
        )
        view_permission = Permission.objects.get(
            content_type__app_label="payments",
            codename="view_paymenttransaction",
        )
        staff.user_permissions.add(view_permission)
        self.client.force_login(staff)
        response = self.client.get(
            reverse("admin:payments_paymenttransaction_changelist")
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Mark selected payments paid")
        self.assertEqual(
            self.client.get(
                reverse("admin:payments_paymentevent_changelist")
            ).status_code,
            403,
        )

    def test_payment_method_choices_are_explicit(self):
        self.assertEqual(
            set(PaymentMethod.values),
            {"bkash", "nagad", "rocket", "card"},
        )
