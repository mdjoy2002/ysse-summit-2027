"""Summit page URL routes."""

from django.urls import path

from . import views


app_name = "summit"

urlpatterns = [
    path("", views.summit_details, name="index"),
]
