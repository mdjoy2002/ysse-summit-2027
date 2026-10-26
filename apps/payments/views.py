"""Owner-protected checkout and provider-verified callback endpoints."""

import logging

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotAllowed,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from apps.registrations.models import Registration
from apps.registrations.services import guest_session_owns_registration

from .models import PaymentTransaction
from .providers.base import ProviderError
from .providers.demo import signed_demo_result
from .services import (
    PaymentIntegrationError,
    PaymentProcessingError,
    _callback_payload,
    create_checkout,
    get_provider,
    get_supported_methods,
    process_provider_callback,
)


logger = logging.getLogger("ysse.payments")


def start_checkout(request, registration_reference):
    """Start hosted checkout only for the account owner or session guest."""
    if request.method != "POST":
        return HttpResponseNotAllowed(("POST",))
    registration = get_object_or_404(
        Registration.objects.select_related("participant", "event"),
        reference=registration_reference,
    )
    if not (
        request.user.is_authenticated
        and registration.participant_id == request.user.pk
    ) and not guest_session_owns_registration(request, registration):
        raise Http404
    supported_methods = tuple(get_supported_methods())
    if not supported_methods:
        messages.error(
            request,
            "Online payment is not configured yet. Do not submit payment details on this site.",
        )
        return redirect(
            "registrations:detail",
            reference=registration.reference,
        )
    method = request.POST.get("method", "")
    if method not in dict(supported_methods):
        return HttpResponseBadRequest("Choose an available payment method.")
    try:
        payment = create_checkout(
            registration=registration,
            method=method,
        )
    except PaymentIntegrationError as error:
        logger.info(
            "Payment checkout was unavailable code=%s registration=%s",
            error.code,
            registration.reference,
        )
        messages.error(
            request,
            "We could not start a payment session. Your registration is still pending.",
        )
        return redirect(
            "registrations:detail",
            reference=registration.reference,
        )
    if payment.provider == "demo-sandbox":
        return redirect("payments:demo-checkout", reference=payment.reference)
    return redirect(payment.checkout_url)


def demo_checkout(request, reference):
    """Offer explicit simulated outcomes only where demo payments are enabled."""
    if not settings.PAYMENT_DEMO_ENABLED:
        raise Http404
    provider = get_provider()
    if not getattr(provider, "is_demo", False):
        raise Http404

    payment = get_object_or_404(
        PaymentTransaction.objects.select_related("registration"),
        reference=reference,
        provider=provider.name,
    )
    if not (
        request.user.is_authenticated
        and payment.registration.participant_id == request.user.pk
    ) and not guest_session_owns_registration(request, payment.registration):
        raise Http404
    if request.method == "POST":
        if payment.status != PaymentTransaction.Status.PENDING:
            messages.error(request, "This demo payment attempt is no longer pending.")
            return redirect("payments:return", reference=payment.reference)
        if payment.expires_at and payment.expires_at <= timezone.now():
            messages.error(request, "This demo payment attempt has expired.")
            return redirect("payments:return", reference=payment.reference)

        result = request.POST.get("result", "")
        if result not in ("success", "failed", "cancelled"):
            return HttpResponseBadRequest("Choose a demo payment result.")
        payload, headers = signed_demo_result(
            reference=payment.reference,
            result=result,
        )
        process_provider_callback(
            payload=payload,
            headers=headers,
            raw_body=(
                f"{payload['reference']}:{payload['result']}:"
                f"{payload['event_reference']}"
            ).encode("utf-8"),
        )
        messages.info(
            request,
            "Demo result recorded. No real payment was made.",
        )
        return redirect("payments:return", reference=payment.reference)

    if request.method != "GET":
        return HttpResponseNotAllowed(("GET", "POST"))
    return render(
        request,
        "payments/demo_checkout.html",
        {"payment": payment},
    )


def payment_return(request, reference):
    """Show stored payment state; browser return parameters never verify payment."""
    payment = get_object_or_404(
        PaymentTransaction.objects.select_related("registration"),
        reference=reference,
    )
    if (
        not (
            request.user.is_authenticated
            and payment.registration.participant_id == request.user.pk
        )
        and not guest_session_owns_registration(request, payment.registration)
        and not (
            request.user.is_authenticated
            and request.user.is_staff
            and request.user.has_perm("payments.view_paymenttransaction")
        )
    ):
        raise PermissionDenied
    return render(
        request,
        "payments/return.html",
        {"payment": payment},
    )


@csrf_exempt
def provider_callback(request):
    """Receive a callback only as a verification trigger, never a success signal."""
    if request.method != "POST":
        return HttpResponseNotAllowed(("POST",))
    try:
        payload = _callback_payload(request)
        process_provider_callback(
            payload=payload,
            headers=request.headers,
            raw_body=request.body,
        )
    except PaymentIntegrationError as error:
        logger.warning("Payment callback rejected code=%s", error.code)
        return HttpResponseBadRequest("Payment notification could not be verified.")
    except PaymentProcessingError:
        return HttpResponse("Payment verification is temporarily unavailable.", status=503)
    except ProviderError as error:
        logger.warning("Payment callback rejected code=%s", error.code)
        return HttpResponseBadRequest("Payment notification could not be verified.")
    except Exception as error:
        logger.error(
            "Unexpected payment callback processing error exception_type=%s",
            type(error).__name__,
        )
        return HttpResponse("Payment verification is temporarily unavailable.", status=503)
    return HttpResponse("Notification processed.", content_type="text/plain")


def transaction_status(request, reference):
    """Show an attempt only to its registration owner or authorized staff."""
    payment = get_object_or_404(
        PaymentTransaction.objects.select_related("registration"),
        reference=reference,
    )
    if (
        not (
            request.user.is_authenticated
            and payment.registration.participant_id == request.user.pk
        )
        and not guest_session_owns_registration(request, payment.registration)
        and not (
            request.user.is_authenticated
            and request.user.is_staff
            and request.user.has_perm("payments.view_paymenttransaction")
        )
    ):
        raise PermissionDenied
    return render(request, "payments/return.html", {"payment": payment})
