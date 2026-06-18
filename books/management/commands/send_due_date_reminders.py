from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.core.management.base import BaseCommand

from books.models import Invoice


class Command(BaseCommand):
    help = "Send invoice due-date reminder emails (overdue + approaching) per user."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=7,
            help="Days ahead to include as 'approaching due' (default: 7).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be sent without actually sending emails.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        days = options["days"]
        today = date.today()
        cutoff = today + timedelta(days=days)

        User = get_user_model()
        users_with_email = User.objects.filter(is_active=True, email__gt="").exclude(email="")
        emails_sent = 0

        for user in users_with_email:
            unpaid = Invoice.objects.filter(
                owner=user,
                due_date__isnull=False,
            ).exclude(status=Invoice.STATUS_PAID)

            overdue = list(unpaid.filter(due_date__lt=today).order_by("due_date"))
            approaching = list(unpaid.filter(due_date__gte=today, due_date__lte=cutoff).order_by("due_date"))

            if not overdue and not approaching:
                continue

            lines = []
            if overdue:
                lines.append("OVERDUE:")
                for inv in overdue:
                    days_late = (today - inv.due_date).days
                    lines.append(
                        f"  • {inv.invoice_number} – {inv.customer_name}"
                        f" – due {inv.due_date} ({days_late} day(s) overdue)"
                        f" – {inv.currency} {inv.grand_total}"
                    )
                lines.append("")

            if approaching:
                lines.append(f"DUE IN THE NEXT {days} DAYS:")
                for inv in approaching:
                    days_until = (inv.due_date - today).days
                    label = "today" if days_until == 0 else f"in {days_until} day(s)"
                    lines.append(
                        f"  • {inv.invoice_number} – {inv.customer_name}"
                        f" – due {inv.due_date} ({label})"
                        f" – {inv.currency} {inv.grand_total}"
                    )

            body = (
                f"Hi {user.username},\n\n"
                "Here is your invoice payment reminder:\n\n"
                + "\n".join(lines)
                + "\n\nLog in to RumiPress to manage your invoices."
            )

            if overdue:
                subject = f"RumiPress: {len(overdue)} overdue invoice(s) need attention"
            else:
                subject = f"RumiPress: {len(approaching)} invoice(s) due in the next {days} days"

            if dry_run:
                self.stdout.write(f"[dry-run] Would email {user.email}:")
                self.stdout.write(body)
                self.stdout.write("")
            else:
                send_mail(
                    subject=subject,
                    message=body,
                    from_email=None,
                    recipient_list=[user.email],
                    fail_silently=False,
                )
                emails_sent += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Sent reminder to {user.email}"
                        f" — {len(overdue)} overdue, {len(approaching)} approaching."
                    )
                )

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(f"Done. {emails_sent} email(s) sent."))
