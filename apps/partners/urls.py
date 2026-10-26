"""URL routes for sponsor information and inquiries."""

from django.urls import path

from . import views


app_name = "partners"

urlpatterns = [
    path("", views.partners_index, name="index"),
    path("sponsorship-deck/", views.sponsorship_deck, name="deck"),
]
