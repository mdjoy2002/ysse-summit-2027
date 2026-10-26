"""Speaker directory taxonomies; profiles reuse home.FeaturedSpeaker."""

from django.db import models
from django.utils.translation import gettext_lazy as _


class SpeakerCategory(models.Model):
    """An editable category that can be shared by multiple speaker profiles."""

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    description = models.CharField(max_length=240, blank=True)
    display_order = models.PositiveSmallIntegerField(default=0, db_index=True)
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ("display_order", "name")
        verbose_name = _("Speaker category")
        verbose_name_plural = _("Speaker categories")

    def __str__(self):
        return self.name
