"""URL routes for the public speaker directory."""

from django.urls import path

from . import views


app_name = "speakers"

urlpatterns = [
    path("", views.speaker_list, name="list"),
    path("<slug:slug>/", views.speaker_detail, name="detail"),
]
