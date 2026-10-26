"""Public competition and authenticated submission URL routes."""

from django.urls import path

from . import views


app_name = "competitions"

urlpatterns = [
    path("", views.competition_list, name="list"),
    path("my-submissions/", views.my_submissions, name="my-submissions"),
    path("submissions/<uuid:reference>/", views.submission_detail, name="submission-detail"),
    path(
        "submissions/<uuid:reference>/file/",
        views.submission_file,
        name="submission-file",
    ),
    path("<slug:slug>/", views.competition_detail, name="detail"),
    path("<slug:slug>/rulebook/", views.rulebook_download, name="rulebook"),
    path("<slug:slug>/submit/", views.submit_competition, name="submit"),
]
