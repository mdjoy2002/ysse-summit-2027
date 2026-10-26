"""Regression tests for competition publication, forms, security, and review."""

from datetime import timedelta
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import (
    FORM_BY_KIND,
    ArticleSubmissionForm,
    AwardSubmissionForm,
    DebateSubmissionForm,
    PitchSubmissionForm,
    PosterSubmissionForm,
    VideoSubmissionForm,
)
from .models import Competition, CompetitionSubmission
from .validators import validate_submission_file


PDF_CONTENT = b"%PDF-1.4\nYSSE test document\n%%EOF"
PNG_CONTENT = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def sample_upload(name="entry.pdf", content=PDF_CONTENT):
    return SimpleUploadedFile(name, content, content_type="application/octet-stream")


class CompetitionCatalogTests(TestCase):
    """Verify seed records and public catalog/detail behavior."""

    def test_all_six_competitions_are_seeded_as_distinct_published_records(self):
        expected = {
            "Impact 2027": "National Social Business Pitch",
            "The Big Debates": "Youth Debate Challenge",
            "Vision to Impact": "Poster Presentation",
            "Words of Impact": "Article Contest for YSSE Magazine",
            "Lens of Change": "Short Video / Reels Contest",
            "Youth Changemaker Award 2027": "National Excellence Award",
        }
        competitions = Competition.objects.filter(is_published=True)

        self.assertEqual(competitions.count(), 6)
        for name, description in expected.items():
            with self.subTest(competition=name):
                competition = competitions.get(name=name)
                self.assertEqual(competition.short_description, description)
                self.assertEqual(competition.status, Competition.Status.UPCOMING)
                self.assertIsNone(competition.starts_at)
                self.assertIsNone(competition.deadline)

    def test_catalog_and_each_competition_details_render(self):
        response = self.client.get(reverse("competitions:list"))

        self.assertEqual(response.status_code, 200)
        for competition in Competition.objects.all():
            self.assertContains(response, competition.name)
            detail = self.client.get(
                reverse("competitions:detail", args=(competition.slug,))
            )
            self.assertEqual(detail.status_code, 200)
            self.assertContains(detail, competition.short_description)

    def test_unpublished_competition_is_not_public(self):
        competition = Competition.objects.get(slug="impact-2027")
        competition.is_published = False
        competition.save()

        self.assertNotContains(
            self.client.get(reverse("competitions:list")),
            competition.name,
        )
        self.assertEqual(
            self.client.get(
                reverse("competitions:detail", args=(competition.slug,))
            ).status_code,
            404,
        )

    def test_empty_catalog_has_a_professional_empty_state(self):
        Competition.objects.update(is_published=False)

        response = self.client.get(reverse("competitions:list"))

        self.assertContains(response, "Competition details are being prepared.")

    def test_published_card_uses_media_fallback_when_cover_is_missing(self):
        response = self.client.get(reverse("competitions:list"))

        self.assertContains(response, "competition-cover-placeholder")
        self.assertNotContains(response, 'src=""')

    def test_lens_leaderboard_is_disabled_without_a_data_source(self):
        response = self.client.get(
            reverse("competitions:detail", args=("lens-of-change",))
        )

        self.assertContains(response, "Leaderboard unavailable")
        self.assertContains(response, "No verified scoring or engagement data source")
        self.assertNotContains(response, "votes received")


class CompetitionPolicyTests(TestCase):
    """Exercise configurable entry windows, team rules, and database constraints."""

    def setUp(self):
        self.competition = Competition.objects.get(slug="impact-2027")
        now = timezone.now()
        self.competition.status = Competition.Status.OPEN
        self.competition.starts_at = now - timedelta(hours=1)
        self.competition.deadline = now + timedelta(days=2)
        self.competition.save()

    def test_competition_is_open_only_inside_aware_configured_window(self):
        self.assertTrue(self.competition.submission_window_open())
        self.assertFalse(
            self.competition.submission_window_open(
                self.competition.starts_at - timedelta(seconds=1)
            )
        )
        self.assertFalse(
            self.competition.submission_window_open(
                self.competition.deadline + timedelta(seconds=1)
            )
        )

    def test_admin_cannot_open_competition_without_window_or_required_file_policy(self):
        self.competition.deadline = None
        with self.assertRaises(ValidationError):
            self.competition.full_clean()

        self.competition.deadline = timezone.now() + timedelta(days=1)
        self.competition.allowed_file_extensions = []
        with self.assertRaises(ValidationError) as error:
            self.competition.full_clean()
        self.assertIn("allowed_file_extensions", error.exception.message_dict)

    def test_database_rejects_deadline_before_start(self):
        self.competition.starts_at = timezone.now() + timedelta(days=2)
        self.competition.deadline = timezone.now() + timedelta(days=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.competition.save()

    def test_database_rejects_duplicate_competition_submission(self):
        user = get_user_model().objects.create_user(username="duplicate")
        CompetitionSubmission.objects.create(
            competition=self.competition,
            participant=user,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            CompetitionSubmission.objects.create(
                competition=self.competition,
                participant=user,
            )

    def test_late_edits_require_explicit_admin_configuration(self):
        now = timezone.now()
        self.competition.deadline = now - timedelta(minutes=1)
        self.assertFalse(self.competition.is_editable_by_participants(now))

        self.competition.allow_late_edits = True
        self.assertTrue(self.competition.is_editable_by_participants(now))


class CompetitionFileValidationTests(TestCase):
    """Reject disallowed extensions, oversized data, and forged MIME metadata."""

    def test_accepts_matching_pdf_and_png_signatures(self):
        validate_submission_file(sample_upload(), ["pdf"], 1)
        validate_submission_file(
            sample_upload("artwork.png", PNG_CONTENT),
            ["png"],
            1,
        )

    def test_rejects_wrong_extension_fake_signature_and_large_files(self):
        with self.assertRaises(ValidationError):
            validate_submission_file(sample_upload("program.exe"), ["pdf"], 1)
        with self.assertRaises(ValidationError):
            validate_submission_file(sample_upload("fake.pdf", b"not a PDF"), ["pdf"], 1)
        with self.assertRaises(ValidationError):
            validate_submission_file(
                sample_upload(content=b"%PDF-" + b"x" * 100),
                ["pdf"],
                0,
            )

    def test_rejects_malformed_office_documents(self):
        with self.assertRaises(ValidationError):
            validate_submission_file(
                sample_upload("fake.pptx", b"PK\x03\x04invalid archive"),
                ["pptx"],
                1,
            )


class CompetitionSubmissionFormTests(TestCase):
    """Validate fields according to each competition's distinct submission type."""

    def setUp(self):
        self.competitions = {
            item.submission_kind: item
            for item in Competition.objects.all()
        }

    def test_pitch_form_supports_individual_or_team_and_requires_a_deck(self):
        competition = self.competitions["pitch"]
        competition.minimum_team_size = 2
        competition.save()
        data = {
            "participation_type": "team",
            "team_name": "Impact Builders",
            "team_members_text": "Member One | one@example.test\nMember Two | two@example.test",
            "idea_name": "Reusable packaging",
            "problem": "Packaging waste",
            "solution": "Reusable containers",
            "impact_summary": "Less single-use waste",
        }
        form = PitchSubmissionForm(
            data,
            {"attachment": sample_upload()},
            competition=competition,
            is_submitting=True,
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(len(form.submission_data()["team_members"]), 2)

        data["team_members_text"] = "Member One"
        invalid = PitchSubmissionForm(
            data,
            {"attachment": sample_upload()},
            competition=competition,
            is_submitting=True,
        )
        self.assertFalse(invalid.is_valid())

    def test_debate_requires_configured_team_member_details(self):
        competition = self.competitions["debate"]
        competition.debate_motions = ["This House supports social business."]
        data = {
            "team_name": "Debate Team",
            "team_members_text": "Member One | one@example.test\nMember Two",
            "institution": "Example University",
            "preferred_motion": "This House supports social business.",
        }
        form = DebateSubmissionForm(
            data,
            competition=competition,
            is_submitting=True,
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(len(form.submission_data()["team_members"]), 2)

    def test_poster_requires_an_artwork_file_in_configured_format(self):
        competition = self.competitions["poster"]
        form = PosterSubmissionForm(
            {"poster_title": "A better future", "abstract": "An abstract."},
            {"attachment": sample_upload("poster.png", PNG_CONTENT)},
            competition=competition,
            is_submitting=True,
        )
        self.assertTrue(form.is_valid(), form.errors)

        invalid = PosterSubmissionForm(
            {"poster_title": "A better future", "abstract": "An abstract."},
            {"attachment": sample_upload("poster.exe")},
            competition=competition,
            is_submitting=True,
        )
        self.assertFalse(invalid.is_valid())

    def test_article_word_count_is_enforced_on_submit(self):
        competition = self.competitions["article"]
        competition.article_min_words = 4
        competition.article_max_words = 6
        data = {
            "article_title": "A short article",
            "article_topic": "Impact",
            "article_text": "One two three",
        }
        form = ArticleSubmissionForm(
            data,
            competition=competition,
            is_submitting=True,
        )
        self.assertFalse(form.is_valid())

        data["article_text"] = "One two three four five"
        valid = ArticleSubmissionForm(
            data,
            competition=competition,
            is_submitting=True,
        )
        self.assertTrue(valid.is_valid(), valid.errors)

    def test_video_accepts_https_link_or_allowed_video_file_only(self):
        competition = self.competitions["video"]
        valid = VideoSubmissionForm(
            {"video_title": "A change story", "video_url": "https://video.example.test/watch"},
            competition=competition,
            is_submitting=True,
        )
        self.assertTrue(valid.is_valid(), valid.errors)

        invalid = VideoSubmissionForm(
            {"video_title": "A change story", "video_url": "http://video.example.test/watch"},
            competition=competition,
            is_submitting=True,
        )
        self.assertFalse(invalid.is_valid())

    def test_award_supports_self_and_peer_nominations(self):
        competition = self.competitions["award"]
        base = {
            "nominee_name": "Young Changemaker",
            "nominee_organization": "Community group",
            "achievements": "Organized an initiative.",
            "supporting_evidence": "Evidence details.",
        }
        for kind in ("self", "peer"):
            with self.subTest(kind=kind):
                form = AwardSubmissionForm(
                    {**base, "nomination_type": kind},
                    competition=competition,
                    is_submitting=True,
                )
                self.assertTrue(form.is_valid(), form.errors)

    def test_draft_forms_allow_incomplete_fields(self):
        form = FORM_BY_KIND["article"](
            {"article_title": "Working title"},
            competition=self.competitions["article"],
            is_submitting=False,
        )

        self.assertTrue(form.is_valid(), form.errors)


class CompetitionSubmissionViewTests(TestCase):
    """Ensure participant history, ownership, deadlines, and uploads stay private."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="participant",
            email="participant@example.test",
            password="test-password-123",
        )
        self.other_user = get_user_model().objects.create_user(
            username="other-participant",
            password="test-password-456",
        )
        self.competition = Competition.objects.get(slug="impact-2027")
        now = timezone.now()
        self.competition.status = Competition.Status.OPEN
        self.competition.starts_at = now - timedelta(hours=1)
        self.competition.deadline = now + timedelta(days=2)
        self.competition.save()

    def tearDown(self):
        for submission in CompetitionSubmission.objects.filter(
            participant__in=(self.user, self.other_user)
        ):
            if submission.attachment:
                submission.attachment.delete(save=False)

    def valid_pitch_data(self, **overrides):
        return {
            "participation_type": "individual",
            "team_name": "",
            "team_members_text": "",
            "idea_name": "Circular design",
            "problem": "Waste problem",
            "solution": "A circular solution",
            "impact_summary": "Reduced waste",
            "action": "submit",
            **overrides,
        }

    def test_login_required_for_submission_and_history(self):
        submit = self.client.get(
            reverse("competitions:submit", args=(self.competition.slug,))
        )
        history = self.client.get(reverse("competitions:my-submissions"))

        self.assertRedirects(
            submit,
            f"{reverse('login')}?next={reverse('competitions:submit', args=(self.competition.slug,))}",
        )
        self.assertRedirects(
            history,
            f"{reverse('login')}?next={reverse('competitions:my-submissions')}",
        )

    def test_participant_can_save_draft_and_submit_one_entry(self):
        self.client.force_login(self.user)
        draft_data = self.valid_pitch_data(action="draft", idea_name="")
        draft_response = self.client.post(
            reverse("competitions:submit", args=(self.competition.slug,)),
            draft_data,
        )
        submission = CompetitionSubmission.objects.get(
            competition=self.competition,
            participant=self.user,
        )
        self.assertEqual(draft_response.status_code, 302)
        self.assertEqual(submission.status, CompetitionSubmission.Status.DRAFT)
        self.assertIsNone(submission.submitted_at)

        data = self.valid_pitch_data()
        response = self.client.post(
            reverse("competitions:submit", args=(self.competition.slug,)),
            {**data, "attachment": sample_upload()},
            follow=True,
        )
        submission.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your submission has been submitted.")
        self.assertEqual(submission.status, CompetitionSubmission.Status.SUBMITTED)
        self.assertIsNotNone(submission.submitted_at)
        self.assertTrue(submission.attachment.name.startswith("competition-submissions/"))
        self.assertNotIn("participant", submission.attachment.name)

        # Repeated submission updates the existing row instead of creating duplicates.
        response = self.client.post(
            reverse("competitions:submit", args=(self.competition.slug,)),
            {
                **self.valid_pitch_data(idea_name="Updated idea"),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            CompetitionSubmission.objects.filter(
                competition=self.competition,
                participant=self.user,
            ).count(),
            1,
        )
        submission.refresh_from_db()
        self.assertEqual(submission.data["idea_name"], "Updated idea")

    def test_submission_history_and_details_only_show_owner_entries(self):
        submission = CompetitionSubmission.objects.create(
            competition=self.competition,
            participant=self.user,
            status=CompetitionSubmission.Status.SUBMITTED,
            submitted_at=timezone.now(),
            data={"idea_name": "Private proposal"},
        )
        self.client.force_login(self.other_user)

        response = self.client.get(reverse("competitions:my-submissions"))
        detail = self.client.get(
            reverse(
                "competitions:submission-detail",
                args=(submission.reference,),
            )
        )

        self.assertNotContains(response, "Private proposal")
        self.assertEqual(detail.status_code, 404)

    def test_only_owner_or_explicit_reviewer_can_download_private_file(self):
        submission = CompetitionSubmission.objects.create(
            competition=self.competition,
            participant=self.user,
            status=CompetitionSubmission.Status.SUBMITTED,
            submitted_at=timezone.now(),
            attachment=sample_upload(),
        )
        url = reverse(
            "competitions:submission-file",
            args=(submission.reference,),
        )
        self.client.force_login(self.other_user)
        self.assertEqual(self.client.get(url).status_code, 404)

        self.client.force_login(self.user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/octet-stream")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        response.close()
        with self.assertRaises(ValueError):
            submission.attachment.url

    def test_participant_cannot_create_or_edit_after_deadline_by_default(self):
        self.competition.deadline = timezone.now() - timedelta(minutes=1)
        self.competition.save()
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("competitions:submit", args=(self.competition.slug,)),
            {**self.valid_pitch_data(), "attachment": sample_upload()},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(CompetitionSubmission.objects.exists())

    def test_late_edits_only_work_when_admin_policy_allows_them(self):
        submission = CompetitionSubmission.objects.create(
            competition=self.competition,
            participant=self.user,
            status=CompetitionSubmission.Status.SUBMITTED,
            submitted_at=timezone.now() - timedelta(days=2),
            data={"idea_name": "Original"},
        )
        self.competition.deadline = timezone.now() - timedelta(minutes=1)
        self.competition.save()
        self.client.force_login(self.user)

        blocked = self.client.get(
            reverse("competitions:submit", args=(self.competition.slug,))
        )
        self.assertEqual(blocked.status_code, 302)

        self.competition.allow_late_edits = True
        self.competition.save()
        allowed = self.client.get(
            reverse("competitions:submit", args=(self.competition.slug,))
        )
        self.assertEqual(allowed.status_code, 200)

    def test_authenticated_staff_without_review_permission_cannot_view_entries(self):
        submission = CompetitionSubmission.objects.create(
            competition=self.competition,
            participant=self.user,
            status=CompetitionSubmission.Status.SUBMITTED,
            submitted_at=timezone.now(),
        )
        staff = get_user_model().objects.create_user(
            username="staff",
            is_staff=True,
            password="test-password-789",
        )
        self.client.force_login(staff)

        response = self.client.get(
            reverse(
                "competitions:submission-detail",
                args=(submission.reference,),
            )
        )

        self.assertEqual(response.status_code, 404)

    def test_authorized_reviewer_can_view_admin_review_screen(self):
        submission = CompetitionSubmission.objects.create(
            competition=self.competition,
            participant=self.user,
            status=CompetitionSubmission.Status.SUBMITTED,
            submitted_at=timezone.now(),
        )
        reviewer = get_user_model().objects.create_user(
            username="reviewer",
            is_staff=True,
            password="reviewer-password",
        )
        reviewer.user_permissions.add(
            Permission.objects.get(codename="review_competitionsubmission")
        )
        self.client.force_login(reviewer)

        response = self.client.get(
            reverse(
                "admin:competitions_competitionsubmission_change",
                args=(submission.pk,),
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="reviewer_notes"')

    def test_authorized_reviewer_can_update_review_status_and_feedback(self):
        submission = CompetitionSubmission.objects.create(
            competition=self.competition,
            participant=self.user,
            status=CompetitionSubmission.Status.SUBMITTED,
            submitted_at=timezone.now(),
        )
        reviewer = get_user_model().objects.create_user(
            username="review-editor",
            is_staff=True,
            password="reviewer-password",
        )
        reviewer.user_permissions.add(
            Permission.objects.get(codename="review_competitionsubmission")
        )
        self.client.force_login(reviewer)

        response = self.client.post(
            reverse(
                "admin:competitions_competitionsubmission_change",
                args=(submission.pk,),
            ),
            {
                "status": CompetitionSubmission.Status.ACCEPTED,
                "reviewer_notes": "Reviewed by the authorized panel.",
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 302)
        submission.refresh_from_db()
        self.assertEqual(submission.status, CompetitionSubmission.Status.ACCEPTED)
        self.assertEqual(
            submission.reviewer_notes,
            "Reviewed by the authorized panel.",
        )
        self.assertEqual(submission.reviewed_by, reviewer)
        self.assertIsNotNone(submission.reviewed_at)

    def test_authorized_reviewer_can_export_metadata_without_private_content(self):
        submission = CompetitionSubmission.objects.create(
            competition=self.competition,
            participant=self.user,
            status=CompetitionSubmission.Status.SUBMITTED,
            submitted_at=timezone.now(),
            data={"idea_name": "Confidential idea"},
        )
        reviewer = get_user_model().objects.create_user(
            username="csv-reviewer",
            is_staff=True,
            password="reviewer-password",
        )
        reviewer.user_permissions.add(
            Permission.objects.get(codename="review_competitionsubmission")
        )
        self.client.force_login(reviewer)

        response = self.client.post(
            reverse("admin:competitions_competitionsubmission_changelist"),
            {
                "action": "export_submissions_csv",
                "_selected_action": [str(submission.pk)],
                "index": "0",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        self.assertContains(response, str(submission.reference))
        self.assertNotContains(response, "participant@example.test")
        self.assertNotContains(response, "Confidential idea")

    def test_anonymous_cannot_access_private_file(self):
        self.client.logout()
        response = self.client.get(reverse("competitions:my-submissions"))
        self.assertEqual(response.status_code, 302)
