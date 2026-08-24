from django.urls import path

from .views import LeadCreateView

app_name = "leads"

urlpatterns = [
    path("leads/", LeadCreateView.as_view(), name="create"),
]
