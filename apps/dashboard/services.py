"""Permission-aware dashboard queries and privacy-minimized reports."""

import logging
from datetime import datetime, time, timedelta

from django.apps import apps
from django.contrib import admin
from django.contrib.admin.utils import display_for_field, display_for_value, lookup_field
from django.db.models import Count, Sum
from django.urls import reverse
from django.utils import timezone

from apps.competitions.models import CompetitionSubmission
from apps.contact.models import ContactInquiry
from apps.home.models import FeaturedSpeaker
from apps.partners.models import Sponsor
from apps.payments.models import PaymentTransaction
from apps.registrations.models import PassType, Registration
from apps.schedule.models import ScheduleSession


logger = logging.getLogger("ysse.dashboard")

def admin_model_catalog(request):
    """Return registered Django Admin models and allowed management actions."""
    catalog = {}
    for model, model_admin in admin.site._registry.items():
        if not model_admin.has_module_permission(request):
            continue
        can_view = model_admin.has_view_or_change_permission(request)
        can_add = model_admin.has_add_permission(request)
        if not can_view and not can_add:
            continue

        app_config = apps.get_app_config(model._meta.app_label)
        catalog.setdefault(
            model._meta.app_label,
            {
                "label": app_config.verbose_name,
                "models": [],
            },
        )["models"].append(
            {
                "name": model._meta.verbose_name_plural,
                "count": (
                    model_admin.get_queryset(request).count()
                    if can_view
                    else None
                ),
                "url": (
                    reverse(
                        "dashboard:records_list",
                        args=(model._meta.app_label, model._meta.model_name),
                    )
                    if can_view
                    else reverse(
                        "dashboard:records_create",
                        args=(model._meta.app_label, model._meta.model_name),
                    )
                ),
                "can_add": can_add,
                "add_url": (
                    reverse(
                        "dashboard:records_create",
                        args=(model._meta.app_label, model._meta.model_name),
                    )
                    if can_add
                    else None
                ),
            }
        )

    return sorted(
        (
            {
                **app,
                "models": sorted(
                    app["models"],
                    key=lambda item: str(item["name"]).casefold(),
                ),
            }
            for app in catalog.values()
        ),
        key=lambda app: str(app["label"]).casefold(),
    )


def local_date_boundary(day, *, next_day=False):
    """Convert a local calendar date into an aware inclusive/exclusive bound."""
    if day is None:
        return None
    boundary_day = day + timedelta(days=1) if next_day else day
    boundary = datetime.combine(boundary_day, time.min)
    if timezone.is_naive(boundary):
        return timezone.make_aware(boundary, timezone.get_current_timezone())
    return boundary


def dashboard_shortcuts(user, request=None):
    """Return custom dashboard pages the current staff member may view."""
    if user.is_superuser:
        shortcuts = []
        for model in sorted(
            admin.site._registry,
            key=lambda item: (
                str(apps.get_app_config(item._meta.app_label).verbose_name).casefold(),
                str(item._meta.verbose_name_plural).casefold(),
            ),
        ):
            model_admin = admin.site._registry[model]
            if request is not None and (
                not model_admin.has_module_permission(request)
                or not (
                    model_admin.has_view_or_change_permission(request)
                    or model_admin.has_add_permission(request)
                )
            ):
                continue
            can_view = (
                model_admin.has_view_or_change_permission(request)
                if request is not None
                else True
            )
            shortcuts.append(
                {
                    "label": model._meta.verbose_name_plural,
                    "description": apps.get_app_config(
                        model._meta.app_label
                    ).verbose_name,
                    "url": reverse(
                        (
                            "dashboard:records_list"
                            if can_view
                            else "dashboard:records_create"
                        ),
                        args=(model._meta.app_label, model._meta.model_name),
                    ),
                }
            )
        return shortcuts

    items = [
        {
            "label": "Registrations",
            "description": "Review participant registrations and pass records.",
            "app_label": "registrations",
            "model_name": "registration",
            "permission": "registrations.view_registration",
        },
        {
            "label": "Competitions",
            "description": "Manage competition configuration.",
            "app_label": "competitions",
            "model_name": "competition",
            "permission": "competitions.view_competition",
        },
        {
            "label": "Competition submissions",
            "description": "Review submitted competition entries.",
            "app_label": "competitions",
            "model_name": "competitionsubmission",
            "permission": "competitions.view_competitionsubmission",
        },
        {
            "label": "Speakers",
            "description": "Manage published speaker profiles and categories.",
            "app_label": "home",
            "model_name": "featuredspeaker",
            "permission": "home.view_featuredspeaker",
        },
        {
            "label": "Event schedule",
            "description": "Edit and publish agenda sessions.",
            "app_label": "schedule",
            "model_name": "schedulesession",
            "permission": "schedule.view_schedulesession",
        },
        {
            "label": "Sponsors",
            "description": "Manage sponsor profiles and publication.",
            "app_label": "partners",
            "model_name": "sponsor",
            "permission": "partners.view_sponsor",
        },
        {
            "label": "Contact inquiries",
            "description": "Review private visitor messages.",
            "app_label": "contact",
            "model_name": "contactinquiry",
            "permission": "contact.view_contactinquiry",
        },
        {
            "label": "Newsletter subscribers",
            "description": "Access subscriber records only if explicitly permitted.",
            "app_label": "contact",
            "model_name": "newslettersubscription",
            "permission": "contact.view_newslettersubscription",
        },
        {
            "label": "Payment records",
            "description": "Inspect read-only provider transaction records.",
            "app_label": "payments",
            "model_name": "paymenttransaction",
            "permission": "payments.view_paymenttransaction",
        },
    ]
    shortcuts = []
    for item in items:
        if user.has_perm(item["permission"]):
            shortcuts.append(
                {
                    "label": item["label"],
                    "description": item["description"],
                    "url": reverse(
                        "dashboard:records_list",
                        args=(item["app_label"], item["model_name"]),
                    ),
                }
            )
    return shortcuts


def dashboard_can_edit_record(request, model_admin, obj):
    """Match editable record links to Django Admin's effective form fields."""
    if not model_admin.has_change_permission(request, obj):
        return False
    form_class = model_admin.get_form(request, obj, change=True)
    if form_class.base_fields:
        return True
    return any(
        entry["formset"].form.base_fields
        for entry in dashboard_inline_formsets(request, model_admin, obj)
    )


def dashboard_record_rows(
    request,
    changelist,
    model_admin,
    *,
    editable_forms=None,
):
    """Format configured Admin columns for safe display in dashboard tables."""
    rows = []
    empty_value_display = model_admin.get_empty_value_display()
    editable_fields = set(model_admin.list_editable)
    forms_by_pk = {
        form.instance.pk: form
        for form in editable_forms or ()
        if form.instance.pk is not None
    }
    for obj in changelist.result_list:
        cells = []
        editable_form = forms_by_pk.get(obj.pk)
        for column in changelist.list_display:
            if editable_form is not None and column in editable_fields:
                cells.append({"bound_field": editable_form[column]})
                continue
            field, attribute, value = lookup_field(column, obj, model_admin)
            if field is not None:
                display_value = display_for_field(
                    value,
                    field,
                    empty_value_display,
                )
            else:
                display_value = display_for_value(
                    value,
                    empty_value_display,
                    boolean=getattr(attribute, "boolean", False),
                )
            cells.append(display_value)
        rows.append(
            {
                "object": obj,
                "url": reverse(
                    "dashboard:records_detail",
                    args=(
                        obj._meta.app_label,
                        obj._meta.model_name,
                        obj.pk,
                    ),
                ),
                "edit_url": reverse(
                    "dashboard:records_edit",
                    args=(obj._meta.app_label, obj._meta.model_name, obj.pk),
                ),
                "can_change": dashboard_can_edit_record(
                    request,
                    model_admin,
                    obj,
                ),
                "can_delete": model_admin.has_delete_permission(request, obj),
                "cells": cells,
                "form": editable_form,
            }
        )
    return rows


def dashboard_record_fields(request, model_admin, obj):
    """Format only fields configured for display by this model's Admin."""
    details = []
    empty_value_display = model_admin.get_empty_value_display()
    for fieldset_name, fieldset_options in model_admin.get_fieldsets(request, obj):
        for row in fieldset_options["fields"]:
            field_names = row if isinstance(row, (tuple, list)) else (row,)
            for field_name in field_names:
                field, attribute, value = lookup_field(
                    field_name,
                    obj,
                    model_admin,
                )
                if field is not None:
                    label = field.verbose_name
                    display_value = (
                        "Password value is hidden."
                        if field.name == "password"
                        else display_for_field(
                            value,
                            field,
                            empty_value_display,
                        )
                    )
                else:
                    label = getattr(attribute, "short_description", field_name)
                    display_value = display_for_value(
                        value,
                        empty_value_display,
                        boolean=getattr(attribute, "boolean", False),
                    )
                details.append(
                    {
                        "fieldset": fieldset_name,
                        "label": str(label).replace("_", " ").capitalize(),
                        "value": display_value,
                    }
                )
    return details


def dashboard_form_sections(request, model_admin, obj, form):
    """Build Admin-fieldset groups while keeping readonly values non-editable."""
    sections = []
    readonly_fields = set(model_admin.get_readonly_fields(request, obj))
    empty_value_display = model_admin.get_empty_value_display()

    for section_name, options in model_admin.get_fieldsets(request, obj):
        section = {"name": section_name, "fields": []}
        for row in options["fields"]:
            field_names = row if isinstance(row, (tuple, list)) else (row,)
            for field_name in field_names:
                if field_name in form.fields:
                    section["fields"].append(
                        {
                            "bound_field": form[field_name],
                            "label": form[field_name].label,
                        }
                    )
                elif field_name in readonly_fields and obj is not None:
                    field, attribute, value = lookup_field(
                        field_name,
                        obj,
                        model_admin,
                    )
                    label = (
                        field.verbose_name
                        if field is not None
                        else getattr(attribute, "short_description", field_name)
                    )
                    display_value = (
                        display_for_field(value, field, empty_value_display)
                        if field is not None
                        else display_for_value(
                            value,
                            empty_value_display,
                            boolean=getattr(attribute, "boolean", False),
                        )
                    )
                    section["fields"].append(
                        {
                            "label": str(label).replace("_", " ").capitalize(),
                            "value": display_value,
                            "readonly": True,
                        }
                    )
        if section["fields"]:
            sections.append(section)
    return sections


def dashboard_inline_formsets(
    request,
    model_admin,
    obj,
    *,
    data=None,
    files=None,
    parent_instance=None,
):
    """Construct permitted Django Admin inline formsets for dashboard forms."""
    formsets = []
    prefixes = {}
    for formset_class, inline in model_admin.get_formsets_with_inlines(
        request,
        obj,
    ):
        base_prefix = formset_class.get_default_prefix()
        prefixes[base_prefix] = prefixes.get(base_prefix, 0) + 1
        prefix = base_prefix
        if prefixes[base_prefix] > 1 or not base_prefix:
            prefix = f"{base_prefix}-{prefixes[base_prefix]}"
        formset = formset_class(
            data=data,
            files=files,
            instance=parent_instance if parent_instance is not None else obj,
            prefix=prefix,
        )
        readonly_fields = set(inline.get_readonly_fields(request, obj))
        rows = []
        for inline_form in formset.forms:
            fields = []
            for field_name in inline.get_fields(request, obj):
                if field_name in inline_form.fields:
                    fields.append(
                        {
                            "bound_field": inline_form[field_name],
                            "label": inline_form[field_name].label,
                        }
                    )
                elif field_name in readonly_fields and inline_form.instance.pk:
                    field, attribute, value = lookup_field(
                        field_name,
                        inline_form.instance,
                        inline,
                    )
                    label = (
                        field.verbose_name
                        if field is not None
                        else getattr(attribute, "short_description", field_name)
                    )
                    display_value = (
                        display_for_field(
                            value,
                            field,
                            inline.get_empty_value_display(),
                        )
                        if field is not None
                        else display_for_value(
                            value,
                            inline.get_empty_value_display(),
                            boolean=getattr(attribute, "boolean", False),
                        )
                    )
                    fields.append(
                        {
                            "label": str(label).replace("_", " ").capitalize(),
                            "value": display_value,
                            "readonly": True,
                        }
                    )
            rows.append({"form": inline_form, "fields": fields})
        formsets.append(
            {
                "formset": formset,
                "rows": rows,
                "title": inline.verbose_name_plural,
                "can_add": inline.has_add_permission(request, obj),
                "can_change": inline.has_change_permission(request, obj),
                "can_delete": inline.has_delete_permission(request, obj),
            }
        )
    return formsets


def dashboard_overview(user, request=None):
    """Build real, permission-gated metrics without loading participant PII."""
    cards = []
    pass_breakdown = []

    if user.has_perm("registrations.view_registration"):
        cards.extend(
            (
                {
                    "label": "Total registrations",
                    "value": Registration.objects.count(),
                    "tone": "forest",
                },
                {
                    "label": "Paid registrations",
                    "value": Registration.objects.filter(
                        payment_status=Registration.PaymentStatus.PAID
                    ).count(),
                    "tone": "green",
                },
                {
                    "label": "Pending payments",
                    "value": Registration.objects.filter(
                        payment_status=Registration.PaymentStatus.PENDING
                    ).count(),
                    "tone": "amber",
                },
            )
        )
        pass_breakdown = list(
            PassType.objects.annotate(
                registration_count=Count("registrations")
            ).values("category", "name", "registration_count")
        )
        category_labels = dict(PassType.Category.choices)
        for item in pass_breakdown:
            item["category_display"] = category_labels.get(
                item["category"],
                item["category"],
            )

    if user.has_perm("competitions.view_competitionsubmission"):
        cards.extend(
            (
                {
                    "label": "Competition submissions",
                    "value": CompetitionSubmission.objects.exclude(
                        status=CompetitionSubmission.Status.DRAFT
                    ).count(),
                    "tone": "blue",
                },
                {
                    "label": "Pending competition reviews",
                    "value": CompetitionSubmission.objects.filter(
                        status__in=(
                            CompetitionSubmission.Status.SUBMITTED,
                            CompetitionSubmission.Status.UNDER_REVIEW,
                        )
                    ).count(),
                    "tone": "amber",
                },
            )
        )

    if user.has_perm("home.view_featuredspeaker"):
        cards.append(
            {
                "label": "Published speakers",
                "value": FeaturedSpeaker.objects.published().count(),
                "tone": "purple",
            }
        )
    if user.has_perm("schedule.view_schedulesession"):
        cards.append(
            {
                "label": "Upcoming schedule entries",
                "value": ScheduleSession.objects.filter(
                    is_published=True,
                    starts_at__gte=timezone.now(),
                ).count(),
                "tone": "blue",
            }
        )
    if user.has_perm("partners.view_sponsor"):
        cards.append(
            {
                "label": "Published sponsors",
                "value": Sponsor.objects.filter(is_published=True).count(),
                "tone": "green",
            }
        )
    if user.has_perm("contact.view_contactinquiry"):
        cards.append(
            {
                "label": "Unread contact inquiries",
                "value": ContactInquiry.objects.filter(
                    status=ContactInquiry.Status.NEW
                ).count(),
                "tone": "amber",
            }
        )
    if user.has_perm("payments.view_paymenttransaction"):
        cards.append(
            {
                "label": "Payment transactions",
                "value": PaymentTransaction.objects.count(),
                "tone": "forest",
            }
        )

    return {
        "cards": cards,
        "pass_breakdown": pass_breakdown,
        "shortcuts": dashboard_shortcuts(user, request),
        "has_registration_access": user.has_perm(
            "registrations.view_registration"
        ),
        "has_dashboard_data_access": bool(cards),
    }


def _filter_date_range(queryset, field, date_from, date_to):
    """Apply local timezone-aware date bounds to an authorized queryset."""
    if date_from:
        queryset = queryset.filter(
            **{f"{field}__gte": local_date_boundary(date_from)}
        )
    if date_to:
        queryset = queryset.filter(
            **{f"{field}__lt": local_date_boundary(date_to, next_day=True)}
        )
    return queryset


def report_rows(user, *, date_from=None, date_to=None):
    """Return aggregate report rows for datasets the user may inspect."""
    rows = []
    if user.has_perm("registrations.view_registration"):
        registrations = _filter_date_range(
            Registration.objects.all(),
            "submitted_at",
            date_from,
            date_to,
        )
        rows.extend(
            {
                "section": "Registrations",
                "status": label,
                "count": registrations.filter(status=value).count(),
                "amount": "",
            }
            for value, label in Registration.Status.choices
        )
        rows.extend(
            {
                "section": "Registration payments",
                "status": label,
                "count": registrations.filter(payment_status=value).count(),
                "amount": "",
            }
            for value, label in Registration.PaymentStatus.choices
        )

    if user.has_perm("payments.view_paymenttransaction"):
        payments = _filter_date_range(
            PaymentTransaction.objects.all(),
            "created_at",
            date_from,
            date_to,
        )
        payment_totals = {
            item["status"]: item
            for item in payments.values("status").annotate(
                count=Count("pk"),
                total_amount=Sum("amount"),
            )
        }
        rows.extend(
            {
                "section": "Payment transactions",
                "status": label,
                "count": payment_totals.get(value, {}).get("count", 0),
                "amount": payment_totals.get(value, {}).get("total_amount") or "",
            }
            for value, label in PaymentTransaction.Status.choices
        )

    if user.has_perm("competitions.view_competitionsubmission"):
        submissions = _filter_date_range(
            CompetitionSubmission.objects.exclude(
                status=CompetitionSubmission.Status.DRAFT
            ),
            "submitted_at",
            date_from,
            date_to,
        )
        rows.extend(
            {
                "section": "Competition submissions",
                "status": label,
                "count": submissions.filter(status=value).count(),
                "amount": "",
            }
            for value, label in CompetitionSubmission.Status.choices
            if value != CompetitionSubmission.Status.DRAFT
        )
    return rows


def log_report_export(*, actor_id, row_count):
    """Log export activity without including participant or payment details."""
    logger.info(
        "Dashboard aggregate CSV exported actor_id=%s row_count=%s",
        actor_id,
        row_count,
    )
