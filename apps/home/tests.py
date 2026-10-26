"""Tests for homepage content, publication rules, and management access."""

from datetime import datetime
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Announcement, FeaturedSpeaker, HomeHighlight, SummitEvent
from apps.speakers.models import SpeakerCategory
from .validators import validate_local_destination, validate_video_url


class HomepageViewTests(TestCase):
    """Verify that the homepage renders editable and only public content."""

    def setUp(self):
        self.event = SummitEvent.objects.get(key=SummitEvent.Key.MAIN)

    def test_homepage_renders_shared_layout_and_internal_navigation(self):
        response = self.client.get(reverse("home:index"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "base.html")
        self.assertContains(response, self.event.event_title)
        self.assertContains(response, 'src="/static/images/ysse-logo.png"')
        self.assertContains(
            response,
            '<link rel="icon" type="image/png" href="/static/images/ysse-logo.png">',
        )
        self.assertContains(response, 'href="#highlights"')
        self.assertContains(response, 'href="/competitions/"')
        self.assertContains(response, 'href="/registrations/"')
        self.assertContains(response, 'name="viewport"')
        self.assertContains(response, "<summary>Summit</summary>")
        self.assertContains(response, "<summary>Get involved</summary>")
        self.assertContains(response, 'class="footer-links-grid"')
        self.assertContains(response, 'id="footer-newsletter-email"')

    def test_shared_footer_has_clickable_developer_social_contacts(self):
        response = self.client.get(reverse("home:index"))

        self.assertContains(response, "Develop &amp; design by")
        self.assertContains(response, "MD. Imtiaz Sarder")
        self.assertContains(
            response,
            'href="https://www.facebook.com/mdimtiaz.joy.50"',
        )
        self.assertContains(response, 'href="https://wa.me/8801736712955"')

    def test_error_pages_use_shared_branded_layout_and_footer(self):
        for template_name, expected_text in (
            ("404.html", "Page Not Found"),
            ("500.html", "Temporary Issue"),
        ):
            with self.subTest(template=template_name):
                content = render_to_string(template_name)

                self.assertIn(expected_text, content)
                self.assertIn('src="/static/images/ysse-logo.png"', content)
                self.assertIn("MD. Imtiaz Sarder", content)
                self.assertIn('href="https://wa.me/8801736712955"', content)

    @override_settings(DEBUG=False)
    def test_not_found_response_includes_shared_logo_and_footer(self):
        response = self.client.get("/page-that-does-not-exist/")

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Page Not Found", status_code=404)
        self.assertContains(response, "MD. Imtiaz Sarder", status_code=404)

    def test_countdown_uses_configured_timezone_aware_event_time(self):
        self.event.event_start_at = datetime(
            2027, 2, 1, 8, 30, tzinfo=ZoneInfo("Asia/Dhaka")
        )
        self.event.venue = "Dhaka"
        self.event.save()

        response = self.client.get(reverse("home:index"))

        self.assertContains(response, 'data-event-start="2027-02-01T08:30:00+06:00"')
        self.assertContains(response, "Dhaka")

    def test_closed_registration_disables_homepage_call_to_action(self):
        self.event.registration_available = False
        self.event.save()

        response = self.client.get(reverse("home:index"))

        self.assertContains(response, "Registration is not open yet")
        self.assertContains(response, 'aria-disabled="true"')
        self.assertNotContains(
            response,
            'class="button button-primary" href="#registration"',
        )

    def test_homepage_uses_database_highlights(self):
        highlight, _created = HomeHighlight.objects.update_or_create(
            title="Six National Competitions",
            defaults={
                "description": "A national stage for young innovators.",
                "icon": HomeHighlight.Icon.TROPHY,
                "is_published": True,
            },
        )

        response = self.client.get(reverse("home:index"))

        self.assertContains(response, highlight.title)
        self.assertContains(response, highlight.description)

    def test_unpublished_highlights_are_not_displayed(self):
        HomeHighlight.objects.create(
            title="Internal highlight",
            description="This item is not public.",
            is_published=False,
        )

        response = self.client.get(reverse("home:index"))

        self.assertNotContains(response, "Internal highlight")

    def test_only_published_featured_speakers_are_displayed(self):
        category = SpeakerCategory.objects.get(slug="keynote-speakers")
        featured = FeaturedSpeaker.objects.create(
            full_name="Amina Example",
            official_designation="Social Entrepreneur",
            organization="Example Organization",
            biography="Works with young entrepreneurs.",
            is_published=True,
            is_featured=True,
        )
        featured.categories.add(category)
        FeaturedSpeaker.objects.create(
            full_name="Draft Example",
            official_designation="Guest",
            organization="Private",
            biography="This profile is not yet public.",
            is_published=False,
            is_featured=True,
        )
        FeaturedSpeaker.objects.create(
            full_name="Not Featured Example",
            official_designation="Guest",
            organization="Private",
            biography="This profile is not featured.",
            is_published=True,
            is_featured=False,
        )

        response = self.client.get(reverse("home:index"))

        self.assertContains(response, featured.full_name)
        self.assertContains(response, featured.official_designation)
        self.assertContains(response, featured.organization)
        self.assertContains(response, featured.biography)
        self.assertNotContains(response, "Draft Example")
        self.assertNotContains(response, "Not Featured Example")

    def test_homepage_limits_featured_speakers_to_four(self):
        category = SpeakerCategory.objects.get(slug="keynote-speakers")
        for index in range(5):
            speaker = FeaturedSpeaker.objects.create(
                full_name=f"Featured Speaker {index}",
                official_designation="Social Entrepreneur",
                organization="Example Organization",
                biography="Works with young entrepreneurs.",
                is_published=True,
                is_featured=True,
                display_order=index,
            )
            speaker.categories.add(category)

        response = self.client.get(reverse("home:index"))

        self.assertEqual(len(response.context["featured_speakers"]), 4)
        self.assertNotContains(response, "Featured Speaker 4")

    def test_speaker_and_announcement_empty_states_render_without_images(self):
        response = self.client.get(reverse("home:index"))

        self.assertContains(response, "Our featured speakers will be announced soon.")
        self.assertContains(response, "There are no public updates just yet.")
        self.assertNotContains(response, '<img src=""')

    def test_only_published_announcements_with_images_optional_are_shown(self):
        announcement = Announcement.objects.create(
            title="Summit date announced",
            summary="Save the date for the summit.",
            status=Announcement.Status.PUBLISHED,
        )
        Announcement.objects.create(
            title="Draft announcement",
            summary="This is still being prepared.",
            status=Announcement.Status.DRAFT,
        )
        Announcement.objects.create(
            title="Archived announcement",
            summary="This update is archived.",
            status=Announcement.Status.ARCHIVED,
        )
        Announcement.objects.create(
            title="Future announcement",
            summary="This is not public yet.",
            status=Announcement.Status.PUBLISHED,
            published_at=datetime(2028, 1, 1, tzinfo=ZoneInfo("UTC")),
        )

        response = self.client.get(reverse("home:index"))

        self.assertContains(response, announcement.title)
        self.assertNotContains(response, "Draft announcement")
        self.assertNotContains(response, "Archived announcement")
        self.assertNotContains(response, "Future announcement")
        self.assertNotContains(response, '<img src=""')


class HomepageAdminTests(TestCase):
    """Ensure homepage content is managed through Django's staff permissions."""

    def test_anonymous_user_cannot_access_event_management(self):
        response = self.client.get(reverse("admin:home_summitevent_changelist"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/ysse2014/login/", response.url)

    def test_staff_can_edit_event_but_cannot_create_a_duplicate(self):
        user = get_user_model().objects.create_user(
            username="editor",
            password="test-password",
            is_staff=True,
        )
        user.user_permissions.add(
            Permission.objects.get(codename="change_summitevent")
        )
        self.client.force_login(user)

        change_response = self.client.get(
            reverse("admin:home_summitevent_change", args=[SummitEvent.objects.get().pk])
        )
        add_response = self.client.get(reverse("admin:home_summitevent_add"))

        self.assertEqual(change_response.status_code, 200)
        self.assertNotEqual(add_response.status_code, 200)

    def test_superuser_can_manage_homepage_content(self):
        user = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.com", password="test-password"
        )
        self.client.force_login(user)

        response = self.client.get(reverse("admin:home_announcement_add"))

        self.assertEqual(response.status_code, 200)

    def test_authorized_editor_can_publish_unpublish_and_archive(self):
        announcement = Announcement.objects.create(
            title="Editorial update",
            summary="A news update for the summit.",
            status=Announcement.Status.DRAFT,
        )
        user = get_user_model().objects.create_user(
            username="news-editor",
            password="test-password",
            is_staff=True,
        )
        user.user_permissions.add(
            Permission.objects.get(codename="change_announcement")
        )
        self.client.force_login(user)
        changelist_url = reverse("admin:home_announcement_changelist")

        for action, expected_status in (
            ("publish_announcements", Announcement.Status.PUBLISHED),
            ("unpublish_announcements", Announcement.Status.DRAFT),
            ("archive_announcements", Announcement.Status.ARCHIVED),
        ):
            with self.subTest(action=action):
                response = self.client.post(
                    changelist_url,
                    {
                        "action": action,
                        "_selected_action": [str(announcement.pk)],
                    },
                )

                self.assertEqual(response.status_code, 302)
                announcement.refresh_from_db()
                self.assertEqual(announcement.status, expected_status)


class HomepageValidationTests(TestCase):
    """Cover risky media and CTA URL values entered by content editors."""

    def test_video_url_requires_https(self):
        with self.assertRaises(ValidationError):
            validate_video_url("http://media.example.com/promo.mp4")

        validate_video_url("https://media.example.com/promo.mp4")

    def test_cta_destination_allows_internal_paths_and_anchors_only(self):
        validate_local_destination("#highlights")
        validate_local_destination("/register/")

        for unsafe_destination in (
            "https://example.com",
            "//example.com",
            "javascript:alert(1)",
        ):
            with self.subTest(destination=unsafe_destination):
                with self.assertRaises(ValidationError):
                    validate_local_destination(unsafe_destination)
