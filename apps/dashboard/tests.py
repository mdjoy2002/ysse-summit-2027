"""Security, permission scoping, reports, and dashboard-query tests."""

import csv
from datetime import timedelta
from io import StringIO

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.competitions.models import Competition, CompetitionSubmission
from apps.contact.models import ContactInquiry, FAQ
from apps.about.models import TimelineMilestone, TimelinePeriod
from apps.home.models import FeaturedSpeaker, SummitEvent
from apps.partners.models import Sponsor, SponsorCategory
from apps.payments.models import PaymentMethod, PaymentTransaction
from apps.registrations.models import PassType, Registration
from apps.schedule.models import ScheduleSession
from apps.speakers.models import SpeakerCategory

from .access import MODERATOR_GROUP_NAME
from .services import dashboard_overview, dashboard_shortcuts


class DashboardFixture(TestCase):
    """Create staff accounts and provide permission-aware access helpers."""

    @classmethod
    def setUpTestData(cls):
        cls.event = SummitEvent.objects.get(key=SummitEvent.Key.MAIN)
        cls.pass_type = PassType.objects.get(category=PassType.Category.GENERAL)
        cls.pass_type.name = "General Delegate Pass"
        cls.pass_type.price = "1200.00"
        cls.pass_type.price_confirmed = True
        cls.pass_type.is_published = True
        cls.pass_type.is_available = True
        cls.pass_type.save()
        cls.moderator_group, _created = Group.objects.get_or_create(
            name=MODERATOR_GROUP_NAME
        )

    def make_staff(self, username, permissions=(), *, in_group=True):
        user = get_user_model().objects.create_user(
            username=username,
            email=f"{username}@example.org",
            password="dashboard-test-password",
            is_staff=True,
        )
        if in_group:
            user.groups.add(self.moderator_group)
        for permission_code in permissions:
            app_label, codename = permission_code.split(".", 1)
            permission = Permission.objects.get(
                content_type__app_label=app_label,
                codename=codename,
            )
            user.user_permissions.add(permission)
        return user

    def create_registration(self, username, **overrides):
        participant = get_user_model().objects.create_user(
            username=username,
            email=f"{username}@example.org",
            password="participant-password",
        )
        values = {
            "event": self.event,
            "participant": participant,
            "pass_type": self.pass_type,
            "full_name": f"{username.title()} Example",
            "contact_email": participant.email,
            "pass_name_snapshot": self.pass_type.name,
            "benefits_snapshot": self.pass_type.included_benefits,
            "amount_due": self.pass_type.price,
        }
        values.update(overrides)
        return Registration.objects.create(**values)


class DashboardAccessTests(DashboardFixture):
    def test_anonymous_user_is_redirected_to_login(self):
        for route in (
            "dashboard:overview",
            "dashboard:records_index",
            "dashboard:records_list",
            "dashboard:reports",
        ):
            with self.subTest(route=route):
                kwargs = (
                    {"app_label": "registrations", "model_name": "registration"}
                    if route == "dashboard:records_list"
                    else {}
                )
                response = self.client.get(reverse(route, kwargs=kwargs))
                self.assertEqual(response.status_code, 302)
                self.assertIn("/admin-2/", response["Location"])
                self.assertIn("/dashboard/", response["Location"])

    def test_admin_panel_two_sign_in_accepts_only_dashboard_authorized_users(self):
        self.assertContains(
            self.client.get(reverse("admin2-login")),
            "Admin Panel 2",
        )
        moderator = self.make_staff("admin-panel-two-moderator")
        moderator.set_password("test-moderator-password")
        moderator.save()
        response = self.client.post(
            reverse("admin2-login"),
            {
                "username": moderator.get_username(),
                "password": "test-moderator-password",
            },
        )
        self.assertRedirects(response, reverse("dashboard:overview"))

        ordinary = get_user_model().objects.create_user(
            username="admin-panel-two-ordinary",
            email="admin-panel-two-ordinary@example.org",
            password="test-ordinary-password",
        )
        self.client.logout()
        response = self.client.post(
            reverse("admin2-login"),
            {
                "username": ordinary.get_username(),
                "password": "test-ordinary-password",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "authorized Summit moderators")

    def test_django_admin_uses_the_requested_nondefault_path(self):
        self.assertEqual(reverse("admin:index"), "/ysse2014/")

    def test_non_staff_and_staff_outside_moderator_group_are_forbidden(self):
        non_staff = get_user_model().objects.create_user(
            username="ordinary-user",
            email="ordinary@example.org",
            password="dashboard-test-password",
        )
        self.client.force_login(non_staff)
        self.assertEqual(
            self.client.get(reverse("dashboard:overview")).status_code,
            403,
        )

        staff = self.make_staff("unassigned-staff", in_group=False)
        self.client.force_login(staff)
        for route in ("dashboard:overview", "dashboard:reports"):
            with self.subTest(route=route):
                self.assertEqual(
                    self.client.get(reverse(route)).status_code,
                    403,
                )

    def test_moderator_group_member_can_open_dashboard_but_only_authorized_data(self):
        moderator = self.make_staff("dashboard-moderator")
        self.client.force_login(moderator)

        response = self.client.get(reverse("dashboard:overview"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No dashboard data permissions")
        self.assertNotContains(response, "Total registrations")

    def test_superuser_can_open_dashboard_without_moderator_group_membership(self):
        user = get_user_model().objects.create_superuser(
            username="dashboard-superuser",
            email="dashboard-superuser@example.org",
            password="dashboard-test-password",
        )
        self.client.force_login(user)

        self.assertEqual(
            self.client.get(reverse("dashboard:overview")).status_code,
            200,
        )

    def test_admin_index_shows_admin_panel_two_only_to_dashboard_moderators(self):
        moderator = self.make_staff("admin-index-moderator")
        self.client.force_login(moderator)
        moderator_index = self.client.get(reverse("admin:index"))
        self.assertEqual(moderator_index.status_code, 200)
        self.assertContains(moderator_index, "Admin Panel 2")
        self.assertContains(
            moderator_index,
            reverse("dashboard:overview"),
        )

        staff_without_group = self.make_staff(
            "admin-index-non-moderator",
            in_group=False,
        )
        self.client.force_login(staff_without_group)
        restricted_index = self.client.get(reverse("admin:index"))
        self.assertEqual(restricted_index.status_code, 200)
        self.assertNotContains(restricted_index, "Admin Panel 2")

    def test_group_setup_command_assigns_baseline_permissions(self):
        call_command("setup_summit_moderators")
        group = Group.objects.get(name=MODERATOR_GROUP_NAME)

        self.assertTrue(
            group.permissions.filter(
                content_type__app_label="registrations",
                codename="view_registration",
            ).exists()
        )
        self.assertTrue(
            group.permissions.filter(
                content_type__app_label="competitions",
                codename="review_competitionsubmission",
            ).exists()
        )
        self.assertFalse(
            group.permissions.filter(
                content_type__app_label="registrations",
                codename="reconcile_registration_payments",
            ).exists()
        )


class DashboardStatisticsTests(DashboardFixture):
    def test_statistics_use_published_and_pending_database_records(self):
        registration = self.create_registration(
            "paid-participant",
            status=Registration.Status.CONFIRMED,
            payment_status=Registration.PaymentStatus.PAID,
        )
        self.create_registration(
            "pending-participant",
            payment_status=Registration.PaymentStatus.PENDING,
        )
        competition = Competition.objects.first()
        submission = CompetitionSubmission.objects.create(
            competition=competition,
            participant=registration.participant,
            status=CompetitionSubmission.Status.SUBMITTED,
            submitted_at=timezone.now(),
        )
        category = SpeakerCategory.objects.filter(is_published=True).first()
        speaker = FeaturedSpeaker.objects.create(
            full_name="Dashboard Speaker",
            official_designation="Guest",
            organization="YSSE",
            biography="Published speaker.",
            is_published=True,
        )
        speaker.categories.add(category)
        existing_upcoming_sessions = ScheduleSession.objects.filter(
            is_published=True,
            starts_at__gte=timezone.now(),
        ).count()
        ScheduleSession.objects.create(
            event=self.event,
            title="Future published session",
            starts_at=timezone.now() + timedelta(days=2),
            is_published=True,
        )
        sponsor_category = SponsorCategory.objects.first()
        Sponsor.objects.create(
            organization_name="Published Dashboard Sponsor",
            category=sponsor_category,
            is_published=True,
        )
        ContactInquiry.objects.create(
            name="Private Sender",
            email="private@example.org",
            subject="Dashboard inquiry",
            message="Private question.",
            consent_given=True,
        )
        PaymentTransaction.objects.create(
            registration=registration,
            provider="dashboard-test",
            provider_transaction_reference="dash-verified",
            method=PaymentMethod.BKASH,
            status=PaymentTransaction.Status.SUCCEEDED,
            amount=registration.amount_due,
        )
        moderator = self.make_staff(
            "statistics-moderator",
            (
                "registrations.view_registration",
                "competitions.view_competitionsubmission",
                "home.view_featuredspeaker",
                "schedule.view_schedulesession",
                "partners.view_sponsor",
                "contact.view_contactinquiry",
                "payments.view_paymenttransaction",
            ),
        )

        data = dashboard_overview(moderator)
        values = {card["label"]: card["value"] for card in data["cards"]}

        self.assertEqual(values["Total registrations"], 2)
        self.assertEqual(values["Paid registrations"], 1)
        self.assertEqual(values["Pending payments"], 1)
        self.assertEqual(values["Competition submissions"], 1)
        self.assertEqual(values["Pending competition reviews"], 1)
        self.assertEqual(values["Published speakers"], 1)
        self.assertEqual(
            values["Upcoming schedule entries"],
            existing_upcoming_sessions + 1,
        )
        self.assertEqual(values["Published sponsors"], 1)
        self.assertEqual(values["Unread contact inquiries"], 1)
        self.assertEqual(values["Payment transactions"], 1)
        self.assertEqual(submission.status, CompetitionSubmission.Status.SUBMITTED)
        self.assertEqual(
            sum(item["registration_count"] for item in data["pass_breakdown"]),
            2,
        )

    def test_empty_dashboard_statistics_are_zero_and_hide_unpermitted_metrics(self):
        moderator = self.make_staff(
            "empty-statistics-moderator",
            ("registrations.view_registration",),
        )

        data = dashboard_overview(moderator)
        values = {card["label"]: card["value"] for card in data["cards"]}

        self.assertEqual(values["Total registrations"], 0)
        self.assertEqual(values["Paid registrations"], 0)
        self.assertEqual(values["Pending payments"], 0)
        self.assertNotIn("Published speakers", values)
        self.assertEqual(
            sum(item["registration_count"] for item in data["pass_breakdown"]),
            0,
        )

    def test_shortcuts_are_filtered_by_each_admin_model_permission(self):
        moderator = self.make_staff(
            "partial-shortcut-moderator",
            ("registrations.view_registration",),
        )

        shortcuts = dashboard_shortcuts(moderator)
        labels = {item["label"] for item in shortcuts}

        self.assertNotIn("Django Admin", labels)
        self.assertIn("Registrations", labels)
        self.assertNotIn("Newsletter subscribers", labels)
        self.assertNotIn("Payment records", labels)
        for shortcut in shortcuts:
            self.assertTrue(shortcut["url"].startswith("/dashboard/"))


class DashboardRecordBrowserTests(DashboardFixture):
    def test_directory_only_shows_models_the_user_may_view(self):
        moderator = self.make_staff(
            "record-directory-moderator",
            (
                "registrations.view_registration",
                "contact.view_contactinquiry",
            ),
        )
        self.client.force_login(moderator)

        response = self.client.get(reverse("dashboard:records_index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "All managed records")
        self.assertContains(
            response,
            reverse(
                "dashboard:records_list",
                args=("registrations", "registration"),
            ),
        )
        self.assertContains(
            response,
            reverse(
                "dashboard:records_list",
                args=("contact", "contactinquiry"),
            ),
        )
        self.assertNotContains(response, "newsletter subscriptions")
        self.assertNotContains(response, "payment transactions")

    def test_model_list_uses_admin_columns_search_and_permission(self):
        registration = self.create_registration(
            "record-list-participant",
            full_name="Record List Participant",
        )
        moderator = self.make_staff(
            "record-list-moderator",
            ("registrations.view_registration",),
        )
        self.client.force_login(moderator)
        url = reverse(
            "dashboard:records_list",
            args=("registrations", "registration"),
        )

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, registration.reference)
        self.assertContains(response, "Record List Participant")
        self.assertNotContains(response, registration.contact_email)
        self.assertNotContains(response, reverse("admin:index"))
        self.assertContains(response, "Payment status")

        matching = self.client.get(url, {"q": "Record List Participant"})
        self.assertEqual(matching.status_code, 200)
        self.assertContains(matching, registration.reference)

        denied = self.client.get(
            reverse(
                "dashboard:records_list",
                args=("contact", "newslettersubscription"),
            )
        )
        self.assertEqual(denied.status_code, 403)

    def test_record_details_stay_inside_dashboard_and_follow_view_permission(self):
        registration = self.create_registration(
            "record-detail-participant",
            full_name="Record Detail Participant",
        )
        moderator = self.make_staff(
            "record-detail-moderator",
            ("registrations.view_registration",),
        )
        self.client.force_login(moderator)

        response = self.client.get(
            reverse(
                "dashboard:records_detail",
                args=("registrations", "registration", registration.pk),
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Record Detail Participant")
        self.assertContains(response, "Payment verification audit")
        self.assertNotContains(response, reverse("admin:index"))
        self.assertContains(
            response,
            reverse(
                "dashboard:records_list",
                args=("registrations", "registration"),
            ),
        )

        denied = self.client.get(
            reverse(
                "dashboard:records_detail",
                args=("contact", "newslettersubscription", 1),
            )
        )
        self.assertEqual(denied.status_code, 403)

    def test_superuser_can_create_and_edit_records_inside_dashboard(self):
        moderator = get_user_model().objects.create_superuser(
            username="custom-ui-editor",
            email="custom-ui-editor@example.org",
            password="test-password",
        )
        self.client.force_login(moderator)
        add_url = reverse(
            "dashboard:records_create",
            args=("contact", "faq"),
        )

        add_page = self.client.get(add_url)
        self.assertEqual(add_page.status_code, 200)
        self.assertContains(add_page, "Save record")
        created = self.client.post(
            add_url,
            {
                "question": "How can I contact the summit team?",
                "answer": "Use the official contact page.",
                "category": "general",
                "display_order": "1",
            },
        )
        self.assertEqual(created.status_code, 302)
        faq = FAQ.objects.get(question="How can I contact the summit team?")
        self.assertRedirects(
            created,
            reverse(
                "dashboard:records_detail",
                args=("contact", "faq", faq.pk),
            ),
        )

        changed = self.client.post(
            reverse(
                "dashboard:records_edit",
                args=("contact", "faq", faq.pk),
            ),
            {
                "question": "How do I reach the team?",
                "answer": "Send a private inquiry.",
                "category": "general",
                "display_order": "2",
            },
        )
        self.assertEqual(changed.status_code, 302)
        faq.refresh_from_db()
        self.assertEqual(faq.question, "How do I reach the team?")
        self.assertEqual(faq.display_order, 2)

    def test_superuser_can_save_admin_inline_records_in_dashboard(self):
        moderator = get_user_model().objects.create_superuser(
            username="inline-superuser",
            email="inline@example.org",
            password="test-password",
        )
        self.client.force_login(moderator)
        url = reverse(
            "dashboard:records_create",
            args=("about", "timelineperiod"),
        )
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        entry = page.context["inline_entries"][0]
        formset = entry["formset"]
        response = self.client.post(
            url,
            {
                "start_year": "2020",
                "end_year": "2021",
                "title": "Summit launch",
                "sort_order": "1",
                "is_published": "on",
                f"{formset.prefix}-TOTAL_FORMS": str(formset.total_form_count()),
                f"{formset.prefix}-INITIAL_FORMS": str(formset.initial_form_count()),
                f"{formset.prefix}-MIN_NUM_FORMS": str(formset.min_num),
                f"{formset.prefix}-MAX_NUM_FORMS": str(formset.max_num),
                f"{formset.prefix}-0-description": "The first summit was organized.",
                f"{formset.prefix}-0-sort_order": "1",
            },
        )

        self.assertEqual(response.status_code, 302)
        period = TimelinePeriod.objects.get(title="Summit launch")
        self.assertEqual(
            TimelineMilestone.objects.get(period=period).description,
            "The first summit was organized.",
        )

    def test_superuser_can_delete_a_record_after_dashboard_confirmation(self):
        faq = FAQ.objects.create(
            question="Temporary question",
            answer="Temporary answer",
        )
        moderator = get_user_model().objects.create_superuser(
            username="custom-ui-deleter",
            email="custom-ui-deleter@example.org",
            password="test-password",
        )
        self.client.force_login(moderator)
        delete_url = reverse(
            "dashboard:records_delete",
            args=("contact", "faq", faq.pk),
        )

        confirmation = self.client.get(delete_url)
        self.assertEqual(confirmation.status_code, 200)
        self.assertContains(confirmation, "Confirm delete")

        deleted = self.client.post(delete_url, {"confirm": "yes"})
        self.assertEqual(deleted.status_code, 302)
        self.assertFalse(FAQ.objects.filter(pk=faq.pk).exists())

    def test_superuser_can_confirm_bulk_delete_inside_dashboard(self):
        first = FAQ.objects.create(question="Bulk delete A", answer="A")
        second = FAQ.objects.create(question="Bulk delete B", answer="B")
        moderator = get_user_model().objects.create_superuser(
            username="bulk-delete-superuser",
            email="bulk-delete@example.org",
            password="test-password",
        )
        self.client.force_login(moderator)
        list_url = reverse("dashboard:records_list", args=("contact", "faq"))

        confirmation = self.client.post(
            list_url,
            {
                "action": "__dashboard_delete_selected__",
                "_selected_action": [str(first.pk), str(second.pk)],
            },
        )
        self.assertEqual(confirmation.status_code, 200)
        self.assertContains(confirmation, "Confirm delete")

        deleted = self.client.post(
            reverse("dashboard:records_bulk_delete", args=("contact", "faq")),
            {
                "confirm": "yes",
                "selected_ids": [str(first.pk), str(second.pk)],
            },
        )
        self.assertEqual(deleted.status_code, 302)
        self.assertFalse(FAQ.objects.filter(pk__in=(first.pk, second.pk)).exists())

    def test_superuser_can_use_admin_list_editable_fields_in_dashboard(self):
        faq = FAQ.objects.create(
            question="List edit question",
            answer="List edit answer",
            display_order=0,
        )
        moderator = get_user_model().objects.create_superuser(
            username="list-edit-superuser",
            email="list-edit@example.org",
            password="test-password",
        )
        self.client.force_login(moderator)
        url = reverse("dashboard:records_list", args=("contact", "faq"))
        response = self.client.get(url)
        formset = response.context["list_editable_formset"]

        edited = self.client.post(
            url,
            {
                f"{formset.prefix}-TOTAL_FORMS": str(formset.total_form_count()),
                f"{formset.prefix}-INITIAL_FORMS": str(formset.initial_form_count()),
                f"{formset.prefix}-MIN_NUM_FORMS": "0",
                f"{formset.prefix}-MAX_NUM_FORMS": "1000",
                f"{formset.prefix}-0-id": str(faq.pk),
                f"{formset.prefix}-0-display_order": "7",
                f"{formset.prefix}-0-is_published": "on",
                "_save": "Save",
            },
        )

        self.assertEqual(edited.status_code, 302)
        faq.refresh_from_db()
        self.assertEqual(faq.display_order, 7)
        self.assertTrue(faq.is_published)

    def test_superuser_can_run_admin_bulk_export_inside_dashboard(self):
        inquiry = ContactInquiry.objects.create(
            name="Admin action test",
            email="action@example.org",
            subject="Custom interface",
            message="Testing Admin action integration.",
            consent_given=True,
        )
        moderator = get_user_model().objects.create_superuser(
            username="action-superuser",
            email="action@example.org",
            password="test-password",
        )
        self.client.force_login(moderator)

        response = self.client.post(
            reverse("dashboard:records_list", args=("contact", "contactinquiry")),
            {
                "action": "export_selected_inquiries",
                "index": "0",
                "select_across": "0",
                "_selected_action": [str(inquiry.pk)],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        self.assertIn(b"Admin action test", response.content)

    def test_user_record_details_never_expose_the_password_hash(self):
        user = get_user_model().objects.create_user(
            username="private-password-hash-user",
            email="private-hash@example.org",
            password="long-test-password",
        )
        moderator = get_user_model().objects.create_superuser(
            username="password-hash-viewer",
            email="hash-viewer@example.org",
            password="test-password",
        )
        self.client.force_login(moderator)

        response = self.client.get(
            reverse(
                "dashboard:records_detail",
                args=("auth", "user", user.pk),
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, user.password)
        self.assertContains(response, "Password value is hidden.")

    def test_moderator_without_change_permission_cannot_open_record_editor(self):
        faq = FAQ.objects.create(
            question="Read-only question",
            answer="Read-only answer",
        )
        moderator = self.make_staff(
            "read-only-dashboard-moderator",
            ("contact.view_faq",),
        )
        self.client.force_login(moderator)

        response = self.client.get(
            reverse(
                "dashboard:records_edit",
                args=("contact", "faq", faq.pk),
            )
        )

        self.assertEqual(response.status_code, 403)

    def test_registered_admin_models_render_in_dashboard_directory(self):
        moderator = get_user_model().objects.create_superuser(
            username="all-records-superuser",
            email="all-records@example.org",
            password="test-password",
        )
        self.client.force_login(moderator)

        directory = self.client.get(reverse("dashboard:records_index"))
        self.assertEqual(directory.status_code, 200)
        self.assertNotContains(directory, reverse("admin:index"))
        self.assertContains(directory, "groups")
        self.assertContains(directory, "users")
        self.assertEqual(
            len(dashboard_shortcuts(moderator)),
            len(admin.site._registry),
        )

        for model in admin.site._registry:
            with self.subTest(model=model._meta.label):
                response = self.client.get(
                    reverse(
                        "dashboard:records_list",
                        args=(model._meta.app_label, model._meta.model_name),
                    )
                )
                self.assertEqual(response.status_code, 200)

                add_response = self.client.get(
                    reverse(
                        "dashboard:records_create",
                        args=(model._meta.app_label, model._meta.model_name),
                    )
                )
                self.assertIn(add_response.status_code, (200, 403))

                model_admin = admin.site._registry[model]
                obj = model_admin.get_queryset(response.wsgi_request).first()
                if obj is not None:
                    detail_response = self.client.get(
                        reverse(
                            "dashboard:records_detail",
                            args=(
                                model._meta.app_label,
                                model._meta.model_name,
                                obj.pk,
                            ),
                        )
                    )
                    self.assertEqual(detail_response.status_code, 200)
                    edit_response = self.client.get(
                        reverse(
                            "dashboard:records_edit",
                            args=(
                                model._meta.app_label,
                                model._meta.model_name,
                                obj.pk,
                            ),
                        )
                    )
                    self.assertIn(edit_response.status_code, (200, 403))

    def test_unknown_unregistered_record_type_is_not_found(self):
        moderator = self.make_staff("unknown-record-moderator")
        self.client.force_login(moderator)

        response = self.client.get(
            reverse(
                "dashboard:records_list",
                args=("not-an-app", "not-a-model"),
            )
        )

        self.assertEqual(response.status_code, 404)


class DashboardReportTests(DashboardFixture):
    def test_registration_filters_and_pagination_hide_personal_information(self):
        first = self.create_registration(
            "filtered-general",
            submitted_at=timezone.now(),
        )
        for index in range(21):
            self.create_registration(
                f"page-participant-{index}",
                submitted_at=timezone.now() - timedelta(minutes=index),
            )
        moderator = self.make_staff(
            "registration-table-moderator",
            ("registrations.view_registration",),
        )
        self.client.force_login(moderator)

        filtered = self.client.get(
            reverse("dashboard:overview"),
            {
                "pass_category": PassType.Category.GENERAL,
                "payment_status": Registration.PaymentStatus.PENDING,
                "date_from": timezone.localdate().isoformat(),
                "date_to": timezone.localdate().isoformat(),
            },
        )

        self.assertEqual(filtered.status_code, 200)
        self.assertContains(filtered, first.reference)
        self.assertNotContains(filtered, first.contact_email)
        self.assertContains(filtered, "Filter records")

        page_two = self.client.get(
            reverse("dashboard:overview"),
            {"page": 2},
        )
        self.assertEqual(page_two.status_code, 200)
        self.assertContains(page_two, "Page 2 of 2")

    def test_invalid_registration_filter_range_is_reported_without_data(self):
        moderator = self.make_staff(
            "invalid-filter-moderator",
            ("registrations.view_registration",),
        )
        self.client.force_login(moderator)

        response = self.client.get(
            reverse("dashboard:overview"),
            {"date_from": "2026-10-27", "date_to": "2026-10-01"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "End date must be on or after the start date.")

    def test_date_filtered_report_and_csv_export_omit_participant_pii(self):
        registration = self.create_registration(
            "report-participant",
            submitted_at=timezone.now(),
        )
        moderator = self.make_staff(
            "report-moderator",
            (
                "registrations.view_registration",
                "payments.view_paymenttransaction",
                "competitions.view_competitionsubmission",
            ),
        )
        self.client.force_login(moderator)
        date = timezone.localdate().isoformat()

        report = self.client.get(
            reverse("dashboard:reports"),
            {"date_from": date, "date_to": date},
        )
        csv_response = self.client.get(
            reverse("dashboard:reports"),
            {"date_from": date, "date_to": date, "format": "csv"},
        )

        self.assertEqual(report.status_code, 200)
        self.assertContains(report, "Registration payments")
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn("text/csv", csv_response["Content-Type"])
        rows = list(csv.reader(StringIO(csv_response.content.decode("utf-8"))))
        self.assertEqual(rows[0], ["section", "status", "count", "total amount"])
        content = csv_response.content.decode("utf-8")
        pending_registration = next(
            row
            for row in rows
            if row[0] == "Registrations" and row[1] == "Pending"
        )
        self.assertEqual(pending_registration[2], "1")
        self.assertNotIn(registration.full_name, content)
        self.assertNotIn(registration.contact_email, content)

    def test_report_invalid_dates_return_a_validation_response(self):
        moderator = self.make_staff(
            "report-validation-moderator",
            ("registrations.view_registration",),
        )
        self.client.force_login(moderator)

        response = self.client.get(
            reverse("dashboard:reports"),
            {"date_from": "2026-11-01", "date_to": "2026-10-01"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response,
            "End date must be on or after the start date.",
            status_code=400,
        )
