"""Admin interfaces for speaker profiles and categories."""

from django.contrib import admin

from apps.home.models import FeaturedSpeaker

from .models import SpeakerCategory


@admin.register(SpeakerCategory)
class SpeakerCategoryAdmin(admin.ModelAdmin):
    """Keep category names, publication, and directory order manageable."""

    list_display = ("name", "slug", "display_order", "is_published")
    list_editable = ("display_order", "is_published")
    list_filter = ("is_published",)
    search_fields = ("name", "description")
    prepopulated_fields = {"slug": ("name",)}
    ordering = ("display_order", "name")


@admin.register(FeaturedSpeaker)
class FeaturedSpeakerAdmin(admin.ModelAdmin):
    """Manage one speaker profile shared by homepage and directory."""

    list_display = (
        "full_name",
        "official_designation",
        "organization",
        "participation_status",
        "is_published",
        "is_featured",
        "display_order",
    )
    list_editable = (
        "participation_status",
        "is_published",
        "is_featured",
        "display_order",
    )
    list_filter = ("categories", "participation_status", "is_published", "is_featured")
    search_fields = ("full_name", "official_designation", "organization", "biography")
    prepopulated_fields = {"slug": ("full_name",)}
    filter_horizontal = ("categories",)
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (
            "Public profile",
            {
                "fields": (
                    "full_name",
                    "slug",
                    "official_designation",
                    "organization",
                    "biography",
                    "session_topic",
                    "categories",
                )
            },
        ),
        ("Profile media", {"fields": ("profile_image", "profile_url")}),
        (
            "Participation and publication",
            {
                "fields": (
                    "participation_status",
                    "is_published",
                    "is_featured",
                    "display_order",
                )
            },
        ),
        ("Audit timestamps", {"fields": ("created_at", "updated_at")}),
    )
