"""Tests for agenda ordering, timezones, publication, and Admin access."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from apps.competitions.models import Competition
from apps.home.models import FeaturedSpeaker, SummitEvent
from apps.speakers.models import SpeakerCategory

from .models import ScheduleSession


class ScheduleViewTests(TestCase):
    """Check the public agenda's chronology and optional linked content."""

    @classmethod
    def setUpTestData(cls):
        cls.event = SummitEvent.objects.get(key=SummitEvent.Key.MAIN)

    def test_initial_agenda_is_seeded_and_shown_in_chronological_order(self):
        response = self.client.get(reverse("schedule:index"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "schedule/index.html")
        sessions = list(response.context["sessions"])
        self.assertEqual(len(sessions), 6)
        self.assertEqual(
            [session.title for session in sessions],
            [
                "Registration & Entry",
                "Opening Ceremony & Keynote",
                "The Grand Pitch",
                "Networking Lunch",
                "The Big Debate",
                "Grand Gala & Closing",
            ],
        )
        self.assertContains(response, "Bangladesh time")
        self.assertContains(response, "February 1, 2027")
        self.assertContains(response, "08:30")
        self.assertContains(response, "AM</small>")

    def test_missing_optional_relationships_and_end_time_render_safely(self):
        session = ScheduleSession.objects.get(title="Registration & Entry")

        self.assertIsNone(session.ends_at)
        self.assertIsNone(session.speaker)
        self.assertIsNone(session.competition)

        response = self.client.get(reverse("schedule:index"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '<img src=""')
        self.assertContains(response, "Registration &amp; Entry")

    def test_unpublished_sessions_are_not_public(self):
        session = ScheduleSession.objects.get(title="Registration & Entry")
        session.is_published = False
        session.save()

        response = self.client.get(reverse("schedule:index"))

        self.assertNotContains(response, session.title)
        self.assertEqual(len(response.context["sessions"]), 5)

    def test_sessions_are_ordered_by_time_before_manual_display_order(self):
        ScheduleSession.objects.filter(title="Grand Gala & Closing").update(
            display_order=0
        )

        response = self.client.get(reverse("schedule:index"))

        sessions = list(response.context["sessions"])
        self.assertEqual(sessions[0].title, "Registration & Entry")
        self.assertEqual(sessions[-1].title, "Grand Gala & Closing")

    def test_speaker_and_competition_links_render_when_published(self):
        speaker = FeaturedSpeaker.objects.create(
            full_name="Keynote Speaker",
            official_designation="Founder",
            organization="Social Enterprise",
            biography="Works with young entrepreneurs.",
            is_published=True,
        )
        category = SpeakerCategory.objects.get(slug="keynote-speakers")
        speaker.categories.add(category)
        session = ScheduleSession.objects.get(title="Opening Ceremony & Keynote")
        session.speaker = speaker
        session.save()

        competition = Competition.objects.get(slug="impact-2027")
        competition.is_published = True
        competition.save()
        debate = ScheduleSession.objects.get(title="The Big Debate")
        debate.competition = competition
        debate.save()

        response = self.client.get(reverse("schedule:index"))

        self.assertContains(
            response,
            reverse("speakers:detail", args=[speaker.slug]),
        )
        self.assertContains(
            response,
            reverse("competitions:detail", args=[competition.slug]),
        )

    def test_empty_published_schedule_has_clear_empty_state(self):
        ScheduleSession.objects.update(is_published=False)

        response = self.client.get(reverse("schedule:index"))

        self.assertContains(response, "The detailed schedule is being prepared.")


class ScheduleValidationTests(TestCase):
    """Reject invalid time ranges in model validation and database constraints."""

    def setUp(self):
        self.event = SummitEvent.objects.get(key=SummitEvent.Key.MAIN)
        self.starts_at = datetime(
            2027,
            2,
            1,
            8,
            30,
            tzinfo=ZoneInfo("Asia/Dhaka"),
        )

    def test_end_time_must_follow_start_time(self):
        session = ScheduleSession(
            event=self.event,
            title="Invalid Session",
            starts_at=self.starts_at,
            ends_at=self.starts_at,
        )

        with self.assertRaises(ValidationError) as error:
            session.full_clean()

        self.assertIn("ends_at", error.exception.message_dict)

    def test_end_time_after_start_is_valid(self):
        session = ScheduleSession(
            event=self.event,
            title="Valid Session",
            starts_at=self.starts_at,
            ends_at=self.starts_at + timedelta(minutes=30),
        )

        session.full_clean()

    def test_database_rejects_end_time_equal_to_start(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ScheduleSession.objects.create(
                    event=self.event,
                    title="Database-invalid session",
                    starts_at=self.starts_at,
                    ends_at=self.starts_at,
                )


class ScheduleAdminTests(TestCase):
    """Ensure agenda editing respects Django's model permissions."""

    def test_anonymous_user_cannot_access_schedule_admin(self):
        response = self.client.get(reverse("admin:schedule_schedulesession_changelist"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/ysse2014/login/", response.url)

    def test_authorized_staff_can_add_and_edit_schedule_sessions(self):
        session = ScheduleSession.objects.get(title="Registration & Entry")
        user = get_user_model().objects.create_user(
            username="schedule-editor",
            password="safe-test-password",
            is_staff=True,
        )
        user.user_permissions.add(
            Permission.objects.get(codename="add_schedulesession"),
            Permission.objects.get(codename="change_schedulesession"),
            Permission.objects.get(codename="view_schedulesession"),
        )
        self.client.force_login(user)

        list_response = self.client.get(
            reverse("admin:schedule_schedulesession_changelist")
        )
        add_response = self.client.get(
            reverse("admin:schedule_schedulesession_add")
        )
        edit_response = self.client.get(
            reverse("admin:schedule_schedulesession_change", args=[session.pk])
        )

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(add_response.status_code, 200)
        self.assertEqual(edit_response.status_code, 200)
