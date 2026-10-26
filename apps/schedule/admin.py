"""Django Admin configuration for agenda editors."""

from django.contrib import admin

from .models import ScheduleSession


@admin.register(ScheduleSession)
class ScheduleSessionAdmin(admin.ModelAdmin):
    """Support authorized schedule editing, ordering, and publication."""

    list_display = (
        "title",
        "event",
        "starts_at",
        "ends_at",
        "venue",
        "speaker",
        "competition",
        "display_order",
        "is_published",
    )
    list_editable = ("display_order", "is_published")
    list_filter = ("event", "is_published", "venue", "starts_at")
    search_fields = ("title", "description", "venue", "speaker__full_name", "competition__name")
    autocomplete_fields = ("speaker", "competition")
    date_hierarchy = "starts_at"
    ordering = ("starts_at", "display_order")
    fieldsets = (
        ("Session details", {"fields": ("event", "title", "description")}),
        ("Timing and location", {"fields": ("starts_at", "ends_at", "venue")}),
        ("Related people and competitions", {"fields": ("speaker", "competition")}),
        ("Display", {"fields": ("display_order", "is_published")}),
    )
