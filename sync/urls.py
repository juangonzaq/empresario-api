from django.urls import path

from .views import SyncStartView, SyncStatusView

app_name = "sync"

urlpatterns = [
    path("sync/status/", SyncStatusView.as_view(), name="status"),
    path("sync/start/", SyncStartView.as_view(), name="start"),
]
