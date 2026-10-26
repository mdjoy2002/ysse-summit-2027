"""Editable YSSE history, impact, founder, and wing content."""

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.home.validators import validate_image_size, validate_video_url


class TimelinePeriod(models.Model):
    """A dated period in YSSE's historical timeline."""

    start_year = models.PositiveSmallIntegerField()
    end_year = models.PositiveSmallIntegerField()
    title = models.CharField(max_length=120)
    sort_order = models.PositiveSmallIntegerField(default=0, db_index=True)
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ("sort_order", "start_year")
        constraints = [
            models.UniqueConstraint(
                fields=("start_year", "end_year"),
                name="about_unique_timeline_period",
            ),
            models.CheckConstraint(
                condition=models.Q(end_year__gte=models.F("start_year")),
                name="about_timeline_years_valid",
            ),
        ]
        verbose_name = _("Timeline period")
        verbose_name_plural = _("Timeline periods")

    def __str__(self):
        return f"{self.start_year}–{self.end_year}: {self.title}"


class TimelineMilestone(models.Model):
    """One user-confirmed milestone belonging to a timeline period."""

    period = models.ForeignKey(
        TimelinePeriod,
        on_delete=models.CASCADE,
        related_name="milestones",
    )
    description = models.CharField(max_length=220)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("sort_order", "pk")
        verbose_name = _("Timeline milestone")
        verbose_name_plural = _("Timeline milestones")

    def __str__(self):
        return self.description


class ImpactStatistic(models.Model):
    """An impact figure that stays private until an editor verifies it."""

    label = models.CharField(max_length=100, unique=True)
    value = models.CharField(max_length=32)
    sort_order = models.PositiveSmallIntegerField(default=0, db_index=True)
    is_published = models.BooleanField(
        default=False,
        help_text=_("Only publish this figure after it has been verified."),
    )

    class Meta:
        ordering = ("sort_order", "pk")
        verbose_name = _("Impact statistic")
        verbose_name_plural = _("Impact statistics")

    def __str__(self):
        return f"{self.label}: {self.value}"


class FounderVision(models.Model):
    """A single editable, explicitly published founder or president message."""

    class Key(models.TextChoices):
        FOUNDER = "founder", _("Founder vision")

    key = models.CharField(
        max_length=20,
        choices=Key.choices,
        default=Key.FOUNDER,
        unique=True,
    )
    name = models.CharField(max_length=120, blank=True)
    designation = models.CharField(max_length=160, blank=True)
    profile_image = models.ImageField(
        upload_to="about/founder/%Y/%m/",
        blank=True,
        validators=[validate_image_size],
    )
    video_message_url = models.URLField(
        blank=True,
        validators=[validate_video_url],
        help_text=_("Optional HTTPS link to the founder's video message."),
    )
    video_thumbnail = models.ImageField(
        upload_to="about/founder/%Y/%m/",
        blank=True,
        validators=[validate_image_size],
    )
    written_message = models.TextField(blank=True)
    is_published = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Founder vision")
        verbose_name_plural = _("Founder vision")

    def clean(self):
        """Require a complete, attributable message before publishing."""
        super().clean()
        if self.is_published and not all(
            (self.name.strip(), self.designation.strip(), self.written_message.strip())
        ):
            raise ValidationError(
                {
                    "is_published": _(
                        "Add the founder's name, official designation, and written "
                        "message before publishing this section."
                    )
                }
            )

    def __str__(self):
        return self.name or str(self._meta.verbose_name)


class YSSEWing(models.Model):
    """A YSSE initiative card with optional logo and external destination."""

    name = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=300)
    image = models.ImageField(
        upload_to="about/wings/%Y/%m/",
        blank=True,
        validators=[validate_image_size],
    )
    external_url = models.URLField(
        blank=True,
        validators=[URLValidator(schemes=["https"])],
    )
    sort_order = models.PositiveSmallIntegerField(default=0, db_index=True)
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ("sort_order", "name")
        verbose_name = _("YSSE wing")
        verbose_name_plural = _("YSSE wings")

    def __str__(self):
        return self.name
