"""Public contact, FAQ, legal, press, and newsletter endpoints."""

import logging
import smtplib

from django.contrib import messages
from django.http import FileResponse, Http404
from mimetypes import guess_type
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .forms import ContactInquiryForm, NewsletterForm
from .models import (
    ContactInquiry,
    FAQ,
    LegalDocument,
    PressRelease,
    PublicInformationSettings,
)
from .services import (
    confirm_newsletter_subscription,
    rate_limit_exceeded,
    subscribe_to_newsletter,
    unsubscribe_from_newsletter,
)

logger = logging.getLogger("ysse.contact")


@require_http_methods(["GET", "POST"])
def contact_page(request):
    """Display official contact information and accept private inquiries."""
    settings = PublicInformationSettings.objects.filter(
        key=PublicInformationSettings.Key.MAIN
    ).first()
    if request.method == "POST":
        if rate_limit_exceeded("inquiry", request.META.get("REMOTE_ADDR", "")):
            messages.error(request, "Too many messages. Please try again later.")
            form = ContactInquiryForm()
        else:
            form = ContactInquiryForm(request.POST)
            if form.is_valid():
                form.save()
                messages.success(
                    request,
                    "Thank you. Your private inquiry has been received for review.",
                )
                return redirect("contact:contact")
    else:
        form = ContactInquiryForm()
    return render(
        request,
        "contact/contact.html",
        {"settings": settings, "form": form},
    )


@require_GET
def faq_page(request):
    """List published FAQs only."""
    faqs = FAQ.objects.filter(is_published=True)
    return render(request, "contact/faq.html", {"faqs": faqs})


@require_GET
def legal_page(request, kind):
    """Show only organizer-reviewed and published policy documents."""
    if kind not in LegalDocument.Kind.values:
        raise Http404
    document = LegalDocument.objects.filter(
        kind=kind,
        is_published=True,
        organizer_reviewed=True,
    ).first()
    return render(
        request,
        "contact/legal.html",
        {"document": document, "kind": kind},
    )


@require_GET
def press_list(request):
    """Display published press releases and approved public attachments."""
    releases = PressRelease.objects.filter(
        is_published=True,
        publication_date__lte=timezone.localdate(),
    )
    return render(request, "contact/press_list.html", {"releases": releases})


@require_GET
def press_detail(request, slug):
    """Render one published press release."""
    release = get_object_or_404(
        PressRelease,
        slug=slug,
        is_published=True,
        publication_date__lte=timezone.localdate(),
    )
    return render(request, "contact/press_detail.html", {"release": release})


@require_GET
def press_attachment(request, slug):
    """Serve attachments only for currently published press releases."""
    release = get_object_or_404(
        PressRelease,
        slug=slug,
        is_published=True,
        publication_date__lte=timezone.localdate(),
    )
    if not release.attachment:
        raise Http404
    try:
        attachment = release.attachment.open("rb")
    except OSError as error:
        raise Http404 from error
    response = FileResponse(
        attachment,
        as_attachment=True,
        filename=f"{release.slug}.pdf",
        content_type="application/pdf",
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


@require_GET
def press_cover(request, slug):
    """Stream a press cover only while its release is public."""
    release = get_object_or_404(
        PressRelease,
        slug=slug,
        is_published=True,
        publication_date__lte=timezone.localdate(),
    )
    if not release.cover_image:
        raise Http404
    try:
        cover = release.cover_image.open("rb")
    except OSError as error:
        raise Http404 from error
    content_type = guess_type(release.cover_image.name)[0] or "application/octet-stream"
    response = FileResponse(
        cover,
        filename=f"{release.slug}-cover",
        content_type=content_type,
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


@require_GET
def newsletter_page(request):
    """Display a generic newsletter confirmation message without subscriber data."""
    return render(request, "contact/newsletter_result.html")


@require_POST
def newsletter_subscribe(request):
    """Accept an email only after consent, validation, and rate-limit checks."""
    if rate_limit_exceeded("newsletter", request.META.get("REMOTE_ADDR", "")):
        messages.error(request, "Too many requests. Please try again later.")
        return redirect("contact:newsletter-result")
    form = NewsletterForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a valid email and consent to receive updates.")
        return redirect("contact:newsletter-result")
    try:
        subscribe_to_newsletter(
            email=form.cleaned_data["email"],
            consent_given=form.cleaned_data["consent_given"],
            site_origin=request.build_absolute_uri("/").rstrip("/"),
        )
    except (OSError, smtplib.SMTPException) as error:
        logger.error(
            "Newsletter confirmation delivery failed exception_type=%s",
            type(error).__name__,
        )
        messages.error(
            request,
            "We could not send the confirmation email right now. Please try again later.",
        )
        response = render(request, "contact/newsletter_result.html")
        response.status_code = 503
        return response
    messages.success(
        request,
        "If this address can be subscribed, a confirmation email has been sent.",
    )
    return redirect("contact:newsletter-result")


@require_GET
def newsletter_confirm(request, token):
    """Confirm an emailed opt-in token without disclosing the subscriber address."""
    if confirm_newsletter_subscription(token):
        messages.success(request, "Your newsletter subscription is confirmed.")
    else:
        messages.info(request, "This confirmation link is invalid or already used.")
    return redirect("contact:newsletter-result")


@require_http_methods(["GET", "POST"])
def newsletter_unsubscribe(request, token):
    """Ask for confirmation, then unsubscribe via an unguessable token."""
    if request.method == "GET":
        return render(request, "contact/newsletter_unsubscribe.html")
    if unsubscribe_from_newsletter(token):
        messages.success(request, "You have been unsubscribed from newsletter emails.")
    else:
        messages.info(request, "This unsubscribe link is invalid or already used.")
    return redirect("contact:newsletter-result")
