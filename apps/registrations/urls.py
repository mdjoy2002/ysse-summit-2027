"""Public registration and protected participant ticket routes."""

from django.urls import path

from . import views


app_name = "registrations"

urlpatterns = [
    path("", views.pass_list, name="passes"),
    path("new/", views.register, name="new"),
    path("my/", views.registration_history, name="history"),
    path("reference/<str:reference>/", views.registration_detail, name="detail"),
    path("ticket/<uuid:ticket_reference>/", views.ticket, name="ticket"),
    path(
        "verify/<uuid:ticket_reference>/",
        views.verify_ticket,
        name="verify-ticket",
    ),
]
