"""Transactional competition submission and access-control workflows."""

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import CompetitionSubmission


class DuplicateSubmissionError(Exception):
    """Raised when a concurrent request creates a second entry for one account."""


def can_review_submissions(user):
    """Review access requires an explicit model permission, not staff status alone."""
    return user.is_authenticated and (
        user.is_superuser
        or user.has_perm("competitions.view_competitionsubmission")
        or user.has_perm("competitions.change_competitionsubmission")
        or user.has_perm("competitions.review_competitionsubmission")
    )


@transaction.atomic
def save_submission(*, competition, participant, form, submit):
    """Create or update the one entry allowed per participant and competition."""
    now = timezone.now()
    existing = (
        CompetitionSubmission.objects.select_for_update()
        .filter(competition=competition, participant=participant)
        .first()
    )

    if existing is None:
        if not competition.submission_window_open(now):
            raise PermissionDenied("This competition is not accepting submissions.")
        existing = CompetitionSubmission(
            competition=competition,
            participant=participant,
        )
    elif not competition.is_editable_by_participants(now):
        raise PermissionDenied("The participant edit deadline has passed.")

    uploaded_file = form.cleaned_data.get("attachment")
    previous_file = existing.attachment.name if existing.attachment else ""
    existing.data = form.submission_data()
    if uploaded_file:
        existing.attachment = uploaded_file

    existing.status = (
        CompetitionSubmission.Status.SUBMITTED
        if submit
        else CompetitionSubmission.Status.DRAFT
    )
    existing.submitted_at = now if submit else None
    existing.reviewer_notes = ""
    existing.reviewed_by = None
    existing.reviewed_at = None

    try:
        with transaction.atomic():
            existing.save()
    except IntegrityError as error:
        if uploaded_file and existing.attachment:
            existing.attachment.storage.delete(existing.attachment.name)
        raise DuplicateSubmissionError(
            "A submission already exists for this participant and competition."
        ) from error

    if previous_file and uploaded_file:
        storage = existing.attachment.storage
        transaction.on_commit(lambda: storage.delete(previous_file))
    return existing


def mark_submission_reviewed(submission, reviewer):
    """Record reviewer attribution when a non-draft status is selected."""
    submission.reviewed_by = reviewer
    submission.reviewed_at = timezone.now()
