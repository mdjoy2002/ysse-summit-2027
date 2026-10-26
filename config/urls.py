"""Project URL configuration."""

from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

from apps.dashboard.views import moderator_login


urlpatterns = [
    path("ysse2014/", admin.site.urls),
    path("admin-2/", moderator_login, name="admin2-login"),
    path("accounts/", include("django.contrib.auth.urls")),
    path(
        "competitions/",
        include("apps.competitions.urls"),
    ),
    path("about/", include("apps.about.urls")),
    path("summit/", include("apps.summit.urls")),
    path("speakers/", include("apps.speakers.urls")),
    path("schedule/", include("apps.schedule.urls")),
    path("partners/", include("apps.partners.urls")),
    path("registrations/", include("apps.registrations.urls")),
    path("payments/", include("apps.payments.urls")),
    path("contact/", include("apps.contact.urls")),
    path("dashboard/", include("apps.dashboard.urls")),
    path("", include("apps.home.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
