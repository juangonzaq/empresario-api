from rest_framework.routers import DefaultRouter

from .views import MessageViewSet

app_name = "sunat_mailbox"

router = DefaultRouter()
router.register("messages", MessageViewSet, basename="message")

urlpatterns = router.urls
