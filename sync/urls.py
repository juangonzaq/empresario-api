from django.urls import path

from .views import (
    SyncSourceRunView, SyncStartView, SyncStatusView, SyncStepRetryView,
)

app_name = "sync"

urlpatterns = [
    path("sync/status/", SyncStatusView.as_view(), name="status"),
    path("sync/start/", SyncStartView.as_view(), name="start"),
    path(
        "sync/steps/<slug:key>/retry/",
        SyncStepRetryView.as_view(),
        name="step-retry",
    ),
    # Relanzar una fuente a demanda: distinto de reintentar un paso fallido.
    path(
        "sync/sources/<slug:key>/run/",
        SyncSourceRunView.as_view(),
        name="source-run",
    ),
]
