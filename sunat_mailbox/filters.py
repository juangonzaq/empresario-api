"""Query filters for the mailbox API."""

from __future__ import annotations

from django.db.models import Q
from django_filters import rest_framework as filters

from .models import ExtractionStatus, Message
from .services import insights

# Same catalog the insights service uses, so SQL filtering and per-row
# classification can never disagree on what counts as urgent.
URGENT_SUBJECT_PATTERN = "|".join(
    rule["pattern"]
    for rule in insights.CATEGORY_RULES
    if rule["priority"] == insights.PRIORITY_URGENT
)


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
    priority = filters.ChoiceFilter(
        choices=[("urgent", "urgent")],
        method="filter_priority",
        label="Only messages the insights catalog classifies as urgent",
    )
    reviewed = filters.BooleanFilter(
        method="filter_reviewed",
        label="Read in SUNAT SOL or opened in this app",
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

    def filter_reviewed(self, queryset, name, value):
        if value is None:
            return queryset
        reviewed = Q(is_read=True) | Q(reviewed_at__isnull=False)
        if value:
            return queryset.filter(reviewed)
        return queryset.exclude(reviewed)

    def filter_priority(self, queryset, name, value):
        if value != "urgent":
            return queryset
        return queryset.filter(
            Q(is_urgent=True) | Q(subject__iregex=URGENT_SUBJECT_PATTERN)
        )

    def filter_has_text(self, queryset, name, value):
        if value is None:
            return queryset
        extracted = Q(attachments__extraction_status=ExtractionStatus.EXTRACTED)
        if value:
            return queryset.filter(extracted).distinct()
        return queryset.exclude(extracted)
