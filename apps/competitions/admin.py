"""Permission-aware Django Admin for competitions and participant reviews."""

import csv

from django.contrib import admin
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from .models import Competition, CompetitionSubmission
from .services import mark_submission_reviewed


@admin.register(Competition)
class CompetitionAdmin(admin.ModelAdmin):
    """Manage publication, entry windows, and competition-specific rules."""

    list_display = (
        "name",
        "category",
        "status",
        "starts_at",
        "deadline",
        "is_published",
        "is_featured",
    )
    list_editable = ("status", "is_published", "is_featured")
    list_filter = ("status", "category", "is_published", "is_featured")
    search_fields = ("name", "short_description", "slug")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "deadline"
    fieldsets = (
        (
            "Public details",
            {
                "fields": (
                    "name",
                    "slug",
                    "short_description",
                    "detailed_description",
                    "category",
                    "submission_kind",
                    "cover_image",
                    "is_featured",
                    "is_published",
                )
            },
        ),
        (
            "Eligibility and rules",
            {
                "fields": (
                    "eligibility_criteria",
                    "rules_and_guidelines",
                    "submission_instructions",
                    "design_guidelines",
                    "article_topic_guidelines",
                    "hashtag_guidelines",
                    "rulebook_file",
                    "rulebook_url",
                    "debate_motions",
                )
            },
        ),
        (
            "Submission window",
            {
                "fields": (
                    "status",
                    "starts_at",
                    "deadline",
                    "allow_late_edits",
                    "participation_mode",
                    "minimum_team_size",
                    "maximum_team_size",
                )
            },
        ),
        (
            "File and content policy",
            {
                "fields": (
                    "allowed_file_extensions",
                    "max_upload_size_mb",
                    "require_file_upload",
                    "article_min_words",
                    "article_max_words",
                    "external_video_allowed",
                )
            },
        ),
        ("Audit", {"fields": ("created_at", "updated_at")}),
    )


def export_submissions_csv(modeladmin, request, queryset):
    """Export only operational submission metadata, not participant PII or content."""
    if not modeladmin.has_view_or_change_permission(request):
        return None

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="competition-submissions.csv"'
    response["X-Content-Type-Options"] = "nosniff"
    writer = csv.writer(response)
    writer.writerow(
        ("reference", "competition", "participant username", "status", "submitted at")
    )
    for submission in queryset.select_related("competition", "participant"):
        values = (
            str(submission.reference),
            submission.competition.name,
            submission.participant.get_username(),
            submission.get_status_display(),
            submission.submitted_at.isoformat() if submission.submitted_at else "",
        )
        writer.writerow(
            tuple(
                f"'{value}" if value.startswith(("=", "+", "-", "@")) else value
                for value in values
            )
        )
    return response


export_submissions_csv.short_description = "Export selected submissions to CSV"


@admin.register(CompetitionSubmission)
class CompetitionSubmissionAdmin(admin.ModelAdmin):
    """Allow explicit reviewers to review status and feedback, not edit entries."""

    list_display = (
        "reference",
        "competition",
        "participant",
        "status",
        "submitted_at",
        "reviewed_at",
    )
    list_filter = ("status", "competition", "submitted_at")
    search_fields = ("reference", "competition__name", "participant__username")
    date_hierarchy = "submitted_at"
    actions = (export_submissions_csv,)
    readonly_fields = (
        "reference",
        "competition",
        "participant",
        "data",
        "private_file_link",
        "submitted_at",
        "created_at",
        "updated_at",
        "reviewed_by",
        "reviewed_at",
    )
    fieldsets = (
        (
            "Submission",
            {
                "fields": (
                    "reference",
                    "competition",
                    "participant",
                    "status",
                    "data",
                    "private_file_link",
                    "submitted_at",
                    "created_at",
                    "updated_at",
                )
            },
        ),
        ("Reviewer feedback", {"fields": ("reviewer_notes", "reviewed_by", "reviewed_at")}),
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return super().has_view_permission(request, obj) or request.user.has_perm(
            "competitions.review_competitionsubmission"
        )

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) or request.user.has_perm(
            "competitions.review_competitionsubmission"
        )

    @admin.display(description="Private attachment")
    def private_file_link(self, obj):
        if not obj or not obj.attachment:
            return "No attachment"
        url = reverse(
            "competitions:submission-file",
            kwargs={"reference": obj.reference},
        )
        return format_html('<a href="{}">Download private file</a>', url)

    def save_model(self, request, obj, form, change):
        if obj.status == CompetitionSubmission.Status.DRAFT:
            obj.submitted_at = None
            obj.reviewed_by = None
            obj.reviewed_at = None
        else:
            if obj.submitted_at is None:
                obj.submitted_at = timezone.now()
            if change and (
                "status" in form.changed_data or "reviewer_notes" in form.changed_data
            ):
                mark_submission_reviewed(obj, request.user)
        super().save_model(request, obj, form, change)
