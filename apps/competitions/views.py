"""Public competition pages and private participant submission views."""

import os
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .forms import FORM_BY_KIND
from .models import Competition, CompetitionSubmission
from .services import (
    DuplicateSubmissionError,
    can_review_submissions,
    save_submission,
)


def competition_list(request):
    """Show published competitions without leaking draft administration data."""
    competitions = Competition.objects.filter(is_published=True).order_by(
        "-is_featured", "name"
    )
    return render(
        request,
        "competitions/list.html",
        {"competitions": competitions},
    )


def competition_detail(request, slug):
    """Render public rules, current dates, and this competition's submission state."""
    competition = get_object_or_404(Competition, slug=slug, is_published=True)
    submission = None
    can_submit = competition.submission_window_open()
    if request.user.is_authenticated:
        submission = CompetitionSubmission.objects.filter(
            competition=competition, participant=request.user
        ).first()
        if submission and competition.is_editable_by_participants():
            can_submit = True
    return render(
        request,
        "competitions/detail.html",
        {
            "competition": competition,
            "submission": submission,
            "can_submit": can_submit,
        },
    )


def _initial_submission_data(submission):
    """Prepare stored team data for the line-based team member form control."""
    if not submission:
        return {}
    initial = dict(submission.data)
    members = initial.pop("team_members", [])
    if members:
        initial["team_members_text"] = "\n".join(
            " | ".join(
                value
                for value in (member.get("name", ""), member.get("email", ""), member.get("role", ""))
            ).rstrip(" |")
            for member in members
        )
    return initial


@login_required
def submit_competition(request, slug):
    """Save a participant's draft or final entry while enforcing its deadline."""
    competition = get_object_or_404(Competition, slug=slug, is_published=True)
    submission = CompetitionSubmission.objects.filter(
        competition=competition, participant=request.user
    ).first()
    now = timezone.now()
    editable = (
        competition.submission_window_open(now)
        if submission is None
        else competition.is_editable_by_participants(now)
    )
    if not editable:
        messages.error(request, "This competition is not accepting changes right now.")
        return redirect("competitions:detail", slug=competition.slug)

    form_class = FORM_BY_KIND[competition.submission_kind]
    is_submitting = request.method == "POST" and request.POST.get("action") == "submit"
    form_kwargs = {
        "competition": competition,
        "instance": submission,
        "is_submitting": is_submitting,
    }
    if request.method == "POST":
        form = form_class(request.POST, request.FILES, **form_kwargs)
        if form.is_valid():
            try:
                saved = save_submission(
                    competition=competition,
                    participant=request.user,
                    form=form,
                    submit=is_submitting,
                )
            except DuplicateSubmissionError:
                messages.error(
                    request,
                    "A submission already exists for this account and competition.",
                )
            except PermissionDenied:
                messages.error(request, "The submission window has closed.")
                return redirect("competitions:detail", slug=competition.slug)
            else:
                messages.success(
                    request,
                    "Your submission has been submitted."
                    if is_submitting
                    else "Your draft has been saved.",
                )
                return redirect(
                    "competitions:submission-detail",
                    reference=saved.reference,
                )
    else:
        form = form_class(
            initial=_initial_submission_data(submission),
            **form_kwargs,
        )

    return render(
        request,
        "competitions/submit.html",
        {
            "competition": competition,
            "form": form,
            "submission": submission,
            "is_editing": submission is not None,
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
def my_submissions(request):
    """Show only the authenticated participant's private submission history."""
    submissions = CompetitionSubmission.objects.filter(
        participant=request.user
    ).select_related("competition")
    return render(
        request,
        "competitions/my_submissions.html",
        {"submissions": submissions},
    )


@login_required
def submission_detail(request, reference):
    """Show an entry only to its owner or an explicitly authorized reviewer."""
    submission = get_object_or_404(
        CompetitionSubmission.objects.select_related("competition", "participant"),
        reference=reference,
    )
    if (
        submission.participant_id != request.user.pk
        and not can_review_submissions(request.user)
    ):
        raise Http404
    submission_fields = [
        {
            "label": key.replace("_", " ").title(),
            "value": json.dumps(value, ensure_ascii=False)
            if isinstance(value, (list, dict))
            else value,
        }
        for key, value in submission.data.items()
    ]
    can_edit = (
        submission.participant_id == request.user.pk
        and submission.competition.is_editable_by_participants()
    )
    return render(
        request,
        "competitions/submission_detail.html",
        {
            "submission": submission,
            "submission_fields": submission_fields,
            "can_edit": can_edit,
        },
    )


@login_required
def submission_file(request, reference):
    """Stream private uploads only to their owner or authorized competition staff."""
    submission = get_object_or_404(
        CompetitionSubmission.objects.select_related("competition", "participant"),
        reference=reference,
    )
    if (
        submission.participant_id != request.user.pk
        and not can_review_submissions(request.user)
    ):
        raise Http404
    if not submission.attachment:
        raise Http404

    filename = os.path.basename(submission.attachment.name)
    response = FileResponse(
        submission.attachment.open("rb"),
        as_attachment=True,
        filename=filename,
        content_type="application/octet-stream",
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


def rulebook_download(request, slug):
    """Serve a public rulebook as an attachment or redirect to its HTTPS URL."""
    competition = get_object_or_404(Competition, slug=slug, is_published=True)
    if competition.rulebook_file:
        response = FileResponse(
            competition.rulebook_file.open("rb"),
            as_attachment=True,
            filename=f"{competition.slug}-rulebook.pdf",
            content_type="application/pdf",
        )
        response["X-Content-Type-Options"] = "nosniff"
        return response
    if competition.rulebook_url:
        return redirect(competition.rulebook_url)
    raise Http404("No rulebook is available for this competition.")
