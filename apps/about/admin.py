"""Admin interfaces for editorially managing About YSSE content."""

from django.contrib import admin

from .models import (
    FounderVision,
    ImpactStatistic,
    TimelineMilestone,
    TimelinePeriod,
    YSSEWing,
)


class TimelineMilestoneInline(admin.TabularInline):
    """Edit the historical points alongside each period."""

    model = TimelineMilestone
    extra = 1
    fields = ("description", "sort_order")


@admin.register(TimelinePeriod)
class TimelinePeriodAdmin(admin.ModelAdmin):
    """Manage chronological history and its publication state."""

    list_display = ("start_year", "end_year", "title", "sort_order", "is_published")
    list_editable = ("sort_order", "is_published")
    list_filter = ("is_published", "start_year")
    search_fields = ("title", "milestones__description")
    ordering = ("sort_order", "start_year")
    inlines = (TimelineMilestoneInline,)


@admin.register(ImpactStatistic)
class ImpactStatisticAdmin(admin.ModelAdmin):
    """Keep proposed impact figures private until verified by an editor."""

    list_display = ("label", "value", "sort_order", "is_published")
    list_editable = ("value", "sort_order", "is_published")
    list_filter = ("is_published",)
    search_fields = ("label", "value")
    ordering = ("sort_order",)


@admin.register(FounderVision)
class FounderVisionAdmin(admin.ModelAdmin):
    """Edit the single founder message and control public visibility."""

    fieldsets = (
        (
            "Founder or President",
            {"fields": ("name", "designation", "profile_image")},
        ),
        (
            "Message",
            {
                "fields": (
                    "written_message",
                    "video_message_url",
                    "video_thumbnail",
                )
            },
        ),
        ("Publication", {"fields": ("key", "is_published", "updated_at")}),
    )
    readonly_fields = ("key", "updated_at")
    list_display = ("name", "designation", "is_published", "updated_at")
    list_filter = ("is_published",)
    search_fields = ("name", "designation", "written_message")

    def has_add_permission(self, request):
        return not FounderVision.objects.exists() and super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(YSSEWing)
class YSSEWingAdmin(admin.ModelAdmin):
    """Manage YSSE initiative card copy, ordering, and publication."""

    list_display = ("name", "sort_order", "is_published")
    list_editable = ("sort_order", "is_published")
    list_filter = ("is_published",)
    search_fields = ("name", "description")
    ordering = ("sort_order", "name")
