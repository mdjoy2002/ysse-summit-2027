"""Public schedule presentation."""

from django.db.models import Prefetch
from django.shortcuts import render

from apps.home.models import SummitEvent
from apps.speakers.models import SpeakerCategory

from .models import ScheduleSession


def schedule_index(request):
    """Render published agenda entries in chronological order."""
    event = SummitEvent.objects.get(key=SummitEvent.Key.MAIN)
    sessions = (
        ScheduleSession.objects.filter(event=event, is_published=True)
        .select_related("speaker", "competition")
        .prefetch_related(
            Prefetch(
                "speaker__categories",
                queryset=SpeakerCategory.objects.filter(is_published=True),
            )
        )
        .order_by("starts_at", "display_order", "pk")
    )
    return render(
        request,
        "schedule/index.html",
        {"event": event, "sessions": sessions},
    )
