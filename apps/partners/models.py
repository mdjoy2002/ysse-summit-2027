"""Sponsor tiers, public partner profiles, and private inquiry records."""

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.home.validators import validate_image_size

from .validators import (
    validate_sponsor_deck_extension,
    validate_sponsor_website,
    validate_sponsor_logo,
    validate_sponsorship_deck,
)


class SponsorCategory(models.Model):
    """An editable, ordered sponsor or partnership tier."""

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    description = models.CharField(max_length=240, blank=True)
    display_order = models.PositiveSmallIntegerField(default=0, db_index=True)
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ("display_order", "name")
        verbose_name = _("Sponsor category")
        verbose_name_plural = _("Sponsor categories")

    def __str__(self):
        return self.name


class Sponsor(models.Model):
    """An organization approved for public partner/sponsor recognition."""

    organization_name = models.CharField(max_length=160)
    logo = models.ImageField(
        upload_to="partners/logos/%Y/%m/",
        validators=[validate_image_size, validate_sponsor_logo],
    )
    description = models.TextField(blank=True)
    category = models.ForeignKey(
        SponsorCategory,
        related_name="sponsors",
        on_delete=models.PROTECT,
    )
    website_url = models.URLField(
        blank=True,
        validators=[validate_sponsor_website],
    )
    display_order = models.PositiveSmallIntegerField(default=0, db_index=True)
    is_published = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("category__display_order", "display_order", "organization_name")
        indexes = [
            models.Index(
                fields=("is_published", "category", "display_order"),
                name="partner_public_order_idx",
            ),
        ]
        verbose_name = _("Sponsor or partner")
        verbose_name_plural = _("Sponsors and partners")

    def clean(self):
        """Validate external websites as HTTPS URLs."""
        super().clean()
        if self.website_url:
            validate_sponsor_website(self.website_url)

    def __str__(self):
        return self.organization_name


class PartnershipSettings(models.Model):
    """Singleton managed sponsorship materials and public inquiry guidance."""

    class Key(models.TextChoices):
        MAIN = "main", _("Main partnership settings")

    key = models.CharField(
        max_length=20,
        choices=Key.choices,
        default=Key.MAIN,
        unique=True,
    )
    sponsorship_deck = models.FileField(
        upload_to="partners/decks/%Y/%m/",
        blank=True,
        validators=[validate_sponsor_deck_extension, validate_sponsorship_deck],
    )
    inquiry_intro = models.CharField(
        max_length=240,
        blank=True,
        default="Tell us how your organization would like to support the summit.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Partnership settings")
        verbose_name_plural = _("Partnership settings")

    def __str__(self):
        return "Partnership settings"

    def save(self, *args, **kwargs):
        """Keep one managed deck/configuration row."""
        self.key = self.Key.MAIN
        super().save(*args, **kwargs)


class PartnershipInquiry(models.Model):
    """A private, unapproved request for the partnership team to review."""

    class Interest(models.TextChoices):
        TITLE = "title", _("Title sponsor")
        CO_SPONSOR = "co-sponsor", _("Co-sponsorship")
        PLATINUM = "platinum", _("Platinum sponsorship")
        GOLD = "gold", _("Gold sponsorship")
        STRATEGIC = "strategic", _("Strategic partnership")
        MEDIA = "media", _("Media partnership")
        OTHER = "other", _("Other partnership")

    class Status(models.TextChoices):
        NEW = "new", _("New")
        IN_REVIEW = "in_review", _("In review")
        CONTACTED = "contacted", _("Contacted")
        DECLINED = "declined", _("Declined")
        APPROVED = "approved", _("Approved partnership")

    organization_name = models.CharField(max_length=160)
    contact_name = models.CharField(max_length=120)
    business_email = models.EmailField(max_length=254)
    phone_number = models.CharField(max_length=32, blank=True)
    interest = models.CharField(max_length=16, choices=Interest.choices)
    message = models.TextField(max_length=3000)
    consent_given = models.BooleanField()
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.NEW,
        db_index=True,
    )
    submitted_at = models.DateTimeField(default=timezone.now, db_index=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

    class Meta:
        ordering = ("-submitted_at", "-pk")
        indexes = [
            models.Index(
                fields=("status", "submitted_at"),
                name="partner_inquiry_status_idx",
            ),
        ]
        verbose_name = _("Partnership inquiry")
        verbose_name_plural = _("Partnership inquiries")

    def clean(self):
        """Require an explicit privacy consent for contact follow-up."""
        super().clean()
        if not self.consent_given:
            raise ValidationError(
                {"consent_given": _("Consent is required to submit an inquiry.")}
            )

    def __str__(self):
        return f"{self.organization_name} — {self.get_interest_display()}"
