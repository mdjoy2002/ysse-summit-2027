"""Public registration and protected participant ticket routes."""

from django.urls import path

from . import views


app_name = "registrations"

urlpatterns = [
    path("", views.pass_list, name="passes"),
    path("new/", views.register, name="new"),
    path(
        "new/payment/<uuid:draft_id>/",
        views.select_registration_payment_method,
        name="registration-payment-method",
    ),
    path("my/", views.registration_history, name="history"),
    path("track/", views.track_registration, name="track"),
    path("reference/<str:reference>/", views.registration_detail, name="detail"),
    path(
        "reference/<str:reference>/pay/",
        views.select_payment_method,
        name="payment-method",
    ),
    path(
        "reference/<str:reference>/pay/<str:method>/",
        views.submit_manual_payment,
        name="payment-details",
    ),
    path(
        "reference/<str:reference>/thank-you/",
        views.payment_thank_you,
        name="thank-you",
    ),
    path(
        "reference/<str:reference>/payment-proof/",
        views.payment_proof,
        name="payment-proof",
    ),
    path("ticket/<uuid:ticket_reference>/", views.ticket, name="ticket"),
    path(
        "verify/<uuid:ticket_reference>/",
        views.verify_ticket,
        name="verify-ticket",
    ),
]
