"""Public directory and detail views for published speaker profiles."""

from django.shortcuts import get_object_or_404, render

from apps.home.models import FeaturedSpeaker

from .models import SpeakerCategory


def speaker_list(request):
    """Render published profiles, optionally narrowed to one category."""
    categories = SpeakerCategory.objects.filter(is_published=True)
    speakers = FeaturedSpeaker.objects.published().prefetch_related("categories")
    active_category = None
    category_slug = request.GET.get("category", "").strip()

    if category_slug:
        active_category = get_object_or_404(
            categories,
            slug=category_slug,
        )
        speakers = speakers.filter(categories=active_category)

    return render(
        request,
        "speakers/list.html",
        {
            "categories": categories,
            "speakers": speakers.distinct(),
            "active_category": active_category,
        },
    )


def speaker_detail(request, slug):
    """Render a published profile without exposing draft speaker records."""
    speaker = get_object_or_404(
        FeaturedSpeaker.objects.published().prefetch_related("categories"),
        slug=slug,
    )
    return render(request, "speakers/detail.html", {"speaker": speaker})
