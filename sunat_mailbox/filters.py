"""Query filters for the mailbox API."""

from __future__ import annotations

from django.db.models import Q
from django_filters import rest_framework as filters

from .models import ExtractionStatus, Message


class MessageFilter(filters.FilterSet):
    """Supports ranges on the mailbox dates plus the usual exact-match fields."""

    sent_from = filters.DateFilter(field_name="sent_on", lookup_expr="gte")
    sent_to = filters.DateFilter(field_name="sent_on", lookup_expr="lte")
    published_from = filters.DateTimeFilter(field_name="published_at", lookup_expr="gte")
    published_to = filters.DateTimeFilter(field_name="published_at", lookup_expr="lte")
    has_attachments = filters.BooleanFilter(method="filter_has_attachments")
    has_text = filters.BooleanFilter(
        method="filter_has_text",
        label="Only messages whose attachments have extracted text",
    )

    class Meta:
        model = Message
        fields = (
            "taxpayer_id", "message_type", "is_read", "is_urgent", "is_starred",
            "office_code", "label_code",
        )

    def filter_has_attachments(self, queryset, name, value):
        if value is None:
            return queryset
        if value:
            return queryset.filter(attachment_count__gt=0)
        return queryset.filter(attachment_count=0)

    def filter_has_text(self, queryset, name, value):
        if value is None:
            return queryset
        extracted = Q(attachments__extraction_status=ExtractionStatus.EXTRACTED)
        if value:
            return queryset.filter(extracted).distinct()
        return queryset.exclude(extracted)
