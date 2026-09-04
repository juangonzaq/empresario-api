from django.urls import path

from .views import ExportDownloadView, ExportRequestView

app_name = "documents"

urlpatterns = [
    path("documents/exports/", ExportRequestView.as_view(), name="export-request"),
    path(
        "documents/exports/<uuid:pk>/download/",
        ExportDownloadView.as_view(),
        name="export-download",
    ),
]
