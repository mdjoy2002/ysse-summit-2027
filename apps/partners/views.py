"""Public sponsor directory, deck download, and partnership inquiry handling."""

from django.contrib import messages
from django.core.cache import cache
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from .forms import PartnershipInquiryForm
from .models import PartnershipSettings, SponsorCategory
from .services import inquiry_rate_limit_exceeded


@require_http_methods(["GET", "POST"])
def partners_index(request):
    """Show published sponsor tiers and accept privacy-consented inquiries."""
    categories = (
        SponsorCategory.objects.filter(
            is_published=True,
            sponsors__is_published=True,
        )
        .distinct()
        .prefetch_related("sponsors")
    )
    settings = PartnershipSettings.objects.filter(key=PartnershipSettings.Key.MAIN).first()
    rate_limited = False

    if request.method == "POST":
        remote_address = request.META.get("REMOTE_ADDR", "")
        if inquiry_rate_limit_exceeded(remote_address):
            rate_limited = True
            form = PartnershipInquiryForm()
            messages.error(
                request,
                "Too many inquiries were submitted. Please try again later.",
            )
        else:
            form = PartnershipInquiryForm(request.POST)
            if form.is_valid():
                form.save()
                messages.success(
                    request,
                    "Thank you. Your inquiry has been received for review; "
                    "it is not an approved partnership.",
                )
                return redirect("partners:index")
    else:
        form = PartnershipInquiryForm()

    return render(
        request,
        "partners/index.html",
        {
            "categories": categories,
            "settings": settings,
            "form": form,
            "rate_limited": rate_limited,
        },
    )


@require_GET
def sponsorship_deck(request):
    """Deliver only the PDF currently uploaded through the Admin settings."""
    settings = PartnershipSettings.objects.filter(key=PartnershipSettings.Key.MAIN).first()
    if not settings or not settings.sponsorship_deck:
        raise Http404("The sponsorship deck is not currently available.")
    try:
        deck = settings.sponsorship_deck.open("rb")
    except OSError as error:
        raise Http404("The sponsorship deck is not currently available.") from error

    response = FileResponse(
        deck,
        as_attachment=True,
        filename="YSSE-Summit-2027-Sponsorship-Deck.pdf",
        content_type="application/pdf",
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response
