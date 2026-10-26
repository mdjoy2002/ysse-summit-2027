"""Database-backed public contact, FAQs, policies, press, and subscribers."""

import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .validators import (
    validate_optional_https_url,
    validate_press_attachment_content,
    validate_press_attachment_extension,
    validate_press_cover,
    private_press_storage,
)


class PublicInformationSettings(models.Model):
    """Singleton settings for official contact details and social profiles."""

    class Key(models.TextChoices):
        MAIN = "main", _("Main public information")

    key = models.CharField(
        max_length=20, choices=Key.choices, default=Key.MAIN, unique=True
    )
    official_email = models.EmailField(default="ysse.hrd@gmail.com")
    hotline_numbers = models.CharField(
        max_length=300,
        blank=True,
        help_text=_("Optional public hotline numbers, separated by commas."),
    )
    office_address = models.CharField(max_length=500, blank=True)
    facebook_url = models.URLField(blank=True, validators=[validate_optional_https_url])
    linkedin_url = models.URLField(blank=True, validators=[validate_optional_https_url])
    youtube_url = models.URLField(blank=True, validators=[validate_optional_https_url])
    instagram_url = models.URLField(blank=True, validators=[validate_optional_https_url])
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Public information settings")
        verbose_name_plural = _("Public information settings")

    def save(self, *args, **kwargs):
        """Keep the public contact/social configuration to one row."""
        self.key = self.Key.MAIN
        super().save(*args, **kwargs)

    @property
    def social_links(self):
        """Return only explicitly configured official social profiles."""
        return [
            (label, getattr(self, field))
            for label, field in (
                ("Facebook", "facebook_url"),
                ("LinkedIn", "linkedin_url"),
                ("YouTube", "youtube_url"),
                ("Instagram", "instagram_url"),
            )
            if getattr(self, field)
        ]

    def __str__(self):
        return "Public information settings"


class ContactInquiry(models.Model):
    """Private visitor inquiry awaiting authorized staff review."""

    class Status(models.TextChoices):
        NEW = "new", _("New")
        IN_REVIEW = "in_review", _("In review")
        RESPONDED = "responded", _("Responded")
        CLOSED = "closed", _("Closed")

    name = models.CharField(max_length=120)
    email = models.EmailField(max_length=254)
    subject = models.CharField(max_length=180)
    message = models.TextField(max_length=4000)
    consent_given = models.BooleanField()
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.NEW, db_index=True
    )
    submitted_at = models.DateTimeField(default=timezone.now, db_index=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

    class Meta:
        ordering = ("-submitted_at", "-pk")
        indexes = [
            models.Index(fields=("status", "submitted_at"), name="contact_status_time_idx")
        ]
        verbose_name = _("Contact inquiry")
        verbose_name_plural = _("Contact inquiries")

    def clean(self):
        super().clean()
        if not self.consent_given:
            raise ValidationError(
                {"consent_given": _("Consent is required to submit an inquiry.")}
            )

    def __str__(self):
        return f"{self.subject} — {self.name}"


class FAQ(models.Model):
    """An ordered, optionally published answer to a common question."""

    class Category(models.TextChoices):
        GENERAL = "general", _("General")
        REGISTRATION = "registration", _("Registration and tickets")
        COMPETITIONS = "competitions", _("Competitions")
        EVENT = "event", _("Event information")
        PARTNERSHIP = "partnership", _("Partnerships")

    question = models.CharField(max_length=240)
    answer = models.TextField(max_length=5000)
    category = models.CharField(
        max_length=20, choices=Category.choices, default=Category.GENERAL, db_index=True
    )
    display_order = models.PositiveSmallIntegerField(default=0, db_index=True)
    is_published = models.BooleanField(default=False, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("category", "display_order", "pk")
        verbose_name = _("Frequently asked question")
        verbose_name_plural = _("Frequently asked questions")

    def __str__(self):
        return self.question


class LegalDocument(models.Model):
    """Editable policy copy that stays private until reviewed and published."""

    class Kind(models.TextChoices):
        PRIVACY = "privacy", _("Privacy policy")
        TERMS = "terms", _("Terms and conditions")

    kind = models.CharField(max_length=12, choices=Kind.choices, unique=True)
    title = models.CharField(max_length=160)
    content = models.TextField()
    organizer_reviewed = models.BooleanField(
        default=False,
        help_text=_("Mark only after the organizer has reviewed the text."),
    )
    is_published = models.BooleanField(default=False, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("kind",)
        verbose_name = _("Privacy or terms page")
        verbose_name_plural = _("Privacy and terms pages")

    def clean(self):
        super().clean()
        if self.is_published and not self.organizer_reviewed:
            raise ValidationError(
                {"organizer_reviewed": _("Review the document before publication.")}
            )

    def __str__(self):
        return self.title


class PressRelease(models.Model):
    """A public announcement that remains hidden until explicitly published."""

    title = models.CharField(max_length=220)
    slug = models.SlugField(max_length=240, unique=True)
    publication_date = models.DateField(default=timezone.localdate, db_index=True)
    content = models.TextField()
    cover_image = models.ImageField(
        upload_to="contact/press/covers/%Y/%m/",
        blank=True,
        storage=private_press_storage,
        validators=[validate_press_cover],
    )
    attachment = models.FileField(
        upload_to="contact/press/attachments/%Y/%m/",
        blank=True,
        storage=private_press_storage,
        validators=[
            validate_press_attachment_extension,
            validate_press_attachment_content,
        ],
    )
    is_published = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-publication_date", "-pk")
        indexes = [
            models.Index(
                fields=("is_published", "publication_date"),
                name="press_public_date_idx",
            )
        ]
        verbose_name = _("Press release")
        verbose_name_plural = _("Press releases")

    def __str__(self):
        return self.title


class NewsletterSubscription(models.Model):
    """Private email subscription with explicit confirmation and unsubscribe."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Awaiting confirmation")
        ACTIVE = "active", _("Subscribed")
        UNSUBSCRIBED = "unsubscribed", _("Unsubscribed")

    email = models.EmailField(max_length=254, unique=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    consent_given = models.BooleanField()
    confirmation_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    unsubscribe_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    subscribed_at = models.DateTimeField(default=timezone.now)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    unsubscribed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-subscribed_at", "-pk")
        indexes = [
            models.Index(fields=("status", "subscribed_at"), name="newsletter_status_time_idx")
        ]
        verbose_name = _("Newsletter subscriber")
        verbose_name_plural = _("Newsletter subscribers")

    def clean(self):
        super().clean()
        if not self.consent_given:
            raise ValidationError(
                {"consent_given": _("Consent is required to subscribe.")}
            )

    def __str__(self):
        return f"{self.email} — {self.get_status_display()}"
