"""Database models for editable homepage content."""

from datetime import datetime
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models
from django.utils.text import slugify
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .validators import (
    validate_image_size,
    validate_local_destination,
    validate_video_url,
)


def default_event_start():
    """Return the initially announced start time in Bangladesh time."""
    return datetime(2027, 2, 1, 8, 30, tzinfo=ZoneInfo("Asia/Dhaka"))


def default_event_end():
    """Return the initially announced end time in Bangladesh time."""
    return datetime(2027, 2, 1, 18, 0, tzinfo=ZoneInfo("Asia/Dhaka"))


class SummitEvent(models.Model):
    """Single admin-managed source of truth for summit date and hero content."""

    class Key(models.TextChoices):
        MAIN = "main", _("Main summit")

    class Status(models.TextChoices):
        PLANNED = "planned", _("Planned")
        POSTPONED = "postponed", _("Postponed")
        CANCELLED = "cancelled", _("Cancelled")
        COMPLETED = "completed", _("Completed")

    key = models.CharField(
        max_length=20, choices=Key.choices, unique=True, default=Key.MAIN
    )
    event_title = models.CharField(
        max_length=140, default="Social Entrepreneurship Summit 2027"
    )
    theme = models.CharField(
        max_length=200,
        default="Youth Leadership, Social Business Innovation & Sustainable Impact",
    )
    description = models.TextField(
        blank=True,
        default=(
            "The Social Entrepreneurship Summit 2027 brings together young "
            "leaders and social business stakeholders to explore innovation "
            "and sustainable impact."
        ),
    )
    subtitle = models.CharField(
        max_length=180,
        default="Transforming the Future Through Youth & Social Business.",
    )
    legacy_message = models.CharField(
        max_length=180, default="Celebrating 12 Years of YSSE's Legacy"
    )
    event_start_at = models.DateTimeField(default=default_event_start)
    event_end_at = models.DateTimeField(default=default_event_end)
    venue = models.CharField(
        max_length=200,
        blank=True,
        default="SEL Centre Auditorium / Designated Venue, Dhaka",
        help_text=_("Proposed or final venue; update this field if the location changes."),
    )
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PLANNED
    )
    banner_image = models.ImageField(
        upload_to="home/summit/%Y/%m/",
        blank=True,
        validators=[validate_image_size],
    )
    registration_available = models.BooleanField(default=True)
    hero_poster = models.ImageField(
        upload_to="home/hero/%Y/%m/",
        blank=True,
        validators=[validate_image_size],
    )
    hero_video_url = models.URLField(
        blank=True,
        validators=[validate_video_url],
        help_text=_("Optional HTTPS URL to a muted, looping MP4/WebM video."),
    )
    register_cta_text = models.CharField(max_length=40, default="Register Now")
    register_cta_url = models.CharField(
        max_length=255,
        default="#registration",
        validators=[validate_local_destination],
    )
    explore_cta_text = models.CharField(max_length=40, default="Explore Competitions")
    explore_cta_url = models.CharField(
        max_length=255,
        default="/competitions/",
        validators=[validate_local_destination],
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Summit event configuration")
        verbose_name_plural = _("Summit event configuration")

    def clean(self):
        """Ensure the configured event ends after it starts."""
        super().clean()
        if (
            self.event_start_at
            and self.event_end_at
            and self.event_end_at <= self.event_start_at
        ):
            raise ValidationError(
                {"event_end_at": _("The event end time must be after its start time.")}
            )

    def __str__(self):
        return self.event_title


class HomeHighlight(models.Model):
    """A concise, ordered event highlight shown on the homepage."""

    class Icon(models.TextChoices):
        SPARK = "spark", _("Spark")
        TROPHY = "trophy", _("Trophy")
        NETWORK = "network", _("Network")
        AWARD = "award", _("Award")
        MAGAZINE = "magazine", _("Magazine")

    title = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=240)
    icon = models.CharField(
        max_length=16, choices=Icon.choices, default=Icon.SPARK
    )
    sort_order = models.PositiveSmallIntegerField(default=0, db_index=True)
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ("sort_order", "pk")
        verbose_name = _("Homepage highlight")
        verbose_name_plural = _("Homepage highlights")

    def __str__(self):
        return self.title


class FeaturedSpeakerQuerySet(models.QuerySet):
    """Share the directory's publication/category visibility rules."""

    def published(self):
        return self.filter(
            is_published=True,
            categories__is_published=True,
        ).distinct()


class FeaturedSpeaker(models.Model):
    """A reusable speaker profile for the homepage and speaker directory."""

    class ParticipationStatus(models.TextChoices):
        UNCONFIRMED = "unconfirmed", _("Participation not confirmed")
        CONFIRMED = "confirmed", _("Participation confirmed")

    full_name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=180, blank=True, unique=True)
    official_designation = models.CharField(max_length=160)
    organization = models.CharField(max_length=160)
    biography = models.TextField()
    profile_image = models.ImageField(
        upload_to="home/speakers/%Y/%m/",
        blank=True,
        validators=[validate_image_size],
    )
    profile_url = models.URLField(blank=True, validators=[URLValidator()])
    session_topic = models.CharField(max_length=180, blank=True)
    categories = models.ManyToManyField(
        "speakers.SpeakerCategory",
        related_name="speakers",
        blank=True,
    )
    display_order = models.PositiveSmallIntegerField(default=0, db_index=True)
    participation_status = models.CharField(
        max_length=12,
        choices=ParticipationStatus.choices,
        default=ParticipationStatus.UNCONFIRMED,
        db_index=True,
    )
    is_published = models.BooleanField(default=False)
    is_featured = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = FeaturedSpeakerQuerySet.as_manager()

    class Meta:
        ordering = ("display_order", "full_name")
        verbose_name = _("Speaker")
        verbose_name_plural = _("Speakers")
        indexes = [
            models.Index(
                fields=("is_published", "is_featured", "display_order"),
                name="home_speaker_featured_idx",
            ),
        ]

    def save(self, *args, **kwargs):
        """Create a readable profile URL slug when editors leave it blank."""
        if not self.slug:
            base_slug = slugify(self.full_name) or "speaker"
            candidate = base_slug
            suffix = 2
            while type(self).objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                candidate = f"{base_slug}-{suffix}"
                suffix += 1
            self.slug = candidate
        super().save(*args, **kwargs)

    def __str__(self):
        return self.full_name


class Announcement(models.Model):
    """A news item with explicit editorial publication and archive states."""

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        PUBLISHED = "published", _("Published")
        ARCHIVED = "archived", _("Archived")

    title = models.CharField(max_length=180)
    summary = models.CharField(max_length=360)
    published_at = models.DateTimeField(default=timezone.now, db_index=True)
    image = models.ImageField(
        upload_to="home/announcements/%Y/%m/",
        blank=True,
        validators=[validate_image_size],
    )
    destination_url = models.URLField(
        blank=True, validators=[URLValidator(schemes=["https"])]
    )
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-published_at", "-pk")
        verbose_name = _("Announcement")
        verbose_name_plural = _("Announcements")
        indexes = [
            models.Index(
                fields=("status", "published_at"),
                name="home_announcement_live_idx",
            ),
        ]

    def __str__(self):
        return self.title
