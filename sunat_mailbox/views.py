"""Read-only API for the scraped SUNAT mailbox."""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from .filters import MessageFilter
from .models import ExtractionStatus, Message, MessageType
from .serializers import MessageDetailSerializer, MessageListSerializer
from .services import insights


class MessageViewSet(viewsets.ReadOnlyModelViewSet):
    """Browse the scraped mailbox.

    Messages are written by the ``scrape_mailbox`` command, so the API is read-only.

    * ``GET /api/messages/`` — paginated list
    * ``GET /api/messages/{id}/`` — full message with attachments and raw payloads
    * ``GET /api/messages/summary/`` — counts by type and read state
    * ``GET /api/messages/card/`` — executive card for the home dashboard
    """

    queryset = Message.objects.all()
    filter_backends = (
        DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter
    )
    filterset_class = MessageFilter
    # Searching attachment text is what makes the scraped PDFs useful; DRF adds the
    # DISTINCT needed for the join on its own.
    search_fields = (
        "subject", "sender_name", "message_code", "attachments__text_content",
    )
    ordering_fields = ("published_at", "sent_on", "message_code", "subject")
    ordering = ("-published_at", "-message_code")

    def get_serializer_class(self):
        if self.action == "retrieve":
            return MessageDetailSerializer
        return MessageListSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == "retrieve":
            return queryset.prefetch_related("attachments")
        # The list serializer never touches attachments or the raw payloads, so keep
        # the (potentially large) JSON columns out of the query.
        return queryset.defer("list_payload", "detail_payload")

    @action(detail=False, methods=["get"])
    def summary(self, request: Request) -> Response:
        """Aggregate counts over the currently filtered queryset."""
        queryset = self.filter_queryset(self.get_queryset())
        # Every count needs distinct=True: with_extracted_text joins the attachments
        # table, which would otherwise multiply a message by its attachment count.
        totals = queryset.aggregate(
            total=Count("id", distinct=True),
            unread=Count(
                "id",
                filter=Q(is_read=False, reviewed_at__isnull=True),
                distinct=True,
            ),
            with_attachments=Count(
                "id", filter=Q(attachment_count__gt=0), distinct=True
            ),
            with_extracted_text=Count(
                "id",
                filter=Q(attachments__extraction_status=ExtractionStatus.EXTRACTED),
                distinct=True,
            ),
        )
        # order_by() is required: the view's default ordering would otherwise leak
        # into the GROUP BY and split the aggregate into one row per message.
        by_type = {
            MessageType(row["message_type"]).label: row["count"]
            for row in queryset.order_by()
            .values("message_type")
            .annotate(count=Count("id"))
        }
        return Response({**totals, "by_type": by_type})

    @action(detail=False, methods=["get"])
    def card(self, request: Request) -> Response:
        """Executive summary of the mailbox for the home dashboard.

        Classification runs over subjects only, so the deferred queryset (raw
        payloads excluded) is enough even though every row is scanned.
        """
        queryset = self.filter_queryset(self.get_queryset())
        messages = list(queryset)
        last_synced = max((m.updated_at for m in messages), default=None)
        card = insights.build_card(messages, last_synced)
        now = timezone.now()
        card["counts"]["expiring_soon"] = queryset.filter(
            is_read=False,
            reviewed_at__isnull=True,
            expires_at__gte=now,
            expires_at__lte=now + timedelta(days=7),
        ).count()
        card["counts"]["attachment_issues"] = (
            queryset.filter(
                attachments__extraction_status__in=(
                    ExtractionStatus.UNSUPPORTED, ExtractionStatus.FAILED
                )
            )
            .distinct()
            .count()
        )
        return Response(card)

    @action(detail=True, methods=["post"])
    def review(self, request: Request, pk=None) -> Response:
        """Mark a message as reviewed in this app.

        Idempotent; the first review timestamp is kept. This does NOT mark the
        message as read in SUNAT SOL — ``read_at``/``is_read`` stay untouched.
        """
        message = self.get_object()
        if message.reviewed_at is None:
            message.reviewed_at = timezone.now()
            message.save(update_fields=["reviewed_at", "updated_at"])
        return Response(
            MessageDetailSerializer(message, context=self.get_serializer_context()).data
        )
