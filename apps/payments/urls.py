"""Provider callback and participant payment routes."""

from django.urls import path

from . import views


app_name = "payments"

urlpatterns = [
    path("start/<str:registration_reference>/", views.start_checkout, name="start"),
    path("demo/<uuid:reference>/", views.demo_checkout, name="demo-checkout"),
    path("return/<uuid:reference>/", views.payment_return, name="return"),
    path("status/<uuid:reference>/", views.transaction_status, name="status"),
    path("callback/", views.provider_callback, name="callback"),
]
