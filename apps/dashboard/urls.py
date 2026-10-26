"""Named URLs for the protected custom management dashboard."""

from django.urls import path

from . import views


app_name = "dashboard"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("records/", views.records_index, name="records_index"),
    path(
        "records/<str:app_label>/<str:model_name>/",
        views.records_list,
        name="records_list",
    ),
    path(
        "records/<str:app_label>/<str:model_name>/add/",
        views.records_create,
        name="records_create",
    ),
    path(
        "records/<str:app_label>/<str:model_name>/bulk-delete/",
        views.records_bulk_delete,
        name="records_bulk_delete",
    ),
    path(
        "records/<str:app_label>/<str:model_name>/<str:object_id>/",
        views.records_detail,
        name="records_detail",
    ),
    path(
        "records/<str:app_label>/<str:model_name>/<str:object_id>/edit/",
        views.records_edit,
        name="records_edit",
    ),
    path(
        "records/<str:app_label>/<str:model_name>/<str:object_id>/delete/",
        views.records_delete,
        name="records_delete",
    ),
    path("reports/", views.reports, name="reports"),
]
