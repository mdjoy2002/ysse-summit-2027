"""Tests for About YSSE content, publication safeguards, and admin workflows."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from .models import (
    FounderVision,
    ImpactStatistic,
    TimelineMilestone,
    TimelinePeriod,
    YSSEWing,
)


class AboutPageTests(TestCase):
    """Verify only published About YSSE content is visible to visitors."""

    def test_timeline_shows_the_supplied_periods_and_milestones_in_order(self):
        response = self.client.get(reverse("about:index"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "base.html")
        self.assertContains(response, "2014—2016")
        self.assertContains(response, "Foundation &amp; Early Growth")
        self.assertContains(response, "Establishment of YSSE.")
        self.assertContains(response, "2017—2020")
        self.assertContains(response, "National Expansion &amp; Academy Launch")
        self.assertContains(response, "2021—2023")
        self.assertContains(response, "2024—2026")
        self.assertContains(response, "Technology and workflow improvements.")
        self.assertEqual(
            set(
                TimelineMilestone.objects.values_list("description", flat=True)
            ),
            {
                "Establishment of YSSE.",
                "University workshops and seminars.",
                "Early youth entrepreneurship initiatives.",
                "Expansion of youth networks.",
                "YSSE Academy initiatives.",
                "Skill-development and internship programs.",
                "YSSE Shop.",
                "YSSE IT.",
                "International social impact initiatives.",
                "Ten-year milestone.",
                "International partnerships.",
                "Technology and workflow improvements.",
            },
        )

    def test_proposed_impact_statistics_remain_unpublished(self):
        response = self.client.get(reverse("about:index"))

        self.assertContains(response, "YSSE impact figures are being verified.")
        self.assertContains(response, "Confirmed statistics will be shared here.")
        self.assertNotContains(response, "10,000+")
        self.assertNotContains(response, "100,000+")
        self.assertFalse(
            ImpactStatistic.objects.filter(is_published=True).exists()
        )

    def test_verified_impact_statistics_are_shown_from_database(self):
        statistic = ImpactStatistic.objects.get(label="Members Mobilized")
        statistic.value = "12,345+"
        statistic.is_published = True
        statistic.save()

        response = self.client.get(reverse("about:index"))

        self.assertContains(response, "12,345+")
        self.assertContains(response, "Members Mobilized")
        self.assertNotContains(response, "Projects Completed")

    def test_unpublished_timeline_period_is_hidden(self):
        TimelinePeriod.objects.filter(start_year=2024).update(is_published=False)

        response = self.client.get(reverse("about:index"))

        self.assertNotContains(response, "Decade Milestone")
        self.assertContains(response, "2014—2016")

    def test_founder_message_is_hidden_until_published(self):
        founder = FounderVision.objects.get(key=FounderVision.Key.FOUNDER)
        founder.name = "Example Founder"
        founder.designation = "Founder"
        founder.written_message = "A message prepared for review."
        founder.save()

        response = self.client.get(reverse("about:index"))

        self.assertNotContains(response, "Example Founder")
        self.assertContains(response, "A message from YSSE leadership is on its way.")

    def test_published_founder_message_and_optional_media_fallbacks_render(self):
        founder = FounderVision.objects.get(key=FounderVision.Key.FOUNDER)
        founder.name = "Example Founder"
        founder.designation = "Founder"
        founder.written_message = "A published message for young entrepreneurs."
        founder.is_published = True
        founder.save()

        response = self.client.get(reverse("about:index"))

        self.assertContains(response, "Example Founder")
        self.assertContains(response, "A published message for young entrepreneurs.")
        self.assertContains(response, "founder-profile-placeholder")
        self.assertNotContains(response, '<img src=""')
        self.assertNotContains(response, "Watch video message")

    def test_confirmed_wings_are_database_backed_and_optional_links_are_hidden(self):
        response = self.client.get(reverse("about:index"))

        self.assertContains(response, "YSSE Academy")
        self.assertContains(response, "YSSE Shop")
        self.assertContains(response, "YSSE IT")
        self.assertContains(response, "YSSE Blog")
        self.assertNotContains(response, 'href=""')
        self.assertNotContains(response, '<img src=""')

    def test_empty_sections_show_professional_states(self):
        TimelinePeriod.objects.all().delete()
        YSSEWing.objects.all().delete()
        ImpactStatistic.objects.all().delete()

        response = self.client.get(reverse("about:index"))

        self.assertContains(response, "Our timeline is being prepared.")
        self.assertContains(response, "YSSE impact figures are being verified.")
        self.assertContains(response, "A message from YSSE leadership is on its way.")
        self.assertContains(response, "YSSE initiatives will be featured here.")

    def test_about_navigation_and_mobile_layout_hooks_are_present(self):
        response = self.client.get(reverse("about:index"))

        self.assertContains(response, 'href="/about/"')
        self.assertContains(response, "about/css/about.css")
        self.assertContains(response, "name=\"viewport\"")


class AboutAdminTests(TestCase):
    """Verify standard Django model permissions protect About content."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="about-editor",
            password="test-password",
            is_staff=True,
        )
        self.client.force_login(self.user)

    def grant(self, *codenames):
        """Grant just the model permissions required by a staff workflow."""
        permissions = Permission.objects.filter(codename__in=codenames)
        self.user.user_permissions.add(*permissions)

    def test_staff_can_create_timeline_period_with_inline_milestone(self):
        self.grant(
            "add_timelineperiod",
            "change_timelineperiod",
            "add_timelinemilestone",
            "change_timelinemilestone",
        )

        response = self.client.get(reverse("admin:about_timelineperiod_add"))

        self.assertEqual(response.status_code, 200)
        inline_admin_formset = response.context["inline_admin_formsets"][0]
        self.assertEqual(inline_admin_formset.formset.model, TimelineMilestone)

        response = self.client.post(
            reverse("admin:about_timelineperiod_add"),
            {
                "start_year": "2030",
                "end_year": "2031",
                "title": "Test timeline period",
                "sort_order": "5",
                "is_published": "on",
                "milestones-TOTAL_FORMS": "1",
                "milestones-INITIAL_FORMS": "0",
                "milestones-MIN_NUM_FORMS": "0",
                "milestones-MAX_NUM_FORMS": "1000",
                "milestones-0-description": "A test milestone.",
                "milestones-0-sort_order": "1",
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 302)
        period = TimelinePeriod.objects.get(title="Test timeline period")
        self.assertEqual(
            list(period.milestones.values_list("description", flat=True)),
            ["A test milestone."],
        )

    def test_staff_can_publish_a_verified_impact_statistic(self):
        self.grant("change_impactstatistic")
        statistic = ImpactStatistic.objects.get(label="Members Mobilized")
        response = self.client.post(
            reverse("admin:about_impactstatistic_change", args=[statistic.pk]),
            {
                "label": statistic.label,
                "value": "10,000+",
                "sort_order": statistic.sort_order,
                "is_published": "on",
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 302)
        statistic.refresh_from_db()
        self.assertTrue(statistic.is_published)

    def test_staff_can_create_edit_and_unpublish_a_wing(self):
        self.grant("add_yssewing", "change_yssewing")
        response = self.client.post(
            reverse("admin:about_yssewing_add"),
            {
                "name": "YSSE Test Initiative",
                "description": "An initiative description.",
                "external_url": "",
                "sort_order": "5",
                "is_published": "on",
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 302)
        wing = YSSEWing.objects.get(name="YSSE Test Initiative")
        self.assertTrue(wing.is_published)

        response = self.client.post(
            reverse("admin:about_yssewing_change", args=[wing.pk]),
            {
                "name": wing.name,
                "description": "An updated initiative description.",
                "external_url": "",
                "sort_order": "5",
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 302)
        wing.refresh_from_db()
        self.assertFalse(wing.is_published)
        self.assertEqual(wing.description, "An updated initiative description.")

    def test_staff_can_publish_founder_vision_only_with_required_attribution(self):
        self.grant("change_foundervision")
        founder = FounderVision.objects.get(key=FounderVision.Key.FOUNDER)
        response = self.client.post(
            reverse("admin:about_foundervision_change", args=[founder.pk]),
            {
                "name": "A Verified Founder",
                "designation": "Founder and President",
                "profile_image-clear": "",
                "video_message_url": "",
                "video_thumbnail-clear": "",
                "written_message": "A founder's published vision.",
                "is_published": "on",
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 302)
        founder.refresh_from_db()
        self.assertTrue(founder.is_published)
        self.assertEqual(founder.name, "A Verified Founder")

    def test_anonymous_user_cannot_access_about_admin(self):
        self.client.logout()
        response = self.client.get(reverse("admin:about_impactstatistic_changelist"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/ysse2014/login/", response.url)


class AboutModelValidationTests(TestCase):
    """Ensure published founder content has an attributable written message."""

    def test_founder_cannot_be_published_without_name_designation_and_message(self):
        founder = FounderVision.objects.get(key=FounderVision.Key.FOUNDER)
        founder.name = ""
        founder.designation = ""
        founder.written_message = ""
        founder.is_published = True

        with self.assertRaises(ValidationError) as context:
            founder.full_clean()

        self.assertIn("is_published", context.exception.message_dict)

    def test_founder_video_url_must_use_https(self):
        founder = FounderVision(
            video_message_url="http://example.com/video",
        )

        with self.assertRaises(ValidationError):
            founder.full_clean()
