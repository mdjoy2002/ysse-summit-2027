"""Participant registration, private ticket, and entry-verification views."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.home.models import SummitEvent
from apps.payments.models import PaymentMethod

from .forms import RegistrationForm
from .models import PassType, Registration
from .services import (
    RegistrationError,
    create_registration,
    guest_session_owns_registration,
)
from apps.payments.services import get_supported_methods, is_demo_provider


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
            "demo_payment_mode": is_demo_provider(),
        },
    )


def register(request):
    """Create a pass registration for a signed-in participant or guest."""
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
                if not request.user.is_authenticated:
                    guest_registration_ids = request.session.get(
                        "guest_registration_ids", []
                    )
                    if registration.pk not in guest_registration_ids:
                        guest_registration_ids.append(registration.pk)
                    request.session["guest_registration_ids"] = (
                        guest_registration_ids[-10:]
                    )
                messages.success(request, "Your registration was received.")
                return redirect(
                    "registrations:detail",
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
    payment_methods = get_supported_methods()
    enabled_method_values = {value for value, _label in payment_methods}
    payment_transactions = registration.payment_transactions.order_by("-created_at")
    return render(
        request,
        "registrations/detail.html",
        {
            "registration": registration,
            "payment_methods": payment_methods,
            "demo_payment_mode": is_demo_provider(),
            "planned_payment_methods": [
                {
                    "value": method.value,
                    "label": method.label,
                    "available": method.value in enabled_method_values,
                }
                for method in PaymentMethod
            ],
            "payment_transactions": payment_transactions,
        },
    )


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
