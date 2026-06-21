import sys
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from books import ai_chat
from books.models import (
    Account, AccountMembership, Author, Book, Category, Customer, Invoice,
    InvoiceItem, RoyaltyPayment, RoyaltyRate, Sale, Supplier,
)
from books.permissions import sync_user_groups_for_role


EVAL_CASES = [
    {
        "name": "dashboard_overview",
        "turns": ["Give me a quick overview of my catalog."],
        "expect_tools": {"get_dashboard_overview"},
    },
    {
        "name": "low_stock",
        "turns": ["What books are low on stock right now?"],
        "expect_tools": {"get_low_stock_books"},
        "expect_keywords": ["Last Lighthouse"],
    },
    {
        "name": "reorder_suggestions",
        "turns": ["What should I reorder, and why?"],
        "expect_tools": {"get_reorder_suggestions"},
        "expect_keywords": ["Last Lighthouse"],
    },
    {
        "name": "slow_movers",
        "turns": ["What's not selling / has been sitting on the shelf?"],
        "expect_tools": {"get_slow_moving_books"},
        "expect_keywords": ["Forgotten Tides"],
    },
    {
        "name": "supplier_email_multiturn",
        "turns": [
            "What should I reorder?",
            "Now draft an email to Northwind for those books.",
        ],
        "expect_tools": {"get_reorder_suggestions", "draft_supplier_email"},
        "expect_keywords": ["Northwind"],
    },
    {
        "name": "overdue_invoices",
        "turns": ["Which invoices are overdue?"],
        "expect_tools": {"get_overdue_invoices"},
        "expect_keywords": ["Maple Street"],
    },
    {
        "name": "customer_balance",
        "turns": ["How much does Maple Street Books owe me?"],
        "expect_tools": {"get_customer_balance"},
        "expect_keywords": ["75"],
    },
    {
        "name": "royalty_summary",
        "turns": ["What do I owe in royalties right now?"],
        "expect_tools": {"get_royalty_summary"},
        "expect_keywords": ["Jane Doe"],
    },
    {
        "name": "ambiguous_customer_asks_for_clarification",
        "turns": ["What's the balance for that customer?"],
        "expect_no_tools": True,
        "expect_keywords": ["?"],
    },
    {
        "name": "general_app_question_no_tool_needed",
        "turns": ["What does the Stock page show?"],
        "expect_no_tools": True,
        "expect_keywords": ["stock"],
    },
]


class Command(BaseCommand):
    help = (
        "Run a fixed set of conversations against the real Anthropic API to "
        "evaluate the AI chat assistant's tool use and answer quality. This "
        "makes real, billed API calls - it is deliberately NOT part of "
        "`manage.py test` (which never hits the network)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--keep-data", action="store_true",
            help="Don't delete the throwaway eval account afterwards (for debugging a failure).",
        )
        parser.add_argument(
            "--verbose", action="store_true",
            help="Print the full final reply for every case, not just failures.",
        )

    def handle(self, *args, **options):
        if not settings.ANTHROPIC_API_KEY:
            self.stderr.write(self.style.ERROR("ANTHROPIC_API_KEY is not configured."))
            sys.exit(1)

        user, account = self._build_fixtures()
        passed = 0
        failed = 0

        try:
            for case in EVAL_CASES:
                ok, reason, reply = self._run_case(case, user, account)
                if ok:
                    passed += 1
                    self.stdout.write(self.style.SUCCESS(f"PASS  {case['name']}"))
                else:
                    failed += 1
                    self.stdout.write(self.style.ERROR(f"FAIL  {case['name']}: {reason}"))
                if options["verbose"] or not ok:
                    self.stdout.write(f"      reply: {reply!r}")
        finally:
            if options["keep_data"]:
                self.stdout.write(f"Kept eval data: user={user.username!r} account_id={account.id}")
            else:
                self._cleanup(user, account)

        self.stdout.write(f"\n{passed} passed, {failed} failed.")
        sys.exit(1 if failed else 0)

    def _build_fixtures(self):
        User = get_user_model()
        username = "ai_eval_throwaway"
        User.objects.filter(username=username).delete()
        user = User.objects.create_user(username=username, password="eval-only-not-a-real-login")

        account = Account.objects.create(name="AI Eval Co")
        AccountMembership.objects.create(account=account, user=user, role=AccountMembership.ROLE_ADMIN)
        sync_user_groups_for_role(user, AccountMembership.ROLE_ADMIN)

        category = Category.objects.create(owner=user, account=account, name="Fiction")
        author = Author.objects.create(owner=user, account=account, name="Jane Doe")

        low_book = Book.objects.create(
            owner=user, account=account, title="The Last Lighthouse", isbn="111",
            publisher="Acme", published_date=date(2024, 1, 1), category=category,
            distribution_expense=Decimal("8.00"), stock_on_hand=2, reorder_threshold=10,
        )
        low_book.authors.add(author)

        Book.objects.create(
            owner=user, account=account, title="Forgotten Tides", isbn="222",
            publisher="Acme", published_date=date(2023, 1, 1), category=category,
            distribution_expense=Decimal("12.00"), stock_on_hand=40, reorder_threshold=5,
        )

        Sale.objects.create(
            owner=user, account=account, book=low_book, quantity=2,
            unit_price=Decimal("15.00"), sale_date=date.today() - timedelta(days=5),
        )

        RoyaltyRate.objects.create(
            owner=user, account=account, book=low_book, author=author,
            rate=Decimal("10.00"), effective_from=date(2024, 1, 1),
        )
        RoyaltyPayment.objects.create(
            owner=user, account=account, author=author,
            amount=Decimal("1.00"), currency="USD", payment_date=date(2024, 2, 1),
        )

        Supplier.objects.create(
            owner=user, account=account, name="Northwind Distribution",
            contact_name="Pat", email="orders@northwind.test",
        )

        customer = Customer.objects.create(owner=user, account=account, name="Maple Street Books")
        overdue_invoice = Invoice.objects.create(
            owner=user, account=account, customer=customer, customer_name=customer.name,
            invoice_date=date.today() - timedelta(days=60),
            due_date=date.today() - timedelta(days=30),
            currency="USD", status=Invoice.STATUS_SENT,
        )
        InvoiceItem.objects.create(
            invoice=overdue_invoice, description="Order", quantity=1, unit_price=Decimal("75.00"),
        )

        return user, account

    def _cleanup(self, user, account):
        user.delete()
        account.delete()

    def _run_case(self, case, user, account):
        history = []
        reply = ""
        tools_used = set()

        for message in case["turns"]:
            reply, history = ai_chat.get_chat_reply(user, account, message, history)
            for entry in history:
                if entry.get("role") != "assistant":
                    continue
                for block in entry.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tools_used.add(block.get("name"))

        missing_tools = case.get("expect_tools", set()) - tools_used
        if missing_tools:
            return False, f"expected tool(s) not used: {missing_tools} (used: {tools_used})", reply

        if case.get("expect_no_tools") and tools_used:
            return False, f"expected no tool use, but used: {tools_used}", reply

        for keyword in case.get("expect_keywords", []):
            if keyword.lower() not in reply.lower():
                return False, f"expected keyword {keyword!r} missing from reply", reply

        return True, "", reply
