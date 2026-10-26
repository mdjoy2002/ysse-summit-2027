"""Competition-specific participant forms and validation."""

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from .models import Competition
from .validators import validate_submission_file


class CompetitionSubmissionForm(forms.Form):
    """Common upload and configured participation validation."""

    attachment = forms.FileField(
        required=False,
        label=_("Upload a file"),
        help_text=_("Accepted formats and file-size limits are set for each competition."),
    )

    def __init__(
        self,
        *args,
        competition,
        instance=None,
        is_submitting=False,
        **kwargs,
    ):
        self.competition = competition
        self.instance = instance
        self.is_submitting = is_submitting
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name != "attachment":
                field.required = is_submitting and field.required
        self.fields["attachment"].required = (
            is_submitting
            and competition.require_file_upload
            and not (instance and instance.attachment)
        )
        allowed = ", ".join(competition.allowed_file_extensions) or "none"
        self.fields["attachment"].help_text = _(
            "Accepted extensions: %(extensions)s. Maximum size: %(size)s MB."
        ) % {
            "extensions": allowed,
            "size": competition.max_upload_size_mb,
        }
        if competition.allowed_file_extensions:
            self.fields["attachment"].widget.attrs["accept"] = ",".join(
                f".{extension.lstrip('.')}"
                for extension in competition.allowed_file_extensions
            )

    def clean_attachment(self):
        """Verify every uploaded file against the competition's live policy."""
        uploaded_file = self.cleaned_data.get("attachment")
        if uploaded_file:
            validate_submission_file(
                uploaded_file,
                self.competition.allowed_file_extensions,
                self.competition.max_upload_size_mb,
            )
        return uploaded_file

    def clean(self):
        cleaned_data = super().clean()
        if (
            self.is_submitting
            and self.competition.require_file_upload
            and not cleaned_data.get("attachment")
            and not (self.instance and self.instance.attachment)
        ):
            self.add_error("attachment", _("A file upload is required to submit."))
        return cleaned_data

    def submission_data(self):
        """Return only form fields, never file bytes or request-only controls."""
        data = dict(self.cleaned_data)
        data.pop("attachment", None)
        if "team_members_text" in data:
            team_members = data.pop("team_members_text")
            data["team_members"] = (
                team_members
                if isinstance(team_members, list)
                else parse_team_members(
                    team_members, self.competition, False
                )
            )
        return data


def parse_team_members(value, competition, is_submitting):
    """Parse one team member per line as name | email | role."""
    members = []
    email_validator = forms.EmailField(required=False)
    for line_number, line in enumerate(value.splitlines(), start=1):
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split("|", maxsplit=2)]
        if not parts[0]:
            raise ValidationError(
                _("Team member on line %(line)s needs a name."),
                params={"line": line_number},
            )
        email = parts[1] if len(parts) > 1 else ""
        if email:
            try:
                email_validator.clean(email)
            except ValidationError as error:
                raise ValidationError(
                    _("Team member email on line %(line)s is invalid."),
                    params={"line": line_number},
                ) from error
        members.append(
            {
                "name": parts[0],
                "email": email,
                "role": parts[2] if len(parts) > 2 else "",
            }
        )

    if is_submitting:
        if len(members) < competition.minimum_team_size:
            raise ValidationError(
                _("Enter at least %(count)s team members."),
                params={"count": competition.minimum_team_size},
            )
        if competition.maximum_team_size and len(members) > competition.maximum_team_size:
            raise ValidationError(
                _("This competition allows no more than %(count)s team members."),
                params={"count": competition.maximum_team_size},
            )
    return members


class PitchSubmissionForm(CompetitionSubmissionForm):
    participation_type = forms.ChoiceField(
        choices=(), label=_("Participation type")
    )
    team_name = forms.CharField(max_length=120, required=False)
    team_members_text = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        label=_("Team members"),
        help_text=_("One member per line: name | email | role."),
    )
    idea_name = forms.CharField(max_length=160, label=_("Social business idea"))
    problem = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}))
    solution = forms.CharField(widget=forms.Textarea(attrs={"rows": 5}))
    impact_summary = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["participation_type"].choices = participation_choices(
            self.competition
        )
        if not self.is_submitting:
            self.fields["idea_name"].required = False
            self.fields["problem"].required = False
            self.fields["solution"].required = False
            self.fields["impact_summary"].required = False

    def clean(self):
        cleaned_data = super().clean()
        participation = cleaned_data.get("participation_type")
        if self.is_submitting and participation == Competition.ParticipationMode.TEAM:
            if not cleaned_data.get("team_name"):
                self.add_error("team_name", _("Enter the team name."))
            try:
                cleaned_data["team_members_text"] = parse_team_members(
                    cleaned_data.get("team_members_text", ""),
                    self.competition,
                    True,
                )
            except ValidationError as error:
                self.add_error("team_members_text", error)
        elif participation == Competition.ParticipationMode.INDIVIDUAL:
            cleaned_data["team_name"] = ""
            cleaned_data["team_members_text"] = []
        return cleaned_data

    def submission_data(self):
        data = super().submission_data()
        if "team_members_text" in self.cleaned_data:
            data["team_members"] = self.cleaned_data["team_members_text"]
        return data


class DebateSubmissionForm(CompetitionSubmissionForm):
    team_name = forms.CharField(max_length=120, label=_("Debate team name"))
    team_members_text = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 5}),
        label=_("Team member details"),
        help_text=_("One member per line: name | email | role."),
    )
    institution = forms.CharField(max_length=180, required=False)
    preferred_motion = forms.ChoiceField(
        choices=(), required=False, label=_("Preferred debate motion")
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        motions = self.competition.debate_motions
        choices = [("", _("Select a motion"))]
        if isinstance(motions, list):
            choices.extend((motion, motion) for motion in motions if isinstance(motion, str))
        self.fields["preferred_motion"].choices = choices

    def clean(self):
        cleaned_data = super().clean()
        if self.is_submitting:
            try:
                cleaned_data["team_members_text"] = parse_team_members(
                    cleaned_data.get("team_members_text", ""),
                    self.competition,
                    True,
                )
            except ValidationError as error:
                self.add_error("team_members_text", error)
        return cleaned_data

    def submission_data(self):
        data = super().submission_data()
        data["team_members"] = self.cleaned_data.get("team_members_text", [])
        return data


class PosterSubmissionForm(CompetitionSubmissionForm):
    poster_title = forms.CharField(max_length=180, label=_("Poster title"))
    abstract = forms.CharField(widget=forms.Textarea(attrs={"rows": 5}))


class ArticleSubmissionForm(CompetitionSubmissionForm):
    article_title = forms.CharField(max_length=180, label=_("Article title"))
    article_topic = forms.CharField(max_length=200, label=_("Article topic"))
    article_text = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 15}),
        label=_("Article"),
        help_text=_("Enter the article text here."),
    )

    def clean_article_text(self):
        text = self.cleaned_data.get("article_text", "")
        if self.is_submitting:
            word_count = len(text.split())
            minimum = self.competition.article_min_words
            maximum = self.competition.article_max_words
            if minimum and word_count < minimum:
                raise ValidationError(
                    _("The article must contain at least %(count)s words."),
                    params={"count": minimum},
                )
            if maximum and word_count > maximum:
                raise ValidationError(
                    _("The article must contain no more than %(count)s words."),
                    params={"count": maximum},
                )
        return text


class VideoSubmissionForm(CompetitionSubmissionForm):
    video_title = forms.CharField(max_length=180, label=_("Video title"))
    video_url = forms.URLField(
        required=False,
        validators=[URLValidator(schemes=["https"])],
        label=_("Approved external video link"),
    )
    hashtags = forms.CharField(
        required=False, max_length=300, label=_("Hashtags")
    )

    def clean(self):
        cleaned_data = super().clean()
        if not self.competition.external_video_allowed and cleaned_data.get("video_url"):
            self.add_error("video_url", _("External video links are not enabled."))
        if self.is_submitting:
            has_video_link = (
                self.competition.external_video_allowed
                and bool(cleaned_data.get("video_url"))
            )
            if not has_video_link and not cleaned_data.get("attachment"):
                raise ValidationError(
                    _("Provide an approved HTTPS video link or upload a video file.")
                )
        return cleaned_data


class AwardSubmissionForm(CompetitionSubmissionForm):
    class NominationType(models.TextChoices):
        SELF = "self", _("Self-nomination")
        PEER = "peer", _("Peer nomination")

    nomination_type = forms.ChoiceField(
        choices=NominationType.choices, label=_("Nomination type")
    )
    nominee_name = forms.CharField(max_length=160, label=_("Nominee name"))
    nominee_organization = forms.CharField(
        max_length=180, required=False, label=_("Organization")
    )
    achievements = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 6}), label=_("Achievements")
    )
    supporting_evidence = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 5}),
        label=_("Supporting evidence"),
    )


FORM_BY_KIND = {
    Competition.SubmissionKind.PITCH: PitchSubmissionForm,
    Competition.SubmissionKind.DEBATE: DebateSubmissionForm,
    Competition.SubmissionKind.POSTER: PosterSubmissionForm,
    Competition.SubmissionKind.ARTICLE: ArticleSubmissionForm,
    Competition.SubmissionKind.VIDEO: VideoSubmissionForm,
    Competition.SubmissionKind.AWARD: AwardSubmissionForm,
}


def participation_choices(competition):
    """Build valid individual/team choices from the competition configuration."""
    if competition.participation_mode == Competition.ParticipationMode.TEAM:
        return [(Competition.ParticipationMode.TEAM, _("Team"))]
    if competition.participation_mode == Competition.ParticipationMode.EITHER:
        return [
            (Competition.ParticipationMode.INDIVIDUAL, _("Individual")),
            (Competition.ParticipationMode.TEAM, _("Team")),
        ]
    return [(Competition.ParticipationMode.INDIVIDUAL, _("Individual"))]
