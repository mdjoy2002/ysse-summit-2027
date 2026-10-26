"""Admin-managed agenda entries for the YSSE Summit."""

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from apps.competitions.models import Competition
from apps.home.models import FeaturedSpeaker, SummitEvent


class ScheduleSession(models.Model):
    """A published agenda entry using the configured summit date and timezone."""

    event = models.ForeignKey(
        SummitEvent,
        related_name="schedule_sessions",
        on_delete=models.CASCADE,
    )
    title = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    venue = models.CharField(max_length=180, blank=True)
    speaker = models.ForeignKey(
        FeaturedSpeaker,
        related_name="schedule_sessions",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    competition = models.ForeignKey(
        Competition,
        related_name="schedule_sessions",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    display_order = models.PositiveSmallIntegerField(default=0, db_index=True)
    is_published = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ("starts_at", "display_order", "pk")
        indexes = [
            models.Index(
                fields=("event", "is_published", "starts_at", "display_order"),
                name="schedule_public_time_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__isnull=True) | Q(ends_at__gt=F("starts_at")),
                name="schedule_end_after_start",
            ),
        ]
        verbose_name = _("Schedule entry")
        verbose_name_plural = _("Schedule entries")

    def clean(self):
        """Reject end times that do not follow the session start."""
        super().clean()
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError(
                {"ends_at": _("The end time must be after the start time.")}
            )

    def __str__(self):
        return self.title
