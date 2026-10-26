"""Tests for summit details rendering and event configuration behavior."""

from datetime import datetime, timezone as datetime_timezone
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.home.models import SummitEvent


class SummitDetailsViewTests(TestCase):
    """Verify the public details page uses the shared summit configuration."""

    def setUp(self):
        self.event = SummitEvent.objects.get(key=SummitEvent.Key.MAIN)

    def test_details_page_renders_event_information_and_audience(self):
        response = self.client.get(reverse("summit:index"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "base.html")
        self.assertContains(response, self.event.event_title)
        self.assertContains(response, "Youth Leadership, Social Business Innovation")
        self.assertContains(response, "Sustainable Impact")
        self.assertContains(response, self.event.description)
        self.assertContains(response, self.event.get_status_display())
        self.assertContains(response, "University students")
        self.assertContains(response, "young researchers")
        self.assertContains(response, "Social business founders")
        self.assertContains(response, "Policymakers")
        self.assertContains(response, "Impact investors")

    @override_settings(TIME_ZONE="UTC")
    def test_aware_datetimes_are_displayed_in_asia_dhaka(self):
        self.event.event_start_at = datetime(
            2027, 2, 1, 2, 30, tzinfo=datetime_timezone.utc
        )
        self.event.event_end_at = datetime(
            2027, 2, 1, 12, 0, tzinfo=datetime_timezone.utc
        )
        self.event.save()

        response = self.client.get(reverse("summit:index"))

        self.assertContains(response, "Monday, February 1, 2027")
        self.assertContains(response, "8:30 AM")
        self.assertContains(response, "6:00 PM")
        self.assertContains(response, "Bangladesh Standard Time (UTC+6)")
        self.assertEqual(
            timezone.localtime(
                self.event.event_start_at, ZoneInfo("Asia/Dhaka")
            ).hour,
            8,
        )

    def test_registration_button_links_to_the_pass_directory(self):
        response = self.client.get(reverse("summit:index"))

        self.assertContains(response, 'href="/registrations/"')
        self.assertContains(response, "Register Now")

    def test_registration_button_is_unavailable_when_registration_is_closed(self):
        self.event.registration_available = False
        self.event.save()

        response = self.client.get(reverse("summit:index"))

        self.assertNotContains(
            response,
            'class="summit-button summit-button-primary" href="/registrations/"',
        )
        self.assertContains(response, 'aria-disabled="true"')
        self.assertContains(response, "Registration is not open yet")

    def test_missing_optional_venue_description_and_banner_have_fallbacks(self):
        self.event.venue = ""
        self.event.description = ""
        self.event.save()

        response = self.client.get(reverse("summit:index"))

        self.assertContains(response, "Venue to be announced")
        self.assertContains(response, "Summit information is being prepared.")
        self.assertNotContains(response, 'class="summit-banner"')
        self.assertNotContains(response, 'src=""')

    def test_admin_can_edit_event_configuration(self):
        admin_user = get_user_model().objects.create_superuser(
            username="summit-admin",
            email="admin@example.com",
            password="safe-test-password",
        )
        self.client.force_login(admin_user)

        response = self.client.get(
            reverse("admin:home_summitevent_change", args=(self.event.pk,))
        )

        self.assertEqual(response.status_code, 200)
        for field_name in (
            "theme",
            "description",
            "venue",
            "status",
            "banner_image",
            "registration_available",
        ):
            with self.subTest(field=field_name):
                self.assertContains(response, f'name="{field_name}"')
        self.assertContains(response, 'name="event_start_at_0"')
        self.assertContains(response, 'name="event_start_at_1"')
        self.assertContains(response, 'name="event_end_at_0"')
        self.assertContains(response, 'name="event_end_at_1"')


class SummitEventValidationTests(TestCase):
    """Keep the configured local event interval logically valid."""

    def test_end_time_must_be_after_start_time(self):
        event = SummitEvent.objects.get(key=SummitEvent.Key.MAIN)
        event.event_end_at = event.event_start_at

        with self.assertRaises(ValidationError) as error:
            event.full_clean()

        self.assertIn("event_end_at", error.exception.message_dict)
