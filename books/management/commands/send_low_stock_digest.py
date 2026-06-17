from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import F

from books.models import Book


class Command(BaseCommand):
    help = "Send a single low-stock digest email per user, then mark books as alerted."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be sent without sending emails or updating flags.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        User = get_user_model()

        users_with_email = User.objects.filter(is_active=True, email__gt="").exclude(email="")
        emails_sent = 0
        books_flagged = 0

        for user in users_with_email:
            low_books = list(
                Book.objects.filter(
                    owner=user,
                    stock_on_hand__lte=F("reorder_threshold"),
                    low_stock_alert_sent=False,
                ).order_by("stock_on_hand", "title")
            )

            if not low_books:
                continue

            lines = [
                f"  • {b.title}  —  {b.stock_on_hand} in stock (threshold: {b.reorder_threshold})"
                for b in low_books
            ]
            body = (
                f"Hi {user.username},\n\n"
                f"The following {len(low_books)} book(s) are below their reorder threshold:\n\n"
                + "\n".join(lines)
                + "\n\nLog in to RumiPress to create reorders or adjust thresholds."
            )

            if dry_run:
                self.stdout.write(f"[dry-run] Would email {user.email}:")
                self.stdout.write(body)
                self.stdout.write("")
            else:
                send_mail(
                    subject=f"RumiPress: {len(low_books)} book(s) low on stock",
                    message=body,
                    from_email=None,
                    recipient_list=[user.email],
                    fail_silently=False,
                )
                with transaction.atomic():
                    Book.objects.filter(
                        id__in=[b.id for b in low_books]
                    ).update(low_stock_alert_sent=True)

                emails_sent += 1
                books_flagged += len(low_books)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Sent digest to {user.email} — {len(low_books)} book(s) listed."
                    )
                )

        if not dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Done. {emails_sent} email(s) sent, {books_flagged} book(s) flagged."
                )
            )
