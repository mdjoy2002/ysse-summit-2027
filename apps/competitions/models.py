"""Competition catalog and participant submission data models."""

import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import (
    MaxValueValidator,
    MinValueValidator,
    URLValidator,
)
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.home.validators import validate_image_size

from .storage import PrivateSubmissionStorage
from .validators import validate_rulebook_file


class Competition(models.Model):
    """Editable public details and per-competition submission policy."""

    class Category(models.TextChoices):
        PITCH = "impact-pitch", _("National Social Business Pitch")
        DEBATE = "youth-debate", _("Youth Debate Challenge")
        POSTER = "poster-presentation", _("Poster Presentation")
        ARTICLE = "article-contest", _("Article Contest")
        VIDEO = "short-video", _("Short Video / Reels Contest")
        AWARD = "changemaker-award", _("National Excellence Award")

    class SubmissionKind(models.TextChoices):
        PITCH = "pitch", _("Pitch deck")
        DEBATE = "debate", _("Team debate")
        POSTER = "poster", _("Poster presentation")
        ARTICLE = "article", _("Article")
        VIDEO = "video", _("Short video")
        AWARD = "award", _("Changemaker award")

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        UPCOMING = "upcoming", _("Upcoming")
        OPEN = "open", _("Open for submissions")
        CLOSED = "closed", _("Closed")
        COMPLETED = "completed", _("Completed")

    class ParticipationMode(models.TextChoices):
        INDIVIDUAL = "individual", _("Individual")
        TEAM = "team", _("Team")
        EITHER = "either", _("Individual or team")

    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180, unique=True)
    short_description = models.CharField(max_length=240)
    detailed_description = models.TextField(blank=True)
    category = models.CharField(max_length=32, choices=Category.choices, unique=True)
    submission_kind = models.CharField(
        max_length=12, choices=SubmissionKind.choices, unique=True
    )
    cover_image = models.ImageField(
        upload_to="competitions/covers/%Y/%m/",
        blank=True,
        validators=[validate_image_size],
    )
    eligibility_criteria = models.TextField(blank=True)
    rules_and_guidelines = models.TextField(blank=True)
    submission_instructions = models.TextField(blank=True)
    design_guidelines = models.TextField(blank=True)
    article_topic_guidelines = models.TextField(blank=True)
    hashtag_guidelines = models.TextField(blank=True)
    rulebook_file = models.FileField(
        upload_to="competitions/rulebooks/%Y/%m/",
        blank=True,
        validators=[validate_rulebook_file],
    )
    rulebook_url = models.URLField(
        blank=True, validators=[URLValidator(schemes=["https"])]
    )
    starts_at = models.DateTimeField(null=True, blank=True)
    deadline = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    is_featured = models.BooleanField(default=False, db_index=True)
    is_published = models.BooleanField(default=False, db_index=True)
    allow_late_edits = models.BooleanField(default=False)
    participation_mode = models.CharField(
        max_length=12,
        choices=ParticipationMode.choices,
        default=ParticipationMode.INDIVIDUAL,
    )
    minimum_team_size = models.PositiveSmallIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(100)]
    )
    maximum_team_size = models.PositiveSmallIntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text=_("Set to 0 for no configured maximum."),
    )
    debate_motions = models.JSONField(default=list, blank=True)
    allowed_file_extensions = models.JSONField(default=list, blank=True)
    max_upload_size_mb = models.PositiveSmallIntegerField(
        default=10,
        validators=[MinValueValidator(1), MaxValueValidator(100)],
        help_text=_("Maximum size per participant upload."),
    )
    require_file_upload = models.BooleanField(default=False)
    article_min_words = models.PositiveSmallIntegerField(default=0)
    article_max_words = models.PositiveSmallIntegerField(default=0)
    external_video_allowed = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        indexes = [
            models.Index(
                fields=("is_published", "status", "is_featured", "name"),
                name="competition_public_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(starts_at__isnull=True)
                    | Q(deadline__isnull=True)
                    | Q(deadline__gt=F("starts_at"))
                ),
                name="competition_deadline_after_start",
            ),
            models.CheckConstraint(
                condition=Q(maximum_team_size=0)
                | Q(maximum_team_size__gte=F("minimum_team_size")),
                name="competition_team_size_range",
            ),
            models.CheckConstraint(
                condition=Q(article_max_words=0)
                | Q(article_min_words__lte=F("article_max_words")),
                name="competition_article_word_range",
            ),
        ]

    def clean(self):
        """Reject malformed file policies and category-specific configuration."""
        super().clean()
        errors = {}
        if not isinstance(self.allowed_file_extensions, list):
            errors["allowed_file_extensions"] = _(
                "Accepted extensions must be entered as a JSON list."
            )
        else:
            normalized = [
                str(ext).lower().lstrip(".")
                for ext in self.allowed_file_extensions
            ]
            if len(normalized) != len(set(normalized)):
                errors["allowed_file_extensions"] = _("File extensions must be unique.")
            if not set(normalized).issubset(
                {"pdf", "png", "jpg", "jpeg", "docx", "pptx", "mp4", "webm"}
            ):
                errors["allowed_file_extensions"] = _(
                    "One or more file extensions are not supported."
                )
        if self.participation_mode == self.ParticipationMode.INDIVIDUAL:
            if self.minimum_team_size != 1 or self.maximum_team_size:
                errors["maximum_team_size"] = _(
                    "Individual competitions must use the default team-size settings."
                )
        if self.article_max_words and self.article_min_words > self.article_max_words:
            errors["article_max_words"] = _(
                "Maximum word count must not be below minimum."
            )
        if self.status == self.Status.OPEN and (
            self.starts_at is None or self.deadline is None
        ):
            errors["deadline"] = _(
                "Set both the opening time and deadline before opening submissions."
            )
        if (
            self.status == self.Status.OPEN
            and self.require_file_upload
            and not self.allowed_file_extensions
        ):
            errors["allowed_file_extensions"] = _(
                "Configure accepted extensions before requiring uploads."
            )
        if (
            self.status == self.Status.OPEN
            and self.submission_kind == self.SubmissionKind.VIDEO
            and not self.external_video_allowed
            and not self.allowed_file_extensions
        ):
            errors["allowed_file_extensions"] = _(
                "Configure video file extensions before disabling external links."
            )
        if errors:
            raise ValidationError(errors)

    def submission_window_open(self, at=None):
        """Return true only while both configured bounds allow submissions."""
        current_time = at or timezone.now()
        return (
            self.status == self.Status.OPEN
            and self.starts_at is not None
            and self.deadline is not None
            and self.starts_at <= current_time <= self.deadline
        )

    def is_editable_by_participants(self, at=None):
        """Allow late changes only when an administrator explicitly enables them."""
        current_time = at or timezone.now()
        if self.submission_window_open(current_time):
            return True
        return (
            self.allow_late_edits
            and self.status in {self.Status.OPEN, self.Status.CLOSED}
            and self.deadline is not None
            and current_time > self.deadline
        )

    def __str__(self):
        return self.name


def submission_upload_path(instance, original_name):
    """Use only the validated suffix and random IDs; never retain user filenames."""
    suffix = Path(original_name).suffix.lower()
    return (
        f"competition-submissions/{instance.reference.hex}/"
        f"{uuid.uuid4().hex}{suffix}"
    )


class CompetitionSubmission(models.Model):
    """A private, single participant entry for one competition."""

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        SUBMITTED = "submitted", _("Submitted")
        UNDER_REVIEW = "under_review", _("Under review")
        ACCEPTED = "accepted", _("Accepted")
        REJECTED = "rejected", _("Not selected")

    reference = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    competition = models.ForeignKey(
        Competition, on_delete=models.PROTECT, related_name="submissions"
    )
    participant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="competition_submissions",
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    data = models.JSONField(default=dict, blank=True)
    attachment = models.FileField(
        upload_to=submission_upload_path,
        storage=PrivateSubmissionStorage(),
        blank=True,
    )
    submitted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    reviewer_notes = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_competition_submissions",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("competition", "participant"),
                name="one_submission_per_competition_participant",
            ),
            models.CheckConstraint(
                condition=Q(status="draft") | Q(submitted_at__isnull=False),
                name="submitted_entry_has_timestamp",
            ),
        ]
        indexes = [
            models.Index(
                fields=("competition", "status", "submitted_at"),
                name="submission_review_idx",
            ),
            models.Index(
                fields=("participant", "updated_at"), name="submission_owner_idx"
            ),
        ]
        permissions = [
            ("review_competitionsubmission", _("Can review competition submissions")),
        ]

    def clean(self):
        """Ensure non-draft entries have a submission timestamp."""
        super().clean()
        if self.status != self.Status.DRAFT and self.submitted_at is None:
            raise ValidationError(
                {"submitted_at": _("Submitted entries require a submission timestamp.")}
            )

    @property
    def is_finalized(self):
        return self.status != self.Status.DRAFT

    def __str__(self):
        return f"{self.competition.name}: {self.reference}"
