"""Django Admin for sponsor tiers, approved partners, and inquiries."""

from django.contrib import admin
from django.utils import timezone

from .models import PartnershipInquiry, PartnershipSettings, Sponsor, SponsorCategory


@admin.register(SponsorCategory)
class SponsorCategoryAdmin(admin.ModelAdmin):
    """Manage display names and ordering for sponsorship tiers."""

    list_display = ("name", "slug", "display_order", "is_published")
    list_editable = ("display_order", "is_published")
    list_filter = ("is_published",)
    search_fields = ("name", "description")
    prepopulated_fields = {"slug": ("name",)}
    ordering = ("display_order", "name")


@admin.register(Sponsor)
class SponsorAdmin(admin.ModelAdmin):
    """Manage approved sponsor profiles independently from inquiry status."""

    list_display = (
        "organization_name",
        "category",
        "website_url",
        "display_order",
        "is_published",
        "updated_at",
    )
    list_editable = ("display_order", "is_published")
    list_filter = ("category", "is_published")
    search_fields = ("organization_name", "description", "website_url")
    autocomplete_fields = ("category",)
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        ("Public sponsor profile", {"fields": ("organization_name", "logo", "description")}),
        ("Category and website", {"fields": ("category", "website_url")}),
        ("Publication", {"fields": ("display_order", "is_published")}),
        ("Audit timestamps", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(PartnershipSettings)
class PartnershipSettingsAdmin(admin.ModelAdmin):
    """Restrict sponsorship material changes to authorized Admin editors."""

    list_display = ("key", "updated_at")
    readonly_fields = ("key", "updated_at")
    fields = ("key", "sponsorship_deck", "inquiry_intro", "updated_at")

    def has_add_permission(self, request):
        return not PartnershipSettings.objects.exists() and super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PartnershipInquiry)
class PartnershipInquiryAdmin(admin.ModelAdmin):
    """Let authorized staff review inquiries without implying approval."""

    list_display = (
        "organization_name",
        "contact_name",
        "business_email",
        "interest",
        "status",
        "submitted_at",
        "reviewed_at",
    )
    list_filter = ("status", "interest", "submitted_at")
    search_fields = ("organization_name", "contact_name", "business_email", "message")
    date_hierarchy = "submitted_at"
    readonly_fields = ("submitted_at", "consent_given", "reviewed_at")
    fieldsets = (
        ("Inquiry", {"fields": ("organization_name", "contact_name", "business_email", "phone_number", "interest", "message", "consent_given", "submitted_at")}),
        ("Review", {"fields": ("status", "review_notes", "reviewed_at")}),
    )

    def save_model(self, request, obj, form, change):
        old_status = None
        if change:
            old_status = PartnershipInquiry.objects.only("status").get(pk=obj.pk).status
        if change and obj.status != old_status:
            obj.reviewed_at = timezone.now()
        super().save_model(request, obj, form, change)
