"""URL routes for the event schedule."""

from django.urls import path

from . import views


app_name = "schedule"

urlpatterns = [
    path("", views.schedule_index, name="index"),
]
