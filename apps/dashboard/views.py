"""Protected moderator dashboard and aggregate report endpoints."""

import csv
from django.contrib import admin
from django.contrib.admin.views.main import IncorrectLookupParameters
from django.contrib.admin.templatetags.admin_list import result_headers
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import router, transaction
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.registrations.models import Registration

from .access import has_dashboard_access
from .forms import RegistrationFilterForm, ReportFilterForm
from .services import (
    admin_model_catalog,
    dashboard_can_edit_record,
    dashboard_form_sections,
    dashboard_inline_formsets,
    dashboard_record_fields,
    dashboard_record_rows,
    dashboard_overview,
    dashboard_shortcuts,
    log_report_export,
    report_rows,
)


REGISTRATION_PAGE_SIZE = 20
dashboard_login_required = login_required(login_url="admin2-login")


class ModeratorLoginView(LoginView):
    """Authenticate only staff accounts permitted to use Admin Panel 2."""

    template_name = "dashboard/login.html"
    redirect_authenticated_user = False

    def dispatch(self, request, *args, **kwargs):
        if has_dashboard_access(request.user):
            return redirect("dashboard:overview")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        if not has_dashboard_access(form.get_user()):
            form.add_error(
                None,
                "This sign-in is for authorized Summit moderators and administrators.",
            )
            return self.form_invalid(form)
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("dashboard:overview")


moderator_login = ModeratorLoginView.as_view()


def _require_dashboard_access(request):
    if not has_dashboard_access(request.user):
        raise PermissionDenied


@dashboard_login_required
def overview(request):
    """Show staff-authorized aggregate metrics and a PII-minimized register."""
    _require_dashboard_access(request)
    context = dashboard_overview(request.user, request)
    context["registration_filter"] = None
    context["registration_page"] = None

    if context["has_registration_access"]:
        registration_filter = RegistrationFilterForm(request.GET or None)
        registrations = Registration.objects.select_related("pass_type").order_by(
            "-submitted_at", "-pk"
        )
        if registration_filter.is_valid():
            registrations = registration_filter.filter_queryset(registrations)
        else:
            registrations = registrations.none()
        context["registration_filter"] = registration_filter
        context["registration_page"] = Paginator(
            registrations,
            REGISTRATION_PAGE_SIZE,
        ).get_page(request.GET.get("page"))
    return render(request, "dashboard/overview.html", context)


@dashboard_login_required
def records_index(request):
    """Show each Admin-registered model the moderator may inspect."""
    _require_dashboard_access(request)
    return render(
        request,
        "dashboard/records_index.html",
        {
            "model_catalog": admin_model_catalog(request),
            "shortcuts": dashboard_shortcuts(request.user, request),
        },
    )


@dashboard_login_required
def records_list(request, app_label, model_name):
    """Render configured Admin columns and filters in the custom dashboard."""
    _require_dashboard_access(request)
    model = next(
        (
            registered_model
            for registered_model in admin.site._registry
            if registered_model._meta.app_label == app_label
            and registered_model._meta.model_name == model_name
        ),
        None,
    )
    if model is None:
        raise Http404("No such managed record type.")

    model_admin = admin.site._registry[model]
    if (
        not model_admin.has_module_permission(request)
        or not model_admin.has_view_or_change_permission(request)
    ):
        raise PermissionDenied

    try:
        changelist = model_admin.get_changelist_instance(request)
        changelist.formset = None
        if "action_checkbox" in changelist.list_display:
            changelist.list_display.remove("action_checkbox")
    except IncorrectLookupParameters:
        return render(
            request,
            "dashboard/records_list.html",
            {
                "invalid_lookup": True,
                "model_name": model._meta.verbose_name_plural,
                "model_catalog": admin_model_catalog(request),
                "shortcuts": dashboard_shortcuts(request.user, request),
            },
            status=400,
        )

    actions = model_admin.get_actions(request)
    actions.pop("delete_selected", None)
    if request.method == "POST" and request.POST.get("action"):
        action_name = request.POST["action"]
        if action_name == "delete_selected":
            raise PermissionDenied
        if action_name == "__dashboard_delete_selected__":
            selected_ids = request.POST.getlist("_selected_action")
            selected = list(
                changelist.get_queryset(request).filter(pk__in=selected_ids)
            )
            if not selected or not model_admin.has_delete_permission(request):
                raise PermissionDenied
            deleted_objects, model_count, perms_needed, protected = (
                model_admin.get_deleted_objects(selected, request)
            )
            return render(
                request,
                "dashboard/records_bulk_delete.html",
                {
                    "app_label": model._meta.app_label,
                    "model_name": model._meta.model_name,
                    "model_label": model._meta.verbose_name_plural,
                    "selected": selected,
                    "selected_ids": [str(obj.pk) for obj in selected],
                    "deleted_objects": deleted_objects,
                    "model_count": model_count,
                    "perms_needed": perms_needed,
                    "protected": protected,
                    "shortcuts": dashboard_shortcuts(request.user, request),
                },
            )
        if action_name not in actions:
            raise PermissionDenied
        response = model_admin.response_action(
            request,
            queryset=changelist.get_queryset(request),
        )
        if response is not None:
            location = response.get("Location")
            if location and location.startswith("/ysse2014/"):
                response["Location"] = reverse(
                    "dashboard:records_list",
                    args=(model._meta.app_label, model._meta.model_name),
                )
            return response

    editable_fields = model_admin.list_editable
    list_editable_formset = None
    can_edit_list = bool(
        editable_fields and model_admin.has_change_permission(request)
    )
    if can_edit_list:
        formset_class = model_admin.get_changelist_formset(request)
        if request.method == "POST" and "_save" in request.POST:
            editable_queryset = model_admin._get_list_editable_queryset(
                request,
                formset_class.get_default_prefix(),
            )
            list_editable_formset = formset_class(
                request.POST,
                request.FILES,
                queryset=editable_queryset,
            )
            if list_editable_formset.is_valid():
                changed_count = 0
                using = router.db_for_write(model)
                with transaction.atomic(using=using):
                    for editable_form in list_editable_formset.forms:
                        if editable_form.has_changed():
                            editable_obj = model_admin.save_form(
                                request,
                                editable_form,
                                change=True,
                            )
                            model_admin.save_model(
                                request,
                                editable_obj,
                                editable_form,
                                change=True,
                            )
                            model_admin.save_related(
                                request,
                                editable_form,
                                [],
                                change=True,
                            )
                            model_admin.log_change(
                                request,
                                editable_obj,
                                model_admin.construct_change_message(
                                    request,
                                    editable_form,
                                    [],
                                ),
                            )
                            changed_count += 1
                if changed_count:
                    messages.success(
                        request,
                        f"{changed_count} record(s) updated successfully.",
                    )
                return redirect(request.get_full_path())
        else:
            list_editable_formset = formset_class(
                queryset=changelist.result_list,
            )

    action_choices = [
        choice
        for choice in model_admin.get_action_choices(request)
        if choice[0] not in {"delete_selected"}
    ]
    if model_admin.has_delete_permission(request):
        action_choices.append(
            ("__dashboard_delete_selected__", "Delete selected records")
        )
    has_actions = bool(actions) or model_admin.has_delete_permission(request)

    previous_url = None
    next_url = None
    if changelist.multi_page:
        if changelist.page_num > 0:
            previous_url = changelist.get_query_string(
                {changelist.page_var: changelist.page_num - 1}
            )
        if changelist.page_num < changelist.paginator.num_pages - 1:
            next_url = changelist.get_query_string(
                {changelist.page_var: changelist.page_num + 1}
            )

    return render(
        request,
        "dashboard/records_list.html",
        {
            "changelist": changelist,
            "model_name": model._meta.verbose_name_plural,
            "opts": model._meta,
            "model_catalog": admin_model_catalog(request),
            "record_rows": dashboard_record_rows(
                request,
                changelist,
                model_admin,
                editable_forms=(
                    list_editable_formset.forms
                    if list_editable_formset is not None
                    else None
                ),
            ),
            "record_headers": result_headers(changelist),
            "can_add": model_admin.has_add_permission(request),
            "action_choices": action_choices,
            "has_actions": has_actions,
            "can_edit_list": can_edit_list,
            "list_editable_formset": list_editable_formset,
            "list_editable_errors": (
                list_editable_formset.errors
                if list_editable_formset is not None
                else None
            ),
            "previous_url": previous_url,
            "next_url": next_url,
            "shortcuts": dashboard_shortcuts(request.user, request),
        },
    )


@dashboard_login_required
def records_detail(request, app_label, model_name, object_id):
    """Show the Admin-configured read-only details in dashboard styling."""
    _require_dashboard_access(request)
    model = next(
        (
            registered_model
            for registered_model in admin.site._registry
            if registered_model._meta.app_label == app_label
            and registered_model._meta.model_name == model_name
        ),
        None,
    )
    if model is None:
        raise Http404("No such managed record type.")

    model_admin = admin.site._registry[model]
    if not model_admin.has_module_permission(request):
        raise PermissionDenied

    obj = model_admin.get_queryset(request).filter(pk=object_id).first()
    if obj is None:
        raise Http404("No such managed record.")
    if not model_admin.has_view_or_change_permission(request, obj):
        raise PermissionDenied

    return render(
        request,
        "dashboard/records_detail.html",
        {
            "model_name": model._meta.verbose_name_plural,
            "object_title": str(obj),
            "object_fields": dashboard_record_fields(request, model_admin, obj),
            "can_change": dashboard_can_edit_record(request, model_admin, obj),
            "can_delete": model_admin.has_delete_permission(request, obj),
            "edit_url": reverse(
                "dashboard:records_edit",
                args=(model._meta.app_label, model._meta.model_name, obj.pk),
            ),
            "delete_url": reverse(
                "dashboard:records_delete",
                args=(model._meta.app_label, model._meta.model_name, obj.pk),
            ),
            "list_url": reverse(
                "dashboard:records_list",
                args=(model._meta.app_label, model._meta.model_name),
            ),
            "shortcuts": dashboard_shortcuts(request.user, request),
        },
    )


def _get_registered_model_admin(request, app_label, model_name):
    model = next(
        (
            registered_model
            for registered_model in admin.site._registry
            if registered_model._meta.app_label == app_label
            and registered_model._meta.model_name == model_name
        ),
        None,
    )
    if model is None:
        raise Http404("No such managed record type.")
    model_admin = admin.site._registry[model]
    if not model_admin.has_module_permission(request):
        raise PermissionDenied
    return model, model_admin


def _render_record_form(request, model, model_admin, obj, *, change):
    if change:
        allowed = model_admin.has_change_permission(request, obj)
    else:
        allowed = model_admin.has_add_permission(request)
    if not allowed:
        raise PermissionDenied

    form_class = model_admin.get_form(request, obj, change=change)
    form = form_class(
        request.POST or None,
        request.FILES or None,
        instance=obj,
    )
    inline_entries = dashboard_inline_formsets(
        request,
        model_admin,
        obj,
        data=request.POST if request.method == "POST" else None,
        files=request.FILES if request.method == "POST" else None,
        parent_instance=form.instance,
    )
    if change and not form_class.base_fields and not any(
        entry["formset"].form.base_fields for entry in inline_entries
    ):
        raise PermissionDenied

    if request.method == "POST":
        form_is_valid = form.is_valid()
        inlines_are_valid = all(
            entry["formset"].is_valid() for entry in inline_entries
        )
        if form_is_valid and inlines_are_valid:
            database = router.db_for_write(model)
            with transaction.atomic(using=database):
                saved_obj = model_admin.save_form(request, form, change=change)
                model_admin.save_model(request, saved_obj, form, change=change)
                formsets = [entry["formset"] for entry in inline_entries]
                model_admin.save_related(request, form, formsets, change=change)
                change_message = model_admin.construct_change_message(
                    request,
                    form,
                    formsets,
                    add=not change,
                )
                if change:
                    model_admin.log_change(request, saved_obj, change_message)
                else:
                    model_admin.log_addition(request, saved_obj, change_message)
            messages.success(
                request,
                f"{model._meta.verbose_name.capitalize()} "
                f"{'updated' if change else 'created'} successfully.",
            )
            return redirect(
                "dashboard:records_detail",
                app_label=model._meta.app_label,
                model_name=model._meta.model_name,
                object_id=saved_obj.pk,
            )

    title = (
        f"Edit {model._meta.verbose_name}"
        if change
        else f"Add {model._meta.verbose_name}"
    )
    form_has_errors = bool(form.errors) or any(
        entry["formset"].errors or entry["formset"].non_form_errors()
        for entry in inline_entries
    )
    return render(
        request,
        "dashboard/records_form.html",
        {
            "model_name": model._meta.verbose_name_plural,
            "title": title,
            "form": form,
            "form_sections": dashboard_form_sections(
                request,
                model_admin,
                obj,
                form,
            ),
            "inline_entries": inline_entries,
            "cancel_url": (
                reverse(
                    "dashboard:records_detail",
                    args=(model._meta.app_label, model._meta.model_name, obj.pk),
                )
                if obj is not None
                else reverse(
                    "dashboard:records_list",
                    args=(model._meta.app_label, model._meta.model_name),
                )
            ),
            "shortcuts": dashboard_shortcuts(request.user, request),
        },
        status=(
            400
            if request.method == "POST" and form_has_errors
            else 200
        ),
    )


@dashboard_login_required
def records_create(request, app_label, model_name):
    """Create records through the registered ModelAdmin form and save hooks."""
    _require_dashboard_access(request)
    model, model_admin = _get_registered_model_admin(
        request,
        app_label,
        model_name,
    )
    return _render_record_form(request, model, model_admin, None, change=False)


@dashboard_login_required
def records_edit(request, app_label, model_name, object_id):
    """Update a record using its configured Admin form and audit hooks."""
    _require_dashboard_access(request)
    model, model_admin = _get_registered_model_admin(
        request,
        app_label,
        model_name,
    )
    obj = model_admin.get_queryset(request).filter(pk=object_id).first()
    if obj is None:
        raise Http404("No such managed record.")
    return _render_record_form(request, model, model_admin, obj, change=True)


@dashboard_login_required
def records_delete(request, app_label, model_name, object_id):
    """Confirm and delete one object using its registered ModelAdmin hooks."""
    _require_dashboard_access(request)
    model, model_admin = _get_registered_model_admin(
        request,
        app_label,
        model_name,
    )
    obj = model_admin.get_queryset(request).filter(pk=object_id).first()
    if obj is None:
        raise Http404("No such managed record.")
    if not model_admin.has_delete_permission(request, obj):
        raise PermissionDenied

    deleted_objects, model_count, perms_needed, protected = (
        model_admin.get_deleted_objects([obj], request)
    )
    if request.method == "POST" and request.POST.get("confirm") == "yes":
        if perms_needed or protected:
            messages.error(
                request,
                "This record cannot be deleted because related records are protected.",
            )
        else:
            using = router.db_for_write(model)
            with transaction.atomic(using=using):
                model_admin.log_deletion(request, obj, str(obj))
                model_admin.delete_model(request, obj)
            messages.success(request, "Record deleted successfully.")
            return redirect(
                "dashboard:records_list",
                app_label=model._meta.app_label,
                model_name=model._meta.model_name,
            )

    return render(
        request,
        "dashboard/records_delete.html",
        {
            "model_name": model._meta.verbose_name,
            "object_title": str(obj),
            "deleted_objects": deleted_objects,
            "model_count": model_count,
            "perms_needed": perms_needed,
            "protected": protected,
            "cancel_url": reverse(
                "dashboard:records_detail",
                args=(model._meta.app_label, model._meta.model_name, obj.pk),
            ),
            "shortcuts": dashboard_shortcuts(request.user, request),
        },
    )


@dashboard_login_required
def records_bulk_delete(request, app_label, model_name):
    """Confirm and execute a permission-scoped bulk deletion."""
    _require_dashboard_access(request)
    if request.method != "POST":
        raise PermissionDenied

    model, model_admin = _get_registered_model_admin(
        request,
        app_label,
        model_name,
    )
    if not model_admin.has_delete_permission(request):
        raise PermissionDenied

    selected_ids = request.POST.getlist("selected_ids")
    if not selected_ids:
        raise Http404("No records were selected.")
    queryset = model_admin.get_queryset(request).filter(pk__in=selected_ids)
    selected = list(queryset)
    if len(selected) != len(set(selected_ids)):
        raise PermissionDenied

    deleted_objects, model_count, perms_needed, protected = (
        model_admin.get_deleted_objects(selected, request)
    )
    if request.POST.get("confirm") == "yes":
        if perms_needed or protected:
            messages.error(
                request,
                "Some selected records cannot be deleted because related records are protected.",
            )
        else:
            using = router.db_for_write(model)
            with transaction.atomic(using=using):
                model_admin.log_deletions(request, queryset)
                model_admin.delete_queryset(request, queryset)
            messages.success(
                request,
                f"{len(selected)} record(s) deleted successfully.",
            )
            return redirect(
                "dashboard:records_list",
                app_label=model._meta.app_label,
                model_name=model._meta.model_name,
            )

    return render(
        request,
        "dashboard/records_bulk_delete.html",
        {
            "app_label": model._meta.app_label,
            "model_name": model._meta.model_name,
            "model_label": model._meta.verbose_name_plural,
            "selected": selected,
            "selected_ids": [str(obj.pk) for obj in selected],
            "deleted_objects": deleted_objects,
            "model_count": model_count,
            "perms_needed": perms_needed,
            "protected": protected,
            "shortcuts": dashboard_shortcuts(request.user, request),
        },
    )


@dashboard_login_required
def reports(request):
    """Show permission-scoped, aggregate-only reports with CSV export."""
    _require_dashboard_access(request)
    report_filter = ReportFilterForm(request.GET or None)
    valid = report_filter.is_valid()
    rows = []
    if valid:
        rows = report_rows(
            request.user,
            date_from=report_filter.cleaned_data.get("date_from"),
            date_to=report_filter.cleaned_data.get("date_to"),
        )
    if valid and request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            'attachment; filename="ysse-management-summary.csv"'
        )
        response["X-Content-Type-Options"] = "nosniff"
        writer = csv.writer(response)
        writer.writerow(("section", "status", "count", "total amount"))
        for row in rows:
            writer.writerow(
                (
                    row["section"],
                    row["status"],
                    row["count"],
                    row["amount"],
                )
            )
        log_report_export(actor_id=request.user.pk, row_count=len(rows))
        return response

    return render(
        request,
        "dashboard/reports.html",
        {
            "report_filter": report_filter,
            "report_rows": rows,
            "has_report_access": bool(rows),
            "invalid_filter": not valid,
            "shortcuts": dashboard_shortcuts(request.user, request),
        },
        status=400 if not valid else 200,
    )
