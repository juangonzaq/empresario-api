"""Scrape the SUNAT electronic mailbox into the database."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from sunat_mailbox.models import Message, MessageType
from sunat_mailbox.services import (
    MailboxSynchronizer,
    SunatLoginError,
    SunatMailboxClient,
)


class Command(BaseCommand):
    help = "Scrape the SUNAT electronic mailbox (buzón SOL) into the database."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--taxpayer-id", default=settings.SUNAT_RUC, help="RUC")
        parser.add_argument("--username", default=settings.SUNAT_USER)
        parser.add_argument("--password", default=settings.SUNAT_PASS)
        parser.add_argument(
            "--type", type=int, action="append", dest="types",
            choices=list(MessageType.values),
            help="1=messages, 2=notifications. Repeatable. Defaults to both.",
        )
        parser.add_argument(
            "--max-pages", type=int, default=None,
            help="Stop after this many pages per type (useful for smoke tests).",
        )
        parser.add_argument(
            "--details", action="store_true",
            help="Also fetch each message body and its attachment metadata.",
        )
        parser.add_argument(
            "--attachments", action="store_true",
            help="Download each PDF attachment and store its extracted text. "
                 "Implies --details.",
        )
        parser.add_argument(
            "--redownload", action="store_true",
            help="Re-download attachments that were already fetched.",
        )
        parser.add_argument(
            "--headful", action="store_true",
            help="Show the browser window while logging in (for debugging).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        credentials = (
            options["taxpayer_id"], options["username"], options["password"]
        )
        if not all(credentials):
            raise CommandError(
                "Missing credentials. Set SUNAT_RUC, SUNAT_USER and SUNAT_PASS in .env "
                "or pass --taxpayer-id/--username/--password."
            )

        client = SunatMailboxClient(
            taxpayer_id=options["taxpayer_id"],
            username=options["username"],
            password=options["password"],
            headless=not options["headful"],
        )

        self.stdout.write(f"Authenticating {options['taxpayer_id']}/{options['username']} ...")
        try:
            client.login()
        except SunatLoginError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS("Login OK"))

        synchronizer = MailboxSynchronizer(
            client,
            fetch_details=options["details"],
            download_attachments=options["attachments"],
            redownload=options["redownload"],
        )
        result = synchronizer.run(
            message_types=options["types"], max_pages=options["max_pages"]
        )

        stored = Message.objects.for_taxpayer(options["taxpayer_id"]).count()
        style = self.style.WARNING if result.failed else self.style.SUCCESS
        self.stdout.write(style(f"Done: {result} ({stored} stored in total)"))
