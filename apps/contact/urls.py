"""Public contact and information routes."""

from django.urls import path

from . import views


app_name = "contact"

urlpatterns = [
    path("", views.contact_page, name="contact"),
    path("faq/", views.faq_page, name="faq"),
    path("privacy/", views.legal_page, {"kind": "privacy"}, name="privacy"),
    path("terms/", views.legal_page, {"kind": "terms"}, name="terms"),
    path("press/", views.press_list, name="press-list"),
    path("press/<slug:slug>/", views.press_detail, name="press-detail"),
    path(
        "press/<slug:slug>/attachment/",
        views.press_attachment,
        name="press-attachment",
    ),
    path(
        "press/<slug:slug>/cover/",
        views.press_cover,
        name="press-cover",
    ),
    path("newsletter/", views.newsletter_page, name="newsletter-result"),
    path("newsletter/subscribe/", views.newsletter_subscribe, name="newsletter-subscribe"),
    path(
        "newsletter/confirm/<uuid:token>/",
        views.newsletter_confirm,
        name="newsletter-confirm",
    ),
    path(
        "newsletter/unsubscribe/<uuid:token>/",
        views.newsletter_unsubscribe,
        name="newsletter-unsubscribe",
    ),
]
