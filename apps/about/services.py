"""Read-model helpers for the public About YSSE page."""

from .models import (
    FounderVision,
    ImpactStatistic,
    TimelinePeriod,
    YSSEWing,
)


def get_about_page_content():
    """Return ordered, publishable About YSSE content for the page."""
    return {
        "timeline_periods": TimelinePeriod.objects.filter(is_published=True).prefetch_related(
            "milestones"
        ),
        "impact_statistics": ImpactStatistic.objects.filter(is_published=True),
        "founder_vision": FounderVision.objects.filter(
            key=FounderVision.Key.FOUNDER,
            is_published=True,
        ).first(),
        "wings": YSSEWing.objects.filter(is_published=True),
    }
