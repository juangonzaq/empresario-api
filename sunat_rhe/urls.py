from django.urls import path

from .views import (
    FeeReceiptPdfView, FeeReceiptRefreshView, FeeReceiptUploadView,
    FeeReceiptView, FeeReceiptsView,
)

app_name = "sunat_rhe"

urlpatterns = [
    path("rhe/receipts/", FeeReceiptsView.as_view(), name="receipts"),
    path(
        "rhe/receipts/upload/",
        FeeReceiptUploadView.as_view(),
        name="receipt-upload",
    ),
    path("rhe/receipts/<uuid:pk>/", FeeReceiptView.as_view(), name="receipt"),
    path(
        "rhe/receipts/<uuid:pk>/pdf/",
        FeeReceiptPdfView.as_view(),
        name="receipt-pdf",
    ),
    path(
        "rhe/receipts/<uuid:pk>/refresh/",
        FeeReceiptRefreshView.as_view(),
        name="receipt-refresh",
    ),
]
