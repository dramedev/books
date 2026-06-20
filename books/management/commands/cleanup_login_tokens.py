from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from books.models import CustomerLoginToken


class Command(BaseCommand):
    help = "Delete used or expired customer portal login tokens older than a retention window."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=7,
            help="Keep used/expired tokens for this many days before deleting (default: 7).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be deleted without actually deleting.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        dry_run = options["dry_run"]
        cutoff = timezone.now() - timedelta(days=days)

        stale = CustomerLoginToken.objects.filter(
            Q(used_at__isnull=False, used_at__lt=cutoff)
            | Q(used_at__isnull=True, expires_at__lt=cutoff)
        )
        count = stale.count()

        if dry_run:
            self.stdout.write(f"[dry-run] Would delete {count} stale login token(s).")
            return

        stale.delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {count} stale login token(s)."))
