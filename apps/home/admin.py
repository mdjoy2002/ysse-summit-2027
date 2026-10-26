"""Admin interfaces for homepage content editors."""

from django.contrib import admin
from django.utils import timezone

from .models import Announcement, HomeHighlight, SummitEvent


@admin.register(SummitEvent)
class SummitEventAdmin(admin.ModelAdmin):
    """Allow staff to edit the single event configuration without duplicates."""

    fieldsets = (
        (
            "Event details",
            {
                "fields": (
                    "key",
                    "event_title",
                    "theme",
                    "description",
                    "subtitle",
                    "legacy_message",
                    "event_start_at",
                    "event_end_at",
                    "venue",
                    "status",
                )
            },
        ),
        ("Hero media", {"fields": ("hero_poster", "hero_video_url")}),
        ("Summit page banner", {"fields": ("banner_image",)}),
        (
            "Calls to action",
            {
                "fields": (
                    "register_cta_text",
                    "register_cta_url",
                    "explore_cta_text",
                    "explore_cta_url",
                    "registration_available",
                )
            },
        ),
    )
    readonly_fields = ("key",)

    def has_add_permission(self, request):
        return not SummitEvent.objects.exists() and super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(HomeHighlight)
class HomeHighlightAdmin(admin.ModelAdmin):
    """Manage the order and publication state of homepage highlights."""

    list_display = ("title", "icon", "sort_order", "is_published")
    list_editable = ("sort_order", "is_published")
    list_filter = ("is_published", "icon")
    search_fields = ("title", "description")


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    """Let authorized editors publish, unpublish, and archive announcements."""

    list_display = ("title", "status", "published_at", "updated_at")
    list_filter = ("status", "published_at")
    search_fields = ("title", "summary")
    date_hierarchy = "published_at"
    actions = (
        "publish_announcements",
        "unpublish_announcements",
        "archive_announcements",
    )
    fieldsets = (
        (None, {"fields": ("title", "summary", "image", "destination_url")}),
        ("Publication", {"fields": ("status", "published_at")}),
    )

    @admin.action(
        description="Publish selected announcements", permissions=["change"]
    )
    def publish_announcements(self, request, queryset):
        queryset.update(
            status=Announcement.Status.PUBLISHED, updated_at=timezone.now()
        )

    @admin.action(
        description="Move selected announcements to draft", permissions=["change"]
    )
    def unpublish_announcements(self, request, queryset):
        queryset.update(status=Announcement.Status.DRAFT, updated_at=timezone.now())

    @admin.action(description="Archive selected announcements", permissions=["change"])
    def archive_announcements(self, request, queryset):
        queryset.update(
            status=Announcement.Status.ARCHIVED, updated_at=timezone.now()
        )
