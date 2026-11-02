"""Participant registration, private ticket, and entry-verification views."""

import uuid
from pathlib import Path

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import (
    FileResponse,
    Http404,
    HttpResponseBadRequest,
    HttpResponseNotAllowed,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.home.models import SummitEvent
from apps.payments.models import PaymentTransaction

from .forms import RegistrationForm
from .models import PaymentSettings, PassType, Registration
from .payment_forms import ManualPaymentSubmissionForm
from .services import (
    RegistrationError,
    create_registration,
    guest_session_owns_registration,
)
from .tracking_forms import RegistrationTrackingForm


def pass_list(request):
    """Display only configured passes currently open for registration."""
    event = SummitEvent.objects.get(key=SummitEvent.Key.MAIN)
    passes = [
        pass_type
        for pass_type in PassType.objects.filter(
            is_published=True, is_available=True
        )
        if pass_type.sales_are_open() and pass_type.has_capacity
    ]
    return render(
        request,
        "registrations/pass_list.html",
        {
            "event": event,
            "passes": passes,
            "registration_open": event.registration_available,
        },
    )


def _remember_guest_registration(request, registration):
    if request.user.is_authenticated:
        return
    guest_registration_ids = request.session.get("guest_registration_ids", [])
    if registration.pk not in guest_registration_ids:
        guest_registration_ids.append(registration.pk)
    request.session["guest_registration_ids"] = guest_registration_ids[-10:]


def register(request):
    """Validate attendee details; finalize paid registrations after method choice."""
    event = SummitEvent.objects.get(key=SummitEvent.Key.MAIN)
    if not event.registration_available:
        messages.error(request, "Summit registration is currently closed.")
        return redirect("registrations:passes")

    initial = {}
    if request.method == "GET" and request.GET.get("pass"):
        initial["pass_type"] = request.GET["pass"]
    if request.method == "POST":
        form = RegistrationForm(request.POST, event=event)
        if form.is_valid():
            if form.cleaned_data["pass_type"].price > 0:
                if not _registration_payment_options():
                    form.add_error(
                        None,
                        "Online payment methods are not configured yet. Please contact the summit team.",
                    )
                else:
                    draft_id = str(uuid.uuid4())
                    drafts = request.session.get("pending_registration_drafts", {})
                    drafts[draft_id] = {
                        "event_id": event.pk,
                        "participant_id": (
                            request.user.pk if request.user.is_authenticated else None
                        ),
                        "form_data": {
                            "pass_type": form.cleaned_data["pass_type"].pk,
                            "full_name": form.cleaned_data["full_name"],
                            "contact_email": form.cleaned_data["contact_email"],
                            "phone_number": form.cleaned_data["phone_number"],
                            "consent": True,
                        },
                    }
                    request.session["pending_registration_drafts"] = dict(
                        list(drafts.items())[-5:]
                    )
                    return redirect(
                        "registrations:registration-payment-method",
                        draft_id=draft_id,
                    )
            else:
                try:
                    registration = create_registration(
                        participant=(
                            request.user if request.user.is_authenticated else None
                        ),
                        form=form,
                        event=event,
                    )
                except RegistrationError as error:
                    form.add_error(None, str(error))
                else:
                    _remember_guest_registration(request, registration)
                    messages.success(request, "Your registration was received.")
                    return redirect(
                        "registrations:thank-you",
                        reference=registration.reference,
                    )
    else:
        form = RegistrationForm(
            event=event,
            initial={
                **initial,
                "full_name": (
                    request.user.get_full_name()
                    if request.user.is_authenticated
                    else ""
                ),
                "contact_email": (
                    request.user.email if request.user.is_authenticated else ""
                ),
            },
        )
    return render(
        request,
        "registrations/register.html",
        {"event": event, "form": form},
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
def registration_history(request):
    """Show only registrations owned by the signed-in participant."""
    registrations = Registration.objects.filter(
        participant=request.user
    ).select_related("event", "pass_type")
    return render(
        request,
        "registrations/history.html",
        {"registrations": registrations},
    )


def track_registration(request):
    """Look up a registration using its reference and email or phone."""
    form = RegistrationTrackingForm(request.POST or None)
    registration = None
    if request.method == "POST" and form.is_valid():
        candidate = (
            Registration.objects.select_related("event", "pass_type")
            .filter(reference__iexact=form.cleaned_data["reference"].strip())
            .first()
        )
        contact = form.cleaned_data["email"].strip()
        contact_digits = "".join(
            str(int(character)) for character in contact if character.isdecimal()
        )
        registration_digits = (
            "".join(
                str(int(character))
                for character in candidate.phone_number
                if character.isdecimal()
            )
            if candidate is not None
            else ""
        )
        if candidate is not None and (
            candidate.contact_email.casefold() == contact.casefold()
            or (contact_digits and contact_digits == registration_digits)
        ):
            registration = candidate
        else:
            form.add_error(
                None,
                "We could not find a registration with those details. Check your ID and email or phone number.",
            )
    return render(
        request,
        "registrations/track.html",
        {"form": form, "registration": registration},
    )


def _registration_payment_options():
    settings = PaymentSettings.objects.filter(
        key=PaymentSettings.Key.MAIN
    ).first()
    if settings is None:
        return []
    return [
        (method, label, getattr(settings, field))
        for method, label, field in (
            ("bkash", "bKash", "bkash_number"),
            ("nagad", "Nagad", "nagad_number"),
            ("rocket", "Rocket", "rocket_number"),
        )
        if getattr(settings, field)
    ]


def _is_registration_owner(request, registration):
    return (
        request.user.is_authenticated
        and registration.participant_id == request.user.pk
    ) or guest_session_owns_registration(request, registration)


def select_payment_method(request, reference):
    """Present configured mobile payment numbers after registration."""
    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(("GET", "POST"))
    registration = get_object_or_404(
        Registration.objects.select_related("event", "pass_type", "participant"),
        reference=reference,
    )
    if not _is_registration_owner(request, registration):
        raise Http404
    if registration.amount_due <= 0:
        return redirect(
            "registrations:thank-you",
            reference=registration.reference,
        )
    if not _can_submit_manual_payment(registration):
        return redirect(
            "registrations:detail",
            reference=registration.reference,
        )

    payment_options = _registration_payment_options()
    if request.method == "POST":
        method = request.POST.get("method", "")
        if method not in {value for value, _label, _number in payment_options}:
            return HttpResponseBadRequest("Choose a configured payment method.")
        return redirect(
            "registrations:payment-details",
            reference=registration.reference,
            method=method,
        )
    return render(
        request,
        "registrations/payment_method.html",
        {
            "registration": registration,
            "amount_due": registration.amount_due,
            "payment_options": payment_options,
        },
    )


def select_registration_payment_method(request, draft_id):
    """Finalize a paid registration only after an owned draft submits a method."""
    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(("GET", "POST"))

    drafts = request.session.get("pending_registration_drafts", {})
    draft = drafts.get(str(draft_id))
    expected_participant_id = (
        request.user.pk if request.user.is_authenticated else None
    )
    if draft is None or draft.get("participant_id") != expected_participant_id:
        raise Http404

    event = get_object_or_404(
        SummitEvent,
        pk=draft.get("event_id"),
        key=SummitEvent.Key.MAIN,
    )
    form = RegistrationForm(draft.get("form_data", {}), event=event)
    if not event.registration_available or not form.is_valid():
        drafts.pop(str(draft_id), None)
        request.session["pending_registration_drafts"] = drafts
        messages.error(
            request,
            "Registration details or pass availability changed. Please register again.",
        )
        return redirect("registrations:new")

    payment_options = _registration_payment_options()
    if request.method == "GET":
        return render(
            request,
            "registrations/payment_method.html",
            {
                "registration": None,
                "amount_due": form.cleaned_data["pass_type"].price,
                "payment_options": payment_options,
            },
        )

    method = request.POST.get("method", "")
    if method not in {value for value, _label, _number in payment_options}:
        return HttpResponseBadRequest("Choose a configured payment method.")

    try:
        registration = create_registration(
            participant=(
                request.user if request.user.is_authenticated else None
            ),
            form=form,
            event=event,
            manual_payment_method=method,
        )
    except RegistrationError as error:
        drafts.pop(str(draft_id), None)
        request.session["pending_registration_drafts"] = drafts
        messages.error(request, str(error))
        return redirect("registrations:new")

    drafts.pop(str(draft_id), None)
    request.session["pending_registration_drafts"] = drafts
    _remember_guest_registration(request, registration)
    messages.success(
        request,
        "Registration received. Submit your transaction details to complete payment.",
    )
    return redirect(
        "registrations:payment-details",
        reference=registration.reference,
        method=method,
    )


def _get_owned_or_staff_registration(request, reference):
    registration = get_object_or_404(
        Registration.objects.select_related("event", "pass_type", "participant"),
        reference=reference,
    )
    if (
        request.user.is_authenticated
        and registration.participant_id == request.user.pk
    ):
        return registration
    if guest_session_owns_registration(request, registration):
        return registration
    if (
        request.user.is_authenticated
        and request.user.is_staff
        and request.user.has_perm("registrations.view_registration")
    ):
        return registration
    raise Http404


def registration_detail(request, reference):
    """Render an owner's or session-authorized guest's private registration."""
    registration = _get_owned_or_staff_registration(request, reference)
    payment_transactions = registration.payment_transactions.order_by("-created_at")
    return render(
        request,
        "registrations/detail.html",
        {
            "registration": registration,
            "payment_transactions": payment_transactions,
        },
    )


def _can_submit_manual_payment(registration):
    """Prevent competing provider checkouts and duplicate pending submissions."""
    if not (
        registration.status == Registration.Status.PENDING
        and registration.amount_due > 0
        and registration.payment_status
        in (
            Registration.PaymentStatus.PENDING,
            Registration.PaymentStatus.FAILED,
            Registration.PaymentStatus.CANCELLED,
        )
        and not (
            registration.payment_status == Registration.PaymentStatus.PENDING
            and registration.payment_submitted_at
        )
    ):
        return False
    return not registration.payment_transactions.filter(
        status=PaymentTransaction.Status.PENDING,
    ).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
    ).exists()


def submit_manual_payment(request, reference, method):
    """Accept transaction ID and screenshot after the participant chooses a method."""
    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(("GET", "POST"))
    registration = get_object_or_404(
        Registration.objects.select_related("participant", "event", "pass_type"),
        reference=reference,
    )
    if not _is_registration_owner(request, registration):
        raise Http404
    payment_options = _registration_payment_options()
    selected_option = next(
        (
            option
            for option in payment_options
            if option[0] == method
        ),
        None,
    )
    if selected_option is None or not _can_submit_manual_payment(registration):
        raise Http404

    form_data = request.POST.copy() if request.method == "POST" else None
    if form_data is not None:
        form_data["manual_payment_method"] = method
    form = ManualPaymentSubmissionForm(
        form_data,
        request.FILES if request.method == "POST" else None,
        instance=registration,
    )
    form.fields["manual_payment_method"].widget = forms.HiddenInput()
    form.initial["manual_payment_method"] = method
    if request.method == "POST":
        with transaction.atomic():
            registration = Registration.objects.select_for_update().get(
                pk=registration.pk
            )
            if not _can_submit_manual_payment(registration):
                raise Http404
            form.instance = registration
            if form.is_valid():
                previous_proof = registration.payment_proof
                payment_submission = form.save(commit=False)
                payment_submission.payment_submitted_at = timezone.now()
                payment_submission.save(
                    update_fields=(
                        "manual_payment_method",
                        "payment_transaction_id",
                        "payment_proof",
                        "payment_submitted_at",
                        "updated_at",
                    )
                )
                if (
                    previous_proof
                    and previous_proof.name != payment_submission.payment_proof.name
                ):
                    transaction.on_commit(
                        lambda: previous_proof.storage.delete(previous_proof.name)
                    )
                from .services import _email_after_commit

                _email_after_commit(registration.pk, "registration")
                messages.success(
                    request,
                    "Your payment details have been submitted.",
                )
                return redirect(
                    "registrations:thank-you",
                    reference=registration.reference,
                )

    return render(
        request,
        "registrations/payment_details.html",
        {
            "registration": registration,
            "payment_method": selected_option[1],
            "payment_number": selected_option[2],
            "form": form,
        },
        status=400 if request.method == "POST" else 200,
    )


def payment_thank_you(request, reference):
    """Confirm receipt of an owner's submitted registration/payment."""
    registration = get_object_or_404(
        Registration.objects.select_related("event", "pass_type", "participant"),
        reference=reference,
    )
    if not _is_registration_owner(request, registration):
        raise Http404
    return render(
        request,
        "registrations/thank_you.html",
        {"registration": registration},
    )


def payment_proof(request, reference):
    """Stream uploaded proof privately to its owner or authorized payment staff."""
    registration = get_object_or_404(
        Registration.objects.select_related("participant"),
        reference=reference,
    )
    is_owner = (
        request.user.is_authenticated
        and registration.participant_id == request.user.pk
    ) or guest_session_owns_registration(request, registration)
    can_review = request.user.is_authenticated and request.user.is_staff and (
        request.user.has_perm("registrations.view_registration")
        or request.user.has_perm("registrations.verify_registration_payments")
        or request.user.has_perm("registrations.reconcile_registration_payments")
    )
    if not is_owner and not can_review:
        raise Http404
    if not registration.payment_proof:
        raise Http404

    response = FileResponse(
        registration.payment_proof.open("rb"),
        as_attachment=True,
        filename="payment-proof"
        + Path(registration.payment_proof.name).suffix.lower(),
        content_type="application/octet-stream",
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


def ticket(request, ticket_reference):
    """Render a ticket for its owner, authorized staff, or UUID bearer."""
    registration = get_object_or_404(
        Registration.objects.select_related("event", "pass_type", "participant"),
        ticket_reference=ticket_reference,
    )
    if registration.participant_id is None:
        return render(
            request,
            "registrations/ticket.html",
            {
                "registration": registration,
                "ticket_valid": registration.ticket_is_valid
                and registration.event.status
                not in (SummitEvent.Status.CANCELLED, SummitEvent.Status.COMPLETED)
                and timezone.now() <= registration.event.event_end_at,
            },
        )
    if (
        not request.user.is_authenticated
        or (
            registration.participant_id != request.user.pk
            and not (
                request.user.is_staff
                and request.user.has_perm("registrations.view_registration")
            )
        )
    ):
        raise Http404
    return render(
        request,
        "registrations/ticket.html",
        {
            "registration": registration,
            "ticket_valid": registration.ticket_is_valid
            and registration.event.status
            not in (SummitEvent.Status.CANCELLED, SummitEvent.Status.COMPLETED)
            and timezone.now() <= registration.event.event_end_at,
        },
    )


@login_required
def verify_ticket(request, ticket_reference):
    """Let specifically authorized entry staff verify a ticket reference."""
    if (
        not request.user.is_staff
        or not request.user.has_perm("registrations.verify_tickets_at_entry")
    ):
        raise PermissionDenied
    registration = get_object_or_404(
        Registration.objects.select_related("event", "pass_type"),
        ticket_reference=ticket_reference,
    )
    valid = (
        registration.ticket_is_valid
        and registration.event.status
        not in (SummitEvent.Status.CANCELLED, SummitEvent.Status.COMPLETED)
        and timezone.now() <= registration.event.event_end_at
    )
    return render(
        request,
        "registrations/verify_ticket.html",
        {"registration": registration, "ticket_valid": valid},
    )
