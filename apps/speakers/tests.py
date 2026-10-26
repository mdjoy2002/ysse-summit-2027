"""Tests for public speaker pages and staff-only profile management."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from apps.home.models import FeaturedSpeaker

from .models import SpeakerCategory


class SpeakerDirectoryTests(TestCase):
    """Verify that only published profiles and categories are publicly visible."""

    @classmethod
    def setUpTestData(cls):
        cls.keynote_category = SpeakerCategory.objects.get(slug="keynote-speakers")
        cls.diplomat_category = SpeakerCategory.objects.get(slug="embassy-delegates")
        cls.speaker = FeaturedSpeaker.objects.create(
            full_name="Amina Rahman",
            official_designation="Founder and CEO",
            organization="Example Social Enterprise",
            biography="A social entrepreneur building inclusive businesses.",
            session_topic="Building sustainable social enterprises",
            is_published=True,
            is_featured=True,
            participation_status=FeaturedSpeaker.ParticipationStatus.CONFIRMED,
        )
        cls.speaker.categories.add(cls.keynote_category, cls.diplomat_category)
        cls.draft = FeaturedSpeaker.objects.create(
            full_name="Draft Guest",
            official_designation="Guest of Honor",
            organization="Private Organization",
            biography="A profile awaiting approval.",
            is_published=False,
        )
        cls.uncategorized_published = FeaturedSpeaker.objects.create(
            full_name="Needs Category Review",
            official_designation="Guest",
            organization="Summit Organization",
            biography="A published profile awaiting category assignment.",
            is_published=True,
        )

    def test_directory_renders_published_speakers_and_confirmation(self):
        response = self.client.get(reverse("speakers:list"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "speakers/list.html")
        self.assertContains(response, self.speaker.full_name)
        self.assertContains(response, self.keynote_category.name)
        self.assertContains(response, "Participation confirmed")
        self.assertNotContains(response, self.draft.full_name)
        self.assertNotContains(response, self.uncategorized_published.full_name)

    def test_category_filter_returns_only_matching_profiles(self):
        response = self.client.get(
            reverse("speakers:list"),
            {"category": "embassy-delegates"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.speaker.full_name)
        self.assertEqual(list(response.context["speakers"]), [self.speaker])
        self.assertEqual(response.context["active_category"], self.diplomat_category)

    def test_invalid_category_filter_returns_not_found(self):
        response = self.client.get(
            reverse("speakers:list"),
            {"category": "not-a-published-category"},
        )

        self.assertEqual(response.status_code, 404)

    def test_detail_shows_biography_topic_categories_and_missing_image_state(self):
        response = self.client.get(
            reverse("speakers:detail", args=[self.speaker.slug])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.speaker.biography)
        self.assertContains(response, self.speaker.session_topic)
        self.assertContains(response, self.keynote_category.name)
        self.assertContains(response, "No profile image available")
        self.assertNotContains(response, '<img src=""')

    def test_unconfirmed_invitation_is_distinguished(self):
        self.speaker.participation_status = (
            FeaturedSpeaker.ParticipationStatus.UNCONFIRMED
        )
        self.speaker.save()

        response = self.client.get(
            reverse("speakers:detail", args=[self.speaker.slug])
        )

        self.assertContains(response, "Participation not confirmed")
        self.assertNotContains(response, "Participation confirmed</span>")

    def test_duplicate_names_receive_distinct_profile_slugs(self):
        another = FeaturedSpeaker.objects.create(
            full_name=self.speaker.full_name,
            official_designation="Panelist",
            organization="Another Organization",
            biography="A separate speaker with the same display name.",
        )

        self.assertNotEqual(another.slug, self.speaker.slug)
        self.assertEqual(another.slug, f"{self.speaker.slug}-2")

    def test_unpublished_profile_is_not_listed_or_accessible(self):
        response = self.client.get(
            reverse("speakers:detail", args=[self.draft.slug])
        )

        self.assertEqual(response.status_code, 404)
        self.assertNotContains(self.client.get(reverse("speakers:list")), self.draft.full_name)

    def test_empty_directory_shows_professional_empty_state(self):
        FeaturedSpeaker.objects.update(is_published=False)

        response = self.client.get(reverse("speakers:list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Speaker announcements are on the way.")

    def test_homepage_feature_card_links_to_public_profile(self):
        response = self.client.get(reverse("home:index"))

        self.assertContains(
            response,
            reverse("speakers:detail", args=[self.speaker.slug]),
        )
        self.assertContains(response, "View All Speakers")
        self.assertContains(response, reverse("speakers:list"))


class SpeakerAdminTests(TestCase):
    """Ensure speaker profile data follows Django Admin model permissions."""

    def test_anonymous_user_cannot_access_speaker_profiles_in_admin(self):
        response = self.client.get(reverse("admin:home_featuredspeaker_changelist"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/ysse2014/login/", response.url)

    def test_authorized_staff_can_edit_speakers_and_categories(self):
        speaker = FeaturedSpeaker.objects.create(
            full_name="Admin Test Speaker",
            official_designation="Panelist",
            organization="Summit Organization",
            biography="A test speaker profile for Admin permissions.",
        )
        user = get_user_model().objects.create_user(
            username="speaker-editor",
            password="safe-test-password",
            is_staff=True,
        )
        user.user_permissions.add(
            Permission.objects.get(codename="add_featuredspeaker"),
            Permission.objects.get(codename="change_featuredspeaker"),
            Permission.objects.get(codename="view_featuredspeaker"),
            Permission.objects.get(codename="change_speakercategory"),
            Permission.objects.get(codename="view_speakercategory"),
        )
        self.client.force_login(user)

        profile_response = self.client.get(
            reverse("admin:home_featuredspeaker_changelist")
        )
        add_response = self.client.get(reverse("admin:home_featuredspeaker_add"))
        edit_response = self.client.get(
            reverse(
                "admin:home_featuredspeaker_change",
                args=[speaker.pk],
            )
        )
        category_response = self.client.get(
            reverse("admin:speakers_speakercategory_changelist")
        )

        self.assertEqual(profile_response.status_code, 200)
        self.assertEqual(add_response.status_code, 200)
        self.assertEqual(edit_response.status_code, 200)
        self.assertEqual(category_response.status_code, 200)

    def test_staff_without_profile_permission_cannot_view_speakers(self):
        user = get_user_model().objects.create_user(
            username="unprivileged-staff",
            password="safe-test-password",
            is_staff=True,
        )
        self.client.force_login(user)

        response = self.client.get(
            reverse("admin:home_featuredspeaker_changelist")
        )

        self.assertEqual(response.status_code, 403)
