import base64
import hashlib
import hmac
import json
import re
import requests
import stripe
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from . import ai_chat, iyzico_client
from .fields import decrypt_value, encrypt_value
from .isbn_lookup import IsbnLookupError, lookup_isbn
from .models import (
    AccessCode, AVATAR_MAX_SIZE_BYTES, Account, AccountInvitation, AccountMembership, Author, Book, Category,
    Customer, CustomerLoginToken, get_or_create_account_for_user,
    Integration, Invoice, InvoiceItem, LOGO_MAX_SIZE_BYTES, Location, PrintRun, ProcessedShopifyOrder, Profile, Reorder, Return, RoyaltyPayment, RoyaltyRate,
    Sale, SaleTransaction, StockAdjustment, StockLevel, Subscription, Supplier, WholesalerFeedItem,
    validate_avatar_size, validate_hex_color, validate_logo_size,
)
from .permissions import sync_user_groups_for_role
from .views import _adjust_stock, _invoice_aging_data, _next_receipt_number, _parse_publish_date, _pl_data, _safe_json


def grant(user, *codenames):
    permissions = Permission.objects.filter(
        content_type__app_label="books",
        codename__in=codenames,
    )
    user.user_permissions.add(*permissions)


class AuthorModelTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="owner", password="pass1234")
        self.other_user = User.objects.create_user(username="other", password="pass1234")

    def test_str_returns_name(self):
        author = Author.objects.create(owner=self.user, name="Jane Doe")
        self.assertEqual(str(author), "Jane Doe")

    def test_name_is_unique_per_owner(self):
        Author.objects.create(owner=self.user, name="Jane Doe")

        with self.assertRaises(Exception):
            Author.objects.create(owner=self.user, name="Jane Doe")

    def test_name_can_repeat_across_owners(self):
        Author.objects.create(owner=self.user, name="Jane Doe")
        Author.objects.create(owner=self.other_user, name="Jane Doe")

        self.assertEqual(Author.objects.filter(name="Jane Doe").count(), 2)

    def test_ordering_by_name(self):
        Author.objects.create(owner=self.user, name="Zed")
        Author.objects.create(owner=self.user, name="Amy")

        names = list(Author.objects.filter(owner=self.user).values_list("name", flat=True))
        self.assertEqual(names, ["Amy", "Zed"])


class BookModelTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="owner", password="pass1234")
        self.category = Category.objects.create(owner=self.user, name="Fiction")

    def _make_book(self, stock_on_hand, reorder_threshold):
        return Book.objects.create(
            owner=self.user,
            title="Test Book",
            publisher="Acme",
            published_date=date(2024, 1, 1),
            category=self.category,
            distribution_expense=Decimal("10.00"),
            stock_on_hand=stock_on_hand,
            reorder_threshold=reorder_threshold,
        )

    def test_is_low_stock_when_below_threshold(self):
        book = self._make_book(stock_on_hand=1, reorder_threshold=5)
        self.assertTrue(book.is_low_stock)

    def test_is_low_stock_when_equal_to_threshold(self):
        book = self._make_book(stock_on_hand=5, reorder_threshold=5)
        self.assertTrue(book.is_low_stock)

    def test_not_low_stock_when_above_threshold(self):
        book = self._make_book(stock_on_hand=6, reorder_threshold=5)
        self.assertFalse(book.is_low_stock)


class SaleModelTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="owner", password="pass1234")
        category = Category.objects.create(owner=self.user, name="Fiction")
        self.book = Book.objects.create(
            owner=self.user,
            title="Test Book",
            publisher="Acme",
            published_date=date(2024, 1, 1),
            category=category,
            distribution_expense=Decimal("10.00"),
        )

    def test_revenue_is_quantity_times_unit_price(self):
        sale = Sale.objects.create(
            owner=self.user,
            book=self.book,
            quantity=3,
            unit_price=Decimal("12.50"),
            sale_date=date(2024, 1, 10),
        )
        self.assertEqual(sale.revenue, Decimal("37.50"))

    def test_ordering_is_most_recent_first(self):
        older = Sale.objects.create(
            owner=self.user,
            book=self.book,
            quantity=1,
            unit_price=Decimal("10.00"),
            sale_date=date(2024, 1, 1),
        )
        newer = Sale.objects.create(
            owner=self.user,
            book=self.book,
            quantity=1,
            unit_price=Decimal("10.00"),
            sale_date=date(2024, 2, 1),
        )

        sales = list(Sale.objects.all())
        self.assertEqual(sales, [newer, older])


class BookDetailViewTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="viewer", password="pass1234")
        grant(self.user, "view_book")
        self.client.force_login(self.user)

        self.category = Category.objects.create(owner=self.user, name="Fiction")
        self.book = Book.objects.create(
            owner=self.user,
            title="Test Book",
            publisher="Acme",
            published_date=date(2024, 1, 1),
            category=self.category,
            distribution_expense=Decimal("10.00"),
            stock_on_hand=20,
            reorder_threshold=5,
        )

    def test_detail_page_returns_200_with_totals(self):
        Sale.objects.create(
            owner=self.user,
            book=self.book,
            quantity=2,
            unit_price=Decimal("10.00"),
            sale_date=date(2024, 1, 5),
        )
        Sale.objects.create(
            owner=self.user,
            book=self.book,
            quantity=3,
            unit_price=Decimal("5.00"),
            sale_date=date(2024, 1, 6),
        )

        response = self.client.get(reverse("book_detail", args=[self.book.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_quantity_sold"], 5)
        self.assertEqual(response.context["total_revenue"], Decimal("35.00"))
        self.assertEqual(len(response.context["history"]), 2)

    def test_detail_page_with_no_sales(self):
        response = self.client.get(reverse("book_detail", args=[self.book.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_quantity_sold"], 0)
        self.assertEqual(response.context["total_revenue"], 0)

    def test_unknown_book_returns_404(self):
        response = self.client.get(reverse("book_detail", args=[99999]))
        self.assertEqual(response.status_code, 404)


class StockListViewTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="viewer", password="pass1234")
        grant(self.user, "view_book")
        self.client.force_login(self.user)

        category = Category.objects.create(owner=self.user, name="Fiction")
        self.low_book = Book.objects.create(
            owner=self.user,
            title="Low Stock Book",
            publisher="Acme",
            published_date=date(2024, 1, 1),
            category=category,
            distribution_expense=Decimal("10.00"),
            stock_on_hand=2,
            reorder_threshold=5,
        )
        self.ok_book = Book.objects.create(
            owner=self.user,
            title="Well Stocked Book",
            publisher="Acme",
            published_date=date(2024, 1, 1),
            category=category,
            distribution_expense=Decimal("10.00"),
            stock_on_hand=50,
            reorder_threshold=5,
        )

    def test_default_listing_includes_all_books(self):
        response = self.client.get(reverse("stock_list"))
        self.assertEqual(response.status_code, 200)

        books = list(response.context["books"])
        self.assertIn(self.low_book, books)
        self.assertIn(self.ok_book, books)
        self.assertFalse(response.context["low_only"])

    def test_low_filter_only_includes_low_stock_books(self):
        response = self.client.get(reverse("stock_list"), {"low": "1"})
        self.assertEqual(response.status_code, 200)

        books = list(response.context["books"])
        self.assertIn(self.low_book, books)
        self.assertNotIn(self.ok_book, books)
        self.assertTrue(response.context["low_only"])


class SaleStockAdjustmentTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="staff", password="pass1234")
        grant(
            self.user,
            "view_sale",
            "add_sale",
            "change_sale",
            "delete_sale",
            "view_book",
        )
        self.client.force_login(self.user)

        category = Category.objects.create(owner=self.user, name="Fiction")
        self.book = Book.objects.create(
            owner=self.user,
            title="Test Book",
            publisher="Acme",
            published_date=date(2024, 1, 1),
            category=category,
            distribution_expense=Decimal("10.00"),
            stock_on_hand=10,
            reorder_threshold=2,
        )

    def _sale_form_data(self, **overrides):
        data = {
            "book": self.book.id,
            "quantity": 3,
            "unit_price": "10.00",
            "sale_date": "2024-01-10",
            "channel": "online",
            "currency": "USD",
            "tax_rate": "0",
        }
        data.update(overrides)
        return data

    def test_create_sale_decreases_stock(self):
        response = self.client.post(reverse("sale_create"), self._sale_form_data())

        self.assertEqual(response.status_code, 302)
        self.book.refresh_from_db()
        self.assertEqual(self.book.stock_on_hand, 7)

    def test_create_sale_rejected_when_quantity_exceeds_stock(self):
        response = self.client.post(reverse("sale_create"), self._sale_form_data(quantity=50))

        self.assertEqual(response.status_code, 200)
        self.book.refresh_from_db()
        self.assertEqual(self.book.stock_on_hand, 10)
        self.assertFalse(Sale.objects.filter(book=self.book).exists())

    def test_update_sale_adjusts_stock_by_delta(self):
        self.client.post(reverse("sale_create"), self._sale_form_data(quantity=3))
        self.book.refresh_from_db()
        self.assertEqual(self.book.stock_on_hand, 7)

        sale = Sale.objects.get(book=self.book)
        response = self.client.post(
            reverse("sale_update", args=[sale.id]),
            self._sale_form_data(quantity=5),
        )

        self.assertEqual(response.status_code, 302)
        self.book.refresh_from_db()
        self.assertEqual(self.book.stock_on_hand, 5)

    def test_delete_sale_restores_stock(self):
        self.client.post(reverse("sale_create"), self._sale_form_data(quantity=3))
        self.book.refresh_from_db()
        self.assertEqual(self.book.stock_on_hand, 7)

        sale = Sale.objects.get(book=self.book)
        response = self.client.post(reverse("sale_delete", args=[sale.id]))

        self.assertEqual(response.status_code, 302)
        self.book.refresh_from_db()
        self.assertEqual(self.book.stock_on_hand, 10)
        self.assertFalse(Sale.objects.filter(id=sale.id).exists())


class CheckoutTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="cashier", password="pass1234")
        self.account = get_or_create_account_for_user(self.user)
        grant(self.user, "view_book", "add_sale", "view_saletransaction", "add_return")
        self.client.force_login(self.user)

        category = Category.objects.create(owner=self.user, account=self.account, name="Fiction")
        self.book_a = Book.objects.create(
            owner=self.user, account=self.account, title="Book A", isbn="111",
            publisher="Acme", published_date=date(2024, 1, 1), category=category,
            distribution_expense=Decimal("5.00"), list_price=Decimal("20.00"), stock_on_hand=10,
        )
        self.book_b = Book.objects.create(
            owner=self.user, account=self.account, title="Book B", isbn="222",
            publisher="Acme", published_date=date(2024, 1, 1), category=category,
            distribution_expense=Decimal("5.00"), list_price=Decimal("15.00"), stock_on_hand=3,
        )

    def _checkout(self, lines, **overrides):
        payload = {
            "lines": lines,
            "payment_method": "cash",
            "currency": "USD",
        }
        payload.update(overrides)
        return self.client.post(
            reverse("checkout_complete"), data=json.dumps(payload), content_type="application/json",
        )

    def test_checkout_page_requires_permission(self):
        other = get_user_model().objects.create_user(username="nopower", password="pass1234")
        self.client.force_login(other)
        response = self.client.get(reverse("checkout"))
        self.assertEqual(response.status_code, 403)

    def test_lookup_scoped_to_account(self):
        other_user = get_user_model().objects.create_user(username="other_acct", password="pass1234")
        other_account = get_or_create_account_for_user(other_user)
        Book.objects.create(
            owner=other_user, account=other_account, title="Other Account Book", isbn="999",
            publisher="X", published_date=date(2024, 1, 1),
            category=Category.objects.create(owner=other_user, account=other_account, name="Other"),
            distribution_expense=Decimal("1.00"), stock_on_hand=5,
        )

        response = self.client.get(reverse("checkout_book_lookup"), {"q": "Other Account"})
        self.assertEqual(response.json()["results"], [])

    def test_single_line_checkout_deducts_stock_and_creates_records(self):
        response = self._checkout([{"book_id": self.book_a.id, "quantity": 2, "unit_price": "20.00"}])
        self.assertEqual(response.status_code, 200)

        self.book_a.refresh_from_db()
        self.assertEqual(self.book_a.stock_on_hand, 8)

        sale_tx = SaleTransaction.objects.get(account=self.account)
        self.assertEqual(sale_tx.line_items.count(), 1)
        self.assertTrue(sale_tx.receipt_number.startswith("RCT-"))

    def test_checkout_applies_per_line_tax_rate(self):
        response = self._checkout([
            {"book_id": self.book_a.id, "quantity": 2, "unit_price": "20.00", "tax_rate": "10"},
        ])
        self.assertEqual(response.status_code, 200)

        sale_tx = SaleTransaction.objects.get(account=self.account)
        sale = sale_tx.line_items.get()
        self.assertEqual(sale.tax_rate, Decimal("10"))
        self.assertEqual(sale.tax_amount, Decimal("4.00"))
        self.assertEqual(sale_tx.tax_total, Decimal("4.00"))
        self.assertEqual(sale_tx.total, Decimal("44.00"))

    def test_checkout_rejects_invalid_tax_rate(self):
        response = self._checkout([
            {"book_id": self.book_a.id, "quantity": 1, "unit_price": "20.00", "tax_rate": "150"},
        ])
        self.assertEqual(response.status_code, 400)

    def test_multi_line_checkout_deducts_stock_for_each_book(self):
        response = self._checkout([
            {"book_id": self.book_a.id, "quantity": 1, "unit_price": "20.00"},
            {"book_id": self.book_b.id, "quantity": 2, "unit_price": "15.00"},
        ])
        self.assertEqual(response.status_code, 200)

        self.book_a.refresh_from_db()
        self.book_b.refresh_from_db()
        self.assertEqual(self.book_a.stock_on_hand, 9)
        self.assertEqual(self.book_b.stock_on_hand, 1)

        sale_tx = SaleTransaction.objects.get(account=self.account)
        self.assertEqual(sale_tx.line_items.count(), 2)

    def test_checkout_rejected_when_any_line_exceeds_stock(self):
        response = self._checkout([
            {"book_id": self.book_a.id, "quantity": 1, "unit_price": "20.00"},
            {"book_id": self.book_b.id, "quantity": 99, "unit_price": "15.00"},
        ])
        self.assertEqual(response.status_code, 400)

        self.book_a.refresh_from_db()
        self.book_b.refresh_from_db()
        self.assertEqual(self.book_a.stock_on_hand, 10)
        self.assertEqual(self.book_b.stock_on_hand, 3)
        self.assertFalse(SaleTransaction.objects.filter(account=self.account).exists())

    def test_checkout_rejects_book_from_another_account(self):
        other_user = get_user_model().objects.create_user(username="other_acct2", password="pass1234")
        other_account = get_or_create_account_for_user(other_user)
        foreign_book = Book.objects.create(
            owner=other_user, account=other_account, title="Foreign Book", isbn="333",
            publisher="X", published_date=date(2024, 1, 1),
            category=Category.objects.create(owner=other_user, account=other_account, name="Other"),
            distribution_expense=Decimal("1.00"), stock_on_hand=5,
        )

        response = self._checkout([{"book_id": foreign_book.id, "quantity": 1, "unit_price": "5.00"}])
        self.assertEqual(response.status_code, 400)
        foreign_book.refresh_from_db()
        self.assertEqual(foreign_book.stock_on_hand, 5)

    def test_empty_cart_rejected(self):
        response = self._checkout([])
        self.assertEqual(response.status_code, 400)

    def test_invalid_payment_method_rejected(self):
        response = self._checkout(
            [{"book_id": self.book_a.id, "quantity": 1, "unit_price": "20.00"}],
            payment_method="bitcoin",
        )
        self.assertEqual(response.status_code, 400)

    def test_receipt_page_renders_and_is_account_scoped(self):
        self._checkout([{"book_id": self.book_a.id, "quantity": 1, "unit_price": "20.00"}])
        sale_tx = SaleTransaction.objects.get(account=self.account)

        response = self.client.get(reverse("checkout_receipt", args=[sale_tx.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, sale_tx.receipt_number)

        other_user = get_user_model().objects.create_user(username="other_acct3", password="pass1234")
        get_or_create_account_for_user(other_user)
        grant(other_user, "view_saletransaction")
        self.client.force_login(other_user)
        response = self.client.get(reverse("checkout_receipt", args=[sale_tx.id]))
        self.assertEqual(response.status_code, 404)

    def test_history_lists_transactions_scoped_to_account(self):
        self._checkout([{"book_id": self.book_a.id, "quantity": 1, "unit_price": "20.00"}])
        sale_tx = SaleTransaction.objects.get(account=self.account)

        other_user = get_user_model().objects.create_user(username="other_acct4", password="pass1234")
        other_account = get_or_create_account_for_user(other_user)
        SaleTransaction.objects.create(owner=other_user, account=other_account, receipt_number="RCT-OTHER-0001")

        response = self.client.get(reverse("checkout_history"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, sale_tx.receipt_number)
        self.assertNotContains(response, "RCT-OTHER-0001")

    def test_history_requires_permission(self):
        other = get_user_model().objects.create_user(username="nopower2", password="pass1234")
        self.client.force_login(other)
        response = self.client.get(reverse("checkout_history"))
        self.assertEqual(response.status_code, 403)

    def test_history_date_filter(self):
        self._checkout([{"book_id": self.book_a.id, "quantity": 1, "unit_price": "20.00"}])
        sale_tx = SaleTransaction.objects.get(account=self.account)

        future = (timezone.now() + timedelta(days=1)).date().isoformat()
        response = self.client.get(reverse("checkout_history"), {"start_date": future})
        self.assertNotContains(response, sale_tx.receipt_number)

    def test_history_search_by_receipt_number(self):
        self._checkout([{"book_id": self.book_a.id, "quantity": 1, "unit_price": "20.00"}])
        sale_tx = SaleTransaction.objects.get(account=self.account)

        response = self.client.get(reverse("checkout_history"), {"q": sale_tx.receipt_number})
        self.assertContains(response, sale_tx.receipt_number)

        response = self.client.get(reverse("checkout_history"), {"q": "no-such-receipt"})
        self.assertNotContains(response, sale_tx.receipt_number)

    def test_history_export_csv_contains_receipt(self):
        self._checkout([{"book_id": self.book_a.id, "quantity": 1, "unit_price": "20.00"}])
        sale_tx = SaleTransaction.objects.get(account=self.account)

        response = self.client.get(reverse("export_checkout_history_csv"))
        self.assertEqual(response.status_code, 200)
        self.assertIn(sale_tx.receipt_number.encode(), response.content)

    def test_history_export_pdf_succeeds(self):
        self._checkout([{"book_id": self.book_a.id, "quantity": 1, "unit_price": "20.00"}])
        response = self.client.get(reverse("export_checkout_history_pdf"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_receipt_pdf_renders(self):
        self._checkout([{"book_id": self.book_a.id, "quantity": 1, "unit_price": "20.00"}])
        sale_tx = SaleTransaction.objects.get(account=self.account)

        response = self.client.get(reverse("checkout_receipt_pdf", args=[sale_tx.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_receipt_pdf_renders_with_custom_logo_and_brand_color(self):
        from io import BytesIO
        from PIL import Image as PILImage
        from django.core.files.uploadedfile import SimpleUploadedFile

        buf = BytesIO()
        PILImage.new("RGB", (10, 10), color=(0, 128, 255)).save(buf, format="PNG")
        buf.seek(0)
        self.account.logo = SimpleUploadedFile("logo.png", buf.read(), content_type="image/png")
        self.account.brand_color = "#336699"
        self.account.save()

        self._checkout([{"book_id": self.book_a.id, "quantity": 1, "unit_price": "20.00"}])
        sale_tx = SaleTransaction.objects.get(account=self.account)

        response = self.client.get(reverse("checkout_receipt_pdf", args=[sale_tx.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_receipt_pdf_scoped_to_account(self):
        self._checkout([{"book_id": self.book_a.id, "quantity": 1, "unit_price": "20.00"}])
        sale_tx = SaleTransaction.objects.get(account=self.account)

        other_user = get_user_model().objects.create_user(username="other_acct6", password="pass1234")
        get_or_create_account_for_user(other_user)
        grant(other_user, "view_saletransaction")
        self.client.force_login(other_user)

        response = self.client.get(reverse("checkout_receipt_pdf", args=[sale_tx.id]))
        self.assertEqual(response.status_code, 404)

    def test_checkout_uses_account_default_tax_rate(self):
        self.account.default_tax_rate = Decimal("7.00")
        self.account.save(update_fields=["default_tax_rate"])

        response = self.client.get(reverse("checkout"))
        self.assertContains(response, "7.00")

    def test_void_transaction_restores_stock_and_creates_returns(self):
        self._checkout([
            {"book_id": self.book_a.id, "quantity": 2, "unit_price": "20.00"},
            {"book_id": self.book_b.id, "quantity": 1, "unit_price": "15.00"},
        ])
        self.book_a.refresh_from_db()
        self.book_b.refresh_from_db()
        self.assertEqual(self.book_a.stock_on_hand, 8)
        self.assertEqual(self.book_b.stock_on_hand, 2)

        sale_tx = SaleTransaction.objects.get(account=self.account)
        response = self.client.post(reverse("checkout_void", args=[sale_tx.id]))
        self.assertRedirects(response, reverse("checkout_receipt", args=[sale_tx.id]))

        self.book_a.refresh_from_db()
        self.book_b.refresh_from_db()
        self.assertEqual(self.book_a.stock_on_hand, 10)
        self.assertEqual(self.book_b.stock_on_hand, 3)

        for sale in sale_tx.line_items.all():
            self.assertEqual(sale.quantity, 0)
        self.assertEqual(Return.objects.filter(sale__transaction=sale_tx).count(), 2)
        self.assertTrue(sale_tx.is_fully_refunded)

    def test_voiding_already_voided_transaction_is_a_safe_no_op(self):
        self._checkout([{"book_id": self.book_a.id, "quantity": 1, "unit_price": "20.00"}])
        sale_tx = SaleTransaction.objects.get(account=self.account)

        self.client.post(reverse("checkout_void", args=[sale_tx.id]))
        self.book_a.refresh_from_db()
        self.assertEqual(self.book_a.stock_on_hand, 10)

        self.client.post(reverse("checkout_void", args=[sale_tx.id]))
        self.book_a.refresh_from_db()
        self.assertEqual(self.book_a.stock_on_hand, 10)
        self.assertEqual(Return.objects.filter(sale__transaction=sale_tx).count(), 1)

    def test_void_requires_permission(self):
        self._checkout([{"book_id": self.book_a.id, "quantity": 1, "unit_price": "20.00"}])
        sale_tx = SaleTransaction.objects.get(account=self.account)

        other = get_user_model().objects.create_user(username="nopower3", password="pass1234")
        self.client.force_login(other)
        response = self.client.post(reverse("checkout_void", args=[sale_tx.id]))
        self.assertEqual(response.status_code, 403)

    def test_void_scoped_to_account(self):
        other_user = get_user_model().objects.create_user(username="other_acct5", password="pass1234")
        other_account = get_or_create_account_for_user(other_user)
        category = Category.objects.create(owner=other_user, account=other_account, name="Other")
        foreign_book = Book.objects.create(
            owner=other_user, account=other_account, title="Foreign", isbn="444",
            publisher="X", published_date=date(2024, 1, 1), category=category,
            distribution_expense=Decimal("1.00"), stock_on_hand=5,
        )
        foreign_tx = SaleTransaction.objects.create(owner=other_user, account=other_account, receipt_number="RCT-FOREIGN-0001")
        Sale.objects.create(
            owner=other_user, account=other_account, book=foreign_book, quantity=1,
            unit_price=Decimal("5.00"), sale_date=date(2024, 1, 1), transaction=foreign_tx,
        )

        response = self.client.post(reverse("checkout_void", args=[foreign_tx.id]))
        self.assertEqual(response.status_code, 404)
        foreign_book.refresh_from_db()
        self.assertEqual(foreign_book.stock_on_hand, 5)

    def test_duplicate_receipt_number_same_account_rejected_at_db_level(self):
        SaleTransaction.objects.create(
            owner=self.user, account=self.account, receipt_number="RCT-DUPETEST-0001",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SaleTransaction.objects.create(
                    owner=self.user, account=self.account, receipt_number="RCT-DUPETEST-0001",
                )

    def test_checkout_retries_when_receipt_number_collides(self):
        # Pre-create a transaction holding the exact receipt number
        # _next_receipt_number would generate next, simulating another
        # checkout winning the race - the retry loop should recover and
        # still complete this checkout under a different number.
        next_number = _next_receipt_number(self.account)
        SaleTransaction.objects.create(owner=self.user, account=self.account, receipt_number=next_number)

        response = self._checkout([{"book_id": self.book_a.id, "quantity": 1, "unit_price": "20.00"}])
        self.assertEqual(response.status_code, 200)

        self.book_a.refresh_from_db()
        self.assertEqual(self.book_a.stock_on_hand, 9)

        new_tx = SaleTransaction.objects.exclude(receipt_number=next_number).get(account=self.account)
        self.assertNotEqual(new_tx.receipt_number, next_number)


class AuthorViewPermissionTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.unauthorized_user = User.objects.create_user(
            username="nobody", password="pass1234"
        )

        self.author_manager = User.objects.create_user(
            username="manager", password="pass1234"
        )
        grant(
            self.author_manager,
            "view_author",
            "add_author",
            "change_author",
            "delete_author",
        )

        self.author = Author.objects.create(owner=self.author_manager, name="Jane Doe")

    def test_author_list_requires_permission(self):
        self.client.force_login(self.unauthorized_user)
        response = self.client.get(reverse("author_list"))
        self.assertEqual(response.status_code, 403)

    def test_author_list_accessible_with_permission(self):
        self.client.force_login(self.author_manager)
        response = self.client.get(reverse("author_list"))
        self.assertEqual(response.status_code, 200)

    def test_author_create_with_permission(self):
        self.client.force_login(self.author_manager)
        response = self.client.post(reverse("author_create"), {"name": "New Author"})

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Author.objects.filter(name="New Author").exists())

    def test_author_delete_blocked_when_linked_to_books(self):
        category = Category.objects.create(owner=self.author_manager, name="Fiction")
        book = Book.objects.create(
            owner=self.author_manager,
            title="Test Book",
            publisher="Acme",
            published_date=date(2024, 1, 1),
            category=category,
            distribution_expense=Decimal("10.00"),
        )
        book.authors.add(self.author)

        self.client.force_login(self.author_manager)
        response = self.client.post(reverse("author_delete", args=[self.author.id]))

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Author.objects.filter(id=self.author.id).exists())

    def test_author_delete_allowed_when_unlinked(self):
        self.client.force_login(self.author_manager)
        response = self.client.post(reverse("author_delete", args=[self.author.id]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Author.objects.filter(id=self.author.id).exists())


class ReportViewTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="viewer", password="pass1234")
        grant(self.user, "view_book")
        self.client.force_login(self.user)

        self.category = Category.objects.create(owner=self.user, name="Fiction")
        self.book = Book.objects.create(
            owner=self.user,
            title="Test Book",
            publisher="Acme",
            published_date=date(2024, 1, 1),
            category=self.category,
            distribution_expense=Decimal("100.00"),
        )
        Sale.objects.create(
            owner=self.user,
            book=self.book,
            quantity=10,
            unit_price=Decimal("12.50"),
            sale_date=date(2024, 1, 5),
        )

    def test_report_totals(self):
        response = self.client.get(reverse("report"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_expense"], Decimal("100.00"))
        self.assertEqual(response.context["total_revenue"], Decimal("125.00"))
        self.assertEqual(response.context["total_profit"], Decimal("25.00"))

    def test_report_sales_trend(self):
        Sale.objects.create(
            owner=self.user,
            book=self.book,
            quantity=4,
            unit_price=Decimal("10.00"),
            sale_date=date(2024, 2, 15),
        )

        response = self.client.get(reverse("report"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            json.loads(response.context["trend_labels"]),
            ["Jan 2024", "Feb 2024"],
        )
        self.assertEqual(json.loads(response.context["trend_units"]), [10, 4])
        self.assertEqual(
            json.loads(response.context["trend_revenues"]),
            [125.0, 40.0],
        )


class ChatApiTests(TestCase):

    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.user = User.objects.create_user(username="chatter", password="pass1234")

    def test_anonymous_post_redirects_to_login(self):
        response = self.client.post(
            reverse("chat_api"),
            data=json.dumps({"message": "hello"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_get_not_allowed(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("chat_api"))
        self.assertEqual(response.status_code, 405)

    def test_missing_message_returns_400(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("chat_api"),
            data=json.dumps({"message": "  "}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_not_configured_without_api_key(self):
        self.client.force_login(self.user)
        with self.settings(ANTHROPIC_API_KEY=""):
            response = self.client.post(
                reverse("chat_api"),
                data=json.dumps({"message": "What does the Stock page show?"}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["reply"], ai_chat.NOT_CONFIGURED_REPLY)

    def test_chat_reply_uses_ai_chat_module(self):
        self.client.force_login(self.user)

        with patch.object(ai_chat, "get_chat_reply", return_value=("Hello!", [])) as mocked:
            response = self.client.post(
                reverse("chat_api"),
                data=json.dumps({"message": "Hi there", "history": []}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["reply"], "Hello!")
        account = AccountMembership.objects.get(user=self.user).account
        mocked.assert_called_once_with(self.user, account, "Hi there", [])

    def test_chat_reply_failure_is_logged_not_silent(self):
        self.client.force_login(self.user)

        with patch.object(ai_chat, "get_chat_reply", side_effect=RuntimeError("boom")):
            with self.assertLogs("books.views", level="ERROR") as logs:
                response = self.client.post(
                    reverse("chat_api"),
                    data=json.dumps({"message": "Hi there", "history": []}),
                    content_type="application/json",
                )

        self.assertEqual(response.status_code, 200)
        self.assertIn("something went wrong", response.json()["reply"].lower())
        self.assertTrue(any("AI chat request failed" in record for record in logs.output))


class AiChatToolTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="tooluser", password="pass1234")
        self.account = get_or_create_account_for_user(self.user)

        self.fiction = Category.objects.create(owner=self.user, name="Fiction")
        self.author = Author.objects.create(owner=self.user, name="Jane Doe")

        self.low_book = Book.objects.create(
            owner=self.user,
            title="Low Stock Book",
            isbn="111",
            publisher="Acme",
            published_date=date(2024, 1, 1),
            category=self.fiction,
            distribution_expense=Decimal("10.00"),
            stock_on_hand=1,
            reorder_threshold=5,
        )
        self.low_book.authors.add(self.author)

        self.ok_book = Book.objects.create(
            owner=self.user,
            title="Well Stocked Book",
            isbn="222",
            publisher="Acme",
            published_date=date(2024, 1, 1),
            category=self.fiction,
            distribution_expense=Decimal("10.00"),
            stock_on_hand=50,
            reorder_threshold=5,
        )

        Sale.objects.create(
            owner=self.user,
            book=self.low_book,
            quantity=3,
            unit_price=Decimal("10.00"),
            sale_date=date(2024, 1, 15),
        )
        Sale.objects.create(
            owner=self.user,
            book=self.ok_book,
            quantity=1,
            unit_price=Decimal("20.00"),
            sale_date=date(2024, 2, 1),
        )

    def test_build_tools_for_user_filters_by_permission(self):
        grant(self.user, "view_book")

        tool_names = {tool["name"] for tool in ai_chat.build_tools_for_user(self.user)}
        self.assertIn("get_low_stock_books", tool_names)
        self.assertIn("list_books", tool_names)
        self.assertIn("search_books", tool_names)
        self.assertNotIn("get_sales_summary", tool_names)
        self.assertNotIn("get_categories", tool_names)
        self.assertNotIn("get_reorder_suggestions", tool_names)
        self.assertNotIn("get_slow_moving_books", tool_names)
        self.assertNotIn("draft_supplier_email", tool_names)
        self.assertNotIn("get_overdue_invoices", tool_names)
        self.assertNotIn("get_customer_balance", tool_names)
        self.assertNotIn("get_royalty_summary", tool_names)
        self.assertNotIn("get_sales_trend", tool_names)
        self.assertNotIn("get_top_customers", tool_names)
        self.assertIn("get_category_performance", tool_names)
        self.assertIn("get_business_insights", tool_names)

    def test_list_books_returns_everything(self):
        result = ai_chat.list_books({}, self.account)
        titles = {book["title"] for book in result["books"]}
        self.assertEqual(titles, {"Low Stock Book", "Well Stocked Book"})

    def test_list_books_filters_by_category(self):
        other = Category.objects.create(owner=self.user, name="Non-Fiction")
        Book.objects.create(
            owner=self.user,
            title="Other Category Book",
            isbn="333",
            publisher="Acme",
            published_date=date(2024, 1, 1),
            category=other,
            distribution_expense=Decimal("10.00"),
            stock_on_hand=10,
            reorder_threshold=5,
        )

        result = ai_chat.list_books({"category": "Fiction"}, self.account)
        titles = {book["title"] for book in result["books"]}
        self.assertEqual(titles, {"Low Stock Book", "Well Stocked Book"})

    def test_list_books_filters_by_author(self):
        result = ai_chat.list_books({"author": "Jane"}, self.account)
        titles = {book["title"] for book in result["books"]}
        self.assertEqual(titles, {"Low Stock Book"})

    def test_list_books_filters_by_stock_range(self):
        result = ai_chat.list_books({"min_stock": 10}, self.account)
        titles = {book["title"] for book in result["books"]}
        self.assertEqual(titles, {"Well Stocked Book"})

        result = ai_chat.list_books({"max_stock": 10}, self.account)
        titles = {book["title"] for book in result["books"]}
        self.assertEqual(titles, {"Low Stock Book"})

    def test_list_books_filters_by_price_range(self):
        Book.objects.create(
            owner=self.user,
            title="Pricey Book",
            isbn="444",
            publisher="Acme",
            published_date=date(2024, 1, 1),
            category=self.fiction,
            distribution_expense=Decimal("50.00"),
            stock_on_hand=5,
            reorder_threshold=5,
        )

        result = ai_chat.list_books({"max_price": 20}, self.account)
        titles = {book["title"] for book in result["books"]}
        self.assertEqual(titles, {"Low Stock Book", "Well Stocked Book"})

        result = ai_chat.list_books({"min_price": 20}, self.account)
        titles = {book["title"] for book in result["books"]}
        self.assertEqual(titles, {"Pricey Book"})

    def test_get_low_stock_books(self):
        result = ai_chat.get_low_stock_books({}, self.account)
        titles = {book["title"] for book in result["books"]}
        self.assertEqual(titles, {"Low Stock Book"})

    def test_search_books_matches_author(self):
        result = ai_chat.search_books({"query": "Jane"}, self.account)
        titles = {book["title"] for book in result["books"]}
        self.assertEqual(titles, {"Low Stock Book"})

    def test_get_sales_summary_filters_by_date(self):
        result = ai_chat.get_sales_summary({"start_date": "2024-02-01"}, self.account)
        self.assertEqual(result["total_units_sold"], 1)
        self.assertEqual(result["sale_count"], 1)

    def test_get_top_selling_books(self):
        result = ai_chat.get_top_selling_books({"limit": 1}, self.account)
        self.assertEqual(len(result["books"]), 1)
        self.assertEqual(result["books"][0]["title"], "Low Stock Book")
        self.assertEqual(result["books"][0]["units_sold"], 3)

    def test_get_categories(self):
        result = ai_chat.get_categories({}, self.account)
        self.assertEqual(
            result["categories"],
            [{"name": "Fiction", "book_count": 2}],
        )

    def test_execute_tool_denies_without_permission(self):
        result = ai_chat.execute_tool("get_categories", {}, self.user, self.account)
        self.assertIn("error", result)

    def test_get_reorder_suggestions_includes_low_stock_book(self):
        result = ai_chat.get_reorder_suggestions({}, self.account)
        titles = {item["title"] for item in result["suggestions"]}
        self.assertEqual(titles, {"Low Stock Book"})

        suggestion = result["suggestions"][0]
        self.assertEqual(suggestion["suggested_quantity"], 9)
        self.assertEqual(suggestion["reorder_url"], f"/reorders/add/{self.low_book.id}/")

    def test_get_reorder_suggestions_respects_limit(self):
        Book.objects.create(
            owner=self.user, account=self.account, title="Another Low Book", isbn="555",
            publisher="Acme", published_date=date(2024, 1, 1), category=self.fiction,
            distribution_expense=Decimal("10.00"), stock_on_hand=1, reorder_threshold=5,
        )

        result = ai_chat.get_reorder_suggestions({"limit": 1}, self.account)
        self.assertEqual(len(result["suggestions"]), 1)

    def test_get_slow_moving_books_excludes_recently_sold_and_out_of_stock(self):
        recently_sold = Book.objects.create(
            owner=self.user, account=self.account, title="Recently Sold Book", isbn="666",
            publisher="Acme", published_date=date(2024, 1, 1), category=self.fiction,
            distribution_expense=Decimal("10.00"), stock_on_hand=20, reorder_threshold=5,
        )
        Sale.objects.create(
            owner=self.user, account=self.account, book=recently_sold,
            quantity=1, unit_price=Decimal("10.00"), sale_date=date.today(),
        )
        Book.objects.create(
            owner=self.user, account=self.account, title="Out of Stock Book", isbn="777",
            publisher="Acme", published_date=date(2024, 1, 1), category=self.fiction,
            distribution_expense=Decimal("10.00"), stock_on_hand=0, reorder_threshold=5,
        )

        result = ai_chat.get_slow_moving_books({}, self.account)
        titles = {item["title"] for item in result["books"]}
        self.assertIn("Well Stocked Book", titles)
        self.assertIn("Low Stock Book", titles)
        self.assertNotIn("Recently Sold Book", titles)
        self.assertNotIn("Out of Stock Book", titles)

    def test_get_slow_moving_books_sorted_by_stock_value_descending(self):
        result = ai_chat.get_slow_moving_books({}, self.account)
        values = [Decimal(item["stock_value"]) for item in result["books"]]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_draft_supplier_email_returns_subject_and_body(self):
        supplier = Supplier.objects.create(
            owner=self.user, account=self.account, name="Acme Supplies",
            contact_name="Pat", email="orders@acme.test",
        )

        result = ai_chat.draft_supplier_email(
            {"supplier_name": "Acme", "items": [{"title": "Low Stock", "quantity": 10}]},
            self.account,
        )

        self.assertEqual(result["supplier_name"], supplier.name)
        self.assertEqual(result["supplier_email"], "orders@acme.test")
        self.assertIn("Low Stock Book", result["body"])
        self.assertIn("10 units", result["body"])
        self.assertEqual(result["note"], "")

    def test_draft_supplier_email_notes_missing_email(self):
        Supplier.objects.create(owner=self.user, account=self.account, name="No Email Supplier")

        result = ai_chat.draft_supplier_email(
            {"supplier_name": "No Email", "items": [{"title": "Low Stock", "quantity": 5}]},
            self.account,
        )

        self.assertEqual(result["supplier_email"], "")
        self.assertNotEqual(result["note"], "")

    def test_draft_supplier_email_unknown_supplier_returns_error(self):
        result = ai_chat.draft_supplier_email(
            {"supplier_name": "Nonexistent", "items": [{"title": "Low Stock", "quantity": 5}]},
            self.account,
        )
        self.assertIn("error", result)

    def test_draft_supplier_email_no_matching_books_returns_error(self):
        Supplier.objects.create(owner=self.user, account=self.account, name="Acme Supplies")

        result = ai_chat.draft_supplier_email(
            {"supplier_name": "Acme", "items": [{"title": "Nonexistent Title", "quantity": 5}]},
            self.account,
        )
        self.assertIn("error", result)

    def test_get_overdue_invoices_returns_only_overdue(self):
        overdue = Invoice.objects.create(
            owner=self.user, account=self.account, customer_name="Late Customer",
            invoice_number="INV-1", invoice_date=date(2024, 1, 1),
            due_date=date(2024, 1, 10), currency="USD", status=Invoice.STATUS_SENT,
        )
        InvoiceItem.objects.create(invoice=overdue, description="Book", quantity=1, unit_price=Decimal("50.00"))

        not_due_yet = Invoice.objects.create(
            owner=self.user, account=self.account, customer_name="Future Customer",
            invoice_number="INV-2", invoice_date=date.today(),
            due_date=date.today() + timedelta(days=30), currency="USD", status=Invoice.STATUS_SENT,
        )
        InvoiceItem.objects.create(invoice=not_due_yet, description="Book", quantity=1, unit_price=Decimal("20.00"))

        paid = Invoice.objects.create(
            owner=self.user, account=self.account, customer_name="Paid Customer",
            invoice_number="INV-3", invoice_date=date(2024, 1, 1),
            due_date=date(2024, 1, 10), currency="USD", status=Invoice.STATUS_PAID,
        )
        InvoiceItem.objects.create(invoice=paid, description="Book", quantity=1, unit_price=Decimal("30.00"))

        result = ai_chat.get_overdue_invoices({}, self.account)
        names = {item["customer_name"] for item in result["invoices"]}
        self.assertEqual(names, {"Late Customer"})
        self.assertEqual(result["invoices"][0]["grand_total"], "50.00")

    def test_get_customer_balance_returns_billed_and_outstanding(self):
        customer = Customer.objects.create(owner=self.user, account=self.account, name="Acme Books Co")

        paid_invoice = Invoice.objects.create(
            owner=self.user, account=self.account, customer=customer, customer_name=customer.name,
            invoice_date=date.today(), currency="USD", status=Invoice.STATUS_PAID,
        )
        InvoiceItem.objects.create(invoice=paid_invoice, description="Book", quantity=1, unit_price=Decimal("40.00"))

        overdue_invoice = Invoice.objects.create(
            owner=self.user, account=self.account, customer=customer, customer_name=customer.name,
            invoice_date=date(2024, 1, 1), due_date=date(2024, 1, 10),
            currency="USD", status=Invoice.STATUS_SENT,
        )
        InvoiceItem.objects.create(invoice=overdue_invoice, description="Book", quantity=1, unit_price=Decimal("60.00"))

        result = ai_chat.get_customer_balance({"customer_name": "Acme"}, self.account)
        self.assertEqual(result["customer_name"], "Acme Books Co")
        self.assertEqual(result["billed_by_currency"], {"USD": "100.00"})
        self.assertEqual(result["outstanding_by_currency"], {"USD": "60.00"})
        self.assertEqual(result["overdue_count"], 1)

    def test_get_customer_balance_unknown_customer_returns_error(self):
        result = ai_chat.get_customer_balance({"customer_name": "Nobody"}, self.account)
        self.assertIn("error", result)

    def test_get_customer_balance_missing_name_returns_error(self):
        result = ai_chat.get_customer_balance({}, self.account)
        self.assertIn("error", result)

    def test_get_royalty_summary_computes_outstanding(self):
        RoyaltyRate.objects.create(
            owner=self.user, account=self.account, book=self.low_book, author=self.author,
            rate=Decimal("10.00"), effective_from=date(2024, 1, 1),
        )
        RoyaltyPayment.objects.create(
            owner=self.user, account=self.account, author=self.author,
            amount=Decimal("1.00"), currency="USD", payment_date=date(2024, 2, 1),
        )

        result = ai_chat.get_royalty_summary({}, self.account)
        self.assertEqual(len(result["authors"]), 1)
        entry = result["authors"][0]
        self.assertEqual(entry["author"], "Jane Doe")
        self.assertEqual(entry["total_earned"], "3.00")
        self.assertEqual(entry["total_paid"], "1.00")
        self.assertEqual(entry["outstanding"], "2.00")

    def test_get_royalty_summary_filters_by_author(self):
        other_author = Author.objects.create(owner=self.user, account=self.account, name="Other Author")
        RoyaltyRate.objects.create(
            owner=self.user, account=self.account, book=self.ok_book, author=other_author,
            rate=Decimal("5.00"), effective_from=date(2024, 1, 1),
        )

        result = ai_chat.get_royalty_summary({"author_name": "Other"}, self.account)
        names = {entry["author"] for entry in result["authors"]}
        self.assertEqual(names, {"Other Author"})

    def test_get_sales_trend_detects_growth(self):
        Sale.objects.create(
            owner=self.user, account=self.account, book=self.low_book,
            quantity=1, unit_price=Decimal("100.00"), sale_date=date.today() - timedelta(days=5),
        )
        Sale.objects.create(
            owner=self.user, account=self.account, book=self.low_book,
            quantity=1, unit_price=Decimal("10.00"), sale_date=date.today() - timedelta(days=35),
        )

        result = ai_chat.get_sales_trend({"days": 30}, self.account)
        self.assertEqual(result["current_revenue"], "100.00")
        self.assertEqual(result["previous_revenue"], "10.00")
        self.assertEqual(result["direction"], "up")
        self.assertEqual(result["revenue_change_percent"], 900.0)

    def test_get_sales_trend_handles_no_sales(self):
        result = ai_chat.get_sales_trend({}, self.account)
        self.assertEqual(result["current_revenue"], "0.00")
        self.assertIsNone(result["revenue_change_percent"])
        self.assertEqual(result["direction"], "flat")

    def test_get_category_performance_aggregates_revenue_and_profit(self):
        result = ai_chat.get_category_performance({}, self.account)
        self.assertEqual(len(result["categories"]), 1)
        entry = result["categories"][0]
        self.assertEqual(entry["category"], "Fiction")
        self.assertEqual(entry["revenue"], "50.00")
        self.assertEqual(entry["profit"], "30.00")

    def test_get_category_performance_respects_days_filter(self):
        result = ai_chat.get_category_performance({"days": 1}, self.account)
        entry = result["categories"][0]
        self.assertEqual(entry["revenue"], "0.00")

    def test_get_top_customers_ranks_by_total_billed(self):
        big_customer = Customer.objects.create(owner=self.user, account=self.account, name="Big Spender")
        small_customer = Customer.objects.create(owner=self.user, account=self.account, name="Small Spender")

        big_invoice = Invoice.objects.create(
            owner=self.user, account=self.account, customer=big_customer, customer_name=big_customer.name,
            invoice_date=date.today(), currency="USD", status=Invoice.STATUS_SENT,
        )
        InvoiceItem.objects.create(invoice=big_invoice, description="Order", quantity=1, unit_price=Decimal("500.00"))

        small_invoice = Invoice.objects.create(
            owner=self.user, account=self.account, customer=small_customer, customer_name=small_customer.name,
            invoice_date=date.today(), currency="USD", status=Invoice.STATUS_SENT,
        )
        InvoiceItem.objects.create(invoice=small_invoice, description="Order", quantity=1, unit_price=Decimal("50.00"))

        result = ai_chat.get_top_customers({"limit": 1}, self.account)
        self.assertEqual(len(result["customers"]), 1)
        self.assertEqual(result["customers"][0]["customer"], "Big Spender")
        self.assertEqual(result["customers"][0]["total_billed"], "500.00")

    def test_get_business_insights_flags_low_stock_and_respects_permissions(self):
        result = ai_chat.execute_tool("get_business_insights", {}, self.user, self.account)
        self.assertIn("error", result)

        grant(self.user, "view_book")
        self.user = get_user_model().objects.get(pk=self.user.pk)
        result = ai_chat.execute_tool("get_business_insights", {}, self.user, self.account)
        headlines = " ".join(item["headline"] for item in result["insights"])
        self.assertIn("reorder threshold", headlines)
        self.assertNotIn("overdue invoice", headlines)

    def test_get_business_insights_reports_nothing_urgent_when_clean(self):
        Book.objects.filter(account=self.account).delete()
        grant(self.user, "view_book")
        self.user = get_user_model().objects.get(pk=self.user.pk)
        result = ai_chat.execute_tool("get_business_insights", {}, self.user, self.account)
        self.assertEqual(len(result["insights"]), 1)
        self.assertEqual(result["insights"][0]["headline"], "Nothing urgent right now")

    def test_get_recent_transactions_scoped_to_account(self):
        tx = SaleTransaction.objects.create(owner=self.user, account=self.account, receipt_number="RCT-TOOL-0001")
        Sale.objects.create(
            owner=self.user, account=self.account, book=self.low_book, quantity=1,
            unit_price=Decimal("10.00"), sale_date=date(2024, 1, 15), transaction=tx,
        )

        other_user = get_user_model().objects.create_user(username="tool_other", password="pass1234")
        other_account = get_or_create_account_for_user(other_user)
        SaleTransaction.objects.create(owner=other_user, account=other_account, receipt_number="RCT-OTHER-0001")

        result = ai_chat.get_recent_transactions({}, self.account)
        receipt_numbers = {item["receipt_number"] for item in result["transactions"]}
        self.assertEqual(receipt_numbers, {"RCT-TOOL-0001"})

    def test_get_transaction_by_receipt_returns_line_items(self):
        tx = SaleTransaction.objects.create(owner=self.user, account=self.account, receipt_number="RCT-TOOL-0002")
        Sale.objects.create(
            owner=self.user, account=self.account, book=self.low_book, quantity=2,
            unit_price=Decimal("10.00"), sale_date=date(2024, 1, 15), transaction=tx,
        )

        result = ai_chat.get_transaction_by_receipt({"receipt_number": "RCT-TOOL-0002"}, self.account)
        self.assertEqual(result["receipt_number"], "RCT-TOOL-0002")
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["title"], "Low Stock Book")

    def test_get_transaction_by_receipt_missing_returns_error(self):
        result = ai_chat.get_transaction_by_receipt({"receipt_number": "RCT-NOPE"}, self.account)
        self.assertIn("error", result)

    def test_get_stock_by_location_scoped_and_filterable(self):
        location = Location.objects.create(owner=self.user, account=self.account, name="Warehouse A")
        StockLevel.objects.create(owner=self.user, account=self.account, book=self.low_book, location=location, quantity=4)
        StockLevel.objects.create(owner=self.user, account=self.account, book=self.ok_book, location=location, quantity=20)

        result = ai_chat.get_stock_by_location({}, self.account)
        self.assertEqual(len(result["stock_levels"]), 2)

        filtered = ai_chat.get_stock_by_location({"book_title": "Low Stock"}, self.account)
        self.assertEqual(len(filtered["stock_levels"]), 1)
        self.assertEqual(filtered["stock_levels"][0]["quantity"], 4)

    def test_get_print_runs_filters_by_status(self):
        PrintRun.objects.create(
            owner=self.user, account=self.account, book=self.low_book, quantity=100,
            cost_per_unit=Decimal("2.00"), run_date=date(2024, 1, 1), status=PrintRun.STATUS_COMPLETED,
        )
        PrintRun.objects.create(
            owner=self.user, account=self.account, book=self.ok_book, quantity=50,
            cost_per_unit=Decimal("3.00"), run_date=date(2024, 2, 1), status=PrintRun.STATUS_PENDING,
        )

        result = ai_chat.get_print_runs({}, self.account)
        self.assertEqual(len(result["print_runs"]), 2)

        completed_only = ai_chat.get_print_runs({"status": "completed"}, self.account)
        self.assertEqual(len(completed_only["print_runs"]), 1)
        self.assertEqual(completed_only["print_runs"][0]["title"], "Low Stock Book")

    def test_get_returns_summary_within_day_window(self):
        sale = Sale.objects.create(
            owner=self.user, account=self.account, book=self.low_book, quantity=5,
            unit_price=Decimal("10.00"), sale_date=date(2024, 1, 15),
        )
        Return.objects.create(
            owner=self.user, account=self.account, sale=sale, quantity=1,
            reason="Damaged", return_date=timezone.now().date(),
        )
        Return.objects.create(
            owner=self.user, account=self.account, sale=sale, quantity=1,
            reason="Old return", return_date=date(2020, 1, 1),
        )

        result = ai_chat.get_returns_summary({"days": 30}, self.account)
        self.assertEqual(len(result["returns"]), 1)
        self.assertEqual(result["returns"][0]["reason"], "Damaged")

    def test_new_tools_gated_by_permission(self):
        grant(self.user, "view_book")
        self.user = get_user_model().objects.get(pk=self.user.pk)
        tool_names = {tool["name"] for tool in ai_chat.build_tools_for_user(self.user)}
        self.assertNotIn("get_recent_transactions", tool_names)
        self.assertNotIn("get_stock_by_location", tool_names)
        self.assertNotIn("get_print_runs", tool_names)
        self.assertNotIn("get_returns_summary", tool_names)

        grant(self.user, "view_saletransaction", "view_stocklevel", "view_printrun", "view_return")
        self.user = get_user_model().objects.get(pk=self.user.pk)
        tool_names = {tool["name"] for tool in ai_chat.build_tools_for_user(self.user)}
        self.assertIn("get_recent_transactions", tool_names)
        self.assertIn("get_transaction_by_receipt", tool_names)
        self.assertIn("get_stock_by_location", tool_names)
        self.assertIn("get_print_runs", tool_names)
        self.assertIn("get_returns_summary", tool_names)


class SetupRolesCommandTests(TestCase):

    def test_groups_have_expected_permissions(self):
        call_command("setup_roles")

        from django.contrib.auth.models import Group

        admin_codenames = set(
            Group.objects.get(name="Admin").permissions.values_list(
                "codename", flat=True
            )
        )
        self.assertIn("delete_author", admin_codenames)
        self.assertIn("add_sale", admin_codenames)

        viewer_codenames = set(
            Group.objects.get(name="Viewer").permissions.values_list(
                "codename", flat=True
            )
        )
        from books.permissions import ROLE_PERMISSIONS
        self.assertEqual(
            viewer_codenames,
            set(ROLE_PERMISSIONS["Viewer"]),
        )


class SignupFlowTests(TestCase):

    def _signup_data(self, **overrides):
        data = {
            "username": "newuser",
            "email": "newuser@example.com",
            "password1": "Sup3rSecret!",
            "password2": "Sup3rSecret!",
        }
        data.update(overrides)
        return data

    def test_valid_signup_creates_inactive_user_and_sends_email(self):
        response = self.client.post(reverse("signup"), self._signup_data())

        self.assertRedirects(response, reverse("verify_email"))

        User = get_user_model()
        user = User.objects.get(username="newuser")
        self.assertFalse(user.is_active)
        self.assertEqual(self.client.session["pending_user_id"], user.id)

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(user.profile.verification_code, mail.outbox[0].body)

    def test_duplicate_username_rejected(self):
        User = get_user_model()
        User.objects.create_user(username="newuser", password="pass1234")

        response = self.client.post(reverse("signup"), self._signup_data())

        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "username", "That username is already taken.")

    def test_duplicate_email_rejected(self):
        User = get_user_model()
        User.objects.create_user(
            username="someoneelse", password="pass1234", email="newuser@example.com"
        )

        response = self.client.post(reverse("signup"), self._signup_data())

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"], "email", "An account with that email already exists."
        )

    def test_mismatched_passwords_rejected(self):
        response = self.client.post(
            reverse("signup"), self._signup_data(password2="Different!")
        )

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"], None, "The two password fields didn't match."
        )

        User = get_user_model()
        self.assertFalse(User.objects.filter(username="newuser").exists())


class EmailVerificationTests(TestCase):

    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.user = User.objects.create_user(
            username="pending", password="pass1234", email="pending@example.com", is_active=False
        )
        self.profile, _ = Profile.objects.get_or_create(user=self.user)
        self.profile.verification_code = "123456"
        self.profile.verification_code_expires_at = timezone.now() + timedelta(minutes=15)
        self.profile.save()

        session = self.client.session
        session["pending_user_id"] = self.user.id
        session.save()

    def test_correct_code_verifies_and_redirects(self):
        response = self.client.post(reverse("verify_email"), {"code": "123456"})

        self.assertRedirects(response, reverse("redeem_access_code"))
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.email_verified)

    def test_verifying_notifies_superuser_owners(self):
        User = get_user_model()
        User.objects.create_superuser(
            username="owner", password="pass1234", email="owner@example.com"
        )

        response = self.client.post(reverse("verify_email"), {"code": "123456"})

        self.assertRedirects(response, reverse("redeem_access_code"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("owner@example.com", mail.outbox[0].to)
        self.assertIn(self.user.username, mail.outbox[0].body)

    def test_verifying_sends_no_email_without_superuser(self):
        response = self.client.post(reverse("verify_email"), {"code": "123456"})

        self.assertRedirects(response, reverse("redeem_access_code"))
        self.assertEqual(len(mail.outbox), 0)

    def test_wrong_code_shows_error(self):
        response = self.client.post(reverse("verify_email"), {"code": "000000"})

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"], "code", "That code is invalid or has expired."
        )
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.email_verified)

    def test_expired_code_shows_error(self):
        self.profile.verification_code_expires_at = timezone.now() - timedelta(minutes=1)
        self.profile.save()

        response = self.client.post(reverse("verify_email"), {"code": "123456"})

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"], "code", "That code is invalid or has expired."
        )

    def test_resend_issues_new_code(self):
        old_code = self.profile.verification_code

        response = self.client.post(reverse("verify_email"), {"action": "resend"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.profile.refresh_from_db()
        self.assertIn(self.profile.verification_code, mail.outbox[0].body)
        self.assertNotEqual(self.profile.verification_code, "")
        # New code should still be a 6-digit string, possibly equal by chance,
        # but expiry should always be refreshed.
        self.assertIsNotNone(self.profile.verification_code_expires_at)
        del old_code

    def test_too_many_wrong_attempts_blocks_further_guesses(self):
        for _ in range(settings.VERIFICATION_CODE_MAX_ATTEMPTS):
            self.client.post(reverse("verify_email"), {"code": "000000"})

        response = self.client.post(reverse("verify_email"), {"code": "123456"})

        self.assertFormError(
            response.context["form"], "code", "Too many incorrect attempts. Please request a new code.",
        )
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.email_verified)

    def test_resend_resets_attempt_counter(self):
        for _ in range(settings.VERIFICATION_CODE_MAX_ATTEMPTS):
            self.client.post(reverse("verify_email"), {"code": "000000"})

        self.client.post(reverse("verify_email"), {"action": "resend"})
        self.profile.refresh_from_db()

        response = self.client.post(reverse("verify_email"), {"code": self.profile.verification_code})
        self.assertRedirects(response, reverse("redeem_access_code"))


class AccessCodeRedemptionTests(TestCase):

    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.user = User.objects.create_user(
            username="pending", password="pass1234", email="pending@example.com", is_active=False
        )
        self.profile, _ = Profile.objects.get_or_create(user=self.user)
        self.profile.email_verified = True
        self.profile.save()

        session = self.client.session
        session["pending_user_id"] = self.user.id
        session.save()

    def test_valid_code_activates_user(self):
        access_code = AccessCode.objects.create(code="ABCD1234EF")

        response = self.client.post(reverse("redeem_access_code"), {"code": "ABCD1234EF"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("billing_start"))

        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)

        self.profile.refresh_from_db()
        self.assertTrue(self.profile.access_code_redeemed)

        access_code.refresh_from_db()
        self.assertTrue(access_code.is_used)
        self.assertEqual(access_code.used_by, self.user)
        self.assertIsNotNone(access_code.used_at)

        self.assertTrue(self.user.groups.filter(name="Admin").exists())
        self.assertTrue(Category.objects.filter(owner=self.user, name="General").exists())
        self.assertTrue(Subscription.objects.filter(user=self.user).exists())

        self.assertNotIn("pending_user_id", self.client.session)

    def test_already_used_code_rejected(self):
        User = get_user_model()
        other_user = User.objects.create_user(username="other", password="pass1234")
        access_code = AccessCode.objects.create(
            code="USEDCODE12", is_used=True, used_by=other_user, used_at=timezone.now()
        )

        response = self.client.post(reverse("redeem_access_code"), {"code": "USEDCODE12"})

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"], "code", "That access code is invalid, used, or expired."
        )

        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        access_code.refresh_from_db()
        self.assertEqual(access_code.used_by, other_user)

    def test_expired_code_rejected(self):
        AccessCode.objects.create(
            code="EXPIRED123", expires_at=timezone.now() - timedelta(days=1)
        )

        response = self.client.post(reverse("redeem_access_code"), {"code": "EXPIRED123"})

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"], "code", "That access code is invalid, used, or expired."
        )

    def test_unknown_code_rejected(self):
        response = self.client.post(reverse("redeem_access_code"), {"code": "NOSUCHCODE"})

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"], "code", "That access code is invalid, used, or expired."
        )

    def test_too_many_wrong_attempts_blocks_further_guesses(self):
        AccessCode.objects.create(code="REALCODE123")

        for _ in range(settings.ACCESS_CODE_MAX_ATTEMPTS):
            self.client.post(reverse("redeem_access_code"), {"code": "WRONGCODE12"})

        response = self.client.post(reverse("redeem_access_code"), {"code": "REALCODE123"})

        self.assertFormError(
            response.context["form"], "code", "Too many incorrect attempts. Please try again later.",
        )
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)


class CsvImportSecurityTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="importer", password="pass1234")
        self.account = get_or_create_account_for_user(self.user)
        self.client.force_login(self.user)
        grant(self.user, "add_book", "view_book")

    def _upload(self, content, name="books.csv"):
        return self.client.post(
            reverse("import_books_csv"),
            {"csv_file": SimpleUploadedFile(name, content.encode("utf-8"), content_type="text/csv")},
        )

    def test_oversized_file_is_rejected(self):
        header = "isbn,title,subtitle,authors,publisher,published_date,category,distribution_expense\n"
        row = "111,Test Book,,Author,Acme,2024-01-01,Fiction,10.00\n"
        big_csv = header + row * 110000
        big_bytes = big_csv.encode("utf-8")
        self.assertGreater(len(big_bytes), settings.CSV_IMPORT_MAX_SIZE_BYTES)

        response = self.client.post(
            reverse("import_books_csv"),
            {"csv_file": SimpleUploadedFile("big.csv", big_bytes, content_type="text/csv")},
            follow=True,
        )
        self.assertContains(response, "too large")
        self.assertFalse(Book.objects.filter(account=self.account, title="Test Book").exists())

    def test_invalid_date_row_is_skipped_not_fatal(self):
        csv_content = (
            "isbn,title,subtitle,authors,publisher,published_date,category,distribution_expense\n"
            "111,Bad Date Book,,Author,Acme,not-a-date,Fiction,10.00\n"
            "222,Good Book,,Author,Acme,2024-01-01,Fiction,10.00\n"
        )
        response = self._upload(csv_content)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Book.objects.filter(account=self.account, title="Bad Date Book").exists())
        self.assertTrue(Book.objects.filter(account=self.account, title="Good Book").exists())

    def test_missing_category_row_is_skipped_not_fatal(self):
        csv_content = (
            "isbn,title,subtitle,authors,publisher,published_date,category,distribution_expense\n"
            "111,No Category Book,,Author,Acme,2024-01-01,,10.00\n"
        )
        response = self._upload(csv_content)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Book.objects.filter(account=self.account, title="No Category Book").exists())

    def test_existing_book_update_does_not_require_date_or_category(self):
        cat = Category.objects.create(owner=self.user, account=self.account, name="Fiction")
        Book.objects.create(
            owner=self.user, account=self.account, title="Old Title", isbn="111",
            publisher="Acme", published_date=date(2024, 1, 1), category=cat,
            distribution_expense=Decimal("5.00"), stock_on_hand=1, reorder_threshold=1,
        )
        csv_content = (
            "isbn,title,subtitle,authors,publisher,published_date,category,distribution_expense\n"
            "111,Updated Title,,Author,Acme,,,12.00\n"
        )
        self._upload(csv_content)

        book = Book.objects.get(account=self.account, isbn="111")
        self.assertEqual(book.title, "Updated Title")
        self.assertEqual(book.category.name, "Fiction")


class CsvExportInjectionTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="exporter", password="pass1234")
        self.account = get_or_create_account_for_user(self.user)
        self.client.force_login(self.user)
        grant(self.user, "view_book")

    def test_formula_prefixed_title_is_neutralized_on_csv_export(self):
        cat = Category.objects.create(owner=self.user, account=self.account, name="Fiction")
        Book.objects.create(
            owner=self.user, account=self.account, title="=cmd|/c calc", isbn="666",
            publisher="Acme", published_date=date(2024, 1, 1), category=cat,
            distribution_expense=Decimal("5.00"), stock_on_hand=1, reorder_threshold=1,
        )

        response = self.client.get(reverse("export_books_csv"))
        content = response.content.decode()

        self.assertNotIn("\n=cmd", content)
        self.assertIn("'=cmd|/c calc", content)

    def test_safe_title_is_unaffected(self):
        cat = Category.objects.create(owner=self.user, account=self.account, name="Fiction")
        Book.objects.create(
            owner=self.user, account=self.account, title="A Normal Title", isbn="777",
            publisher="Acme", published_date=date(2024, 1, 1), category=cat,
            distribution_expense=Decimal("5.00"), stock_on_hand=1, reorder_threshold=1,
        )

        response = self.client.get(reverse("export_books_csv"))
        content = response.content.decode()

        self.assertIn("A Normal Title", content)
        self.assertNotIn("'A Normal Title", content)


class IntegrationSecretExposureTests(TestCase):
    """Integration api_key/api_secret/webhook_secret must never be rendered
    back into the edit form, since that puts them in plain text in the page's
    HTML (view-source, browser history, dev tools)."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="intg_owner", password="pass1234")
        self.account = get_or_create_account_for_user(self.user)
        self.client.force_login(self.user)
        grant(self.user, "add_integration", "change_integration", "view_integration")

        self.integration = Integration.objects.create(
            owner=self.user, account=self.account, platform=Integration.PLATFORM_STRIPE, name="My Stripe",
            api_key="sk_live_SUPERSECRET123", api_secret="whsec_ANOTHERSECRET456",
            webhook_secret="whsec_THIRDSECRET789",
        )

    def test_edit_page_does_not_render_existing_secrets(self):
        response = self.client.get(reverse("integration_update", args=[self.integration.id]))
        content = response.content.decode()

        self.assertNotIn("sk_live_SUPERSECRET123", content)
        self.assertNotIn("whsec_ANOTHERSECRET456", content)
        self.assertNotIn("whsec_THIRDSECRET789", content)

    def test_leaving_secrets_blank_keeps_existing_values(self):
        self.client.post(reverse("integration_update", args=[self.integration.id]), {
            "platform": Integration.PLATFORM_STRIPE, "name": "Renamed", "store_url": "",
            "api_key": "", "api_secret": "", "webhook_secret": "", "is_active": "on",
        })

        self.integration.refresh_from_db()
        self.assertEqual(self.integration.name, "Renamed")
        self.assertEqual(self.integration.api_key, "sk_live_SUPERSECRET123")
        self.assertEqual(self.integration.api_secret, "whsec_ANOTHERSECRET456")
        self.assertEqual(self.integration.webhook_secret, "whsec_THIRDSECRET789")

    def test_providing_a_new_secret_updates_it(self):
        self.client.post(reverse("integration_update", args=[self.integration.id]), {
            "platform": Integration.PLATFORM_STRIPE, "name": "My Stripe", "store_url": "",
            "api_key": "sk_live_NEWVALUE999", "api_secret": "", "webhook_secret": "", "is_active": "on",
        })

        self.integration.refresh_from_db()
        self.assertEqual(self.integration.api_key, "sk_live_NEWVALUE999")
        self.assertEqual(self.integration.api_secret, "whsec_ANOTHERSECRET456")

    def test_create_form_has_no_existing_value_to_leak(self):
        response = self.client.get(reverse("integration_create"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("sk_live", response.content.decode())


class IntegrationEncryptionAtRestTests(TestCase):
    """api_key/api_secret/webhook_secret must be encrypted in the actual DB
    column, not just round-trip correctly through the ORM (a no-op
    "encryption" would still pass ORM-only tests)."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="enc_owner", password="pass1234")
        self.account = get_or_create_account_for_user(self.user)

    def test_raw_db_column_is_not_plaintext(self):
        Integration.objects.create(
            owner=self.user, account=self.account, platform=Integration.PLATFORM_STRIPE, name="Stripe",
            api_key="sk_live_RAWCHECK123", api_secret="whsec_RAWCHECK456", webhook_secret="whsec_RAWCHECK789",
        )

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT api_key, api_secret, webhook_secret FROM books_integration WHERE name = %s", ["Stripe"],
            )
            raw_api_key, raw_api_secret, raw_webhook_secret = cursor.fetchone()

        self.assertNotEqual(raw_api_key, "sk_live_RAWCHECK123")
        self.assertNotEqual(raw_api_secret, "whsec_RAWCHECK456")
        self.assertNotEqual(raw_webhook_secret, "whsec_RAWCHECK789")
        self.assertNotIn("RAWCHECK", raw_api_key)

    def test_orm_read_transparently_decrypts(self):
        Integration.objects.create(
            owner=self.user, account=self.account, platform=Integration.PLATFORM_STRIPE, name="Stripe",
            api_key="sk_live_ROUNDTRIP123",
        )

        fetched = Integration.objects.get(name="Stripe")
        self.assertEqual(fetched.api_key, "sk_live_ROUNDTRIP123")

    def test_blank_secret_stays_blank_not_a_ciphertext_token(self):
        Integration.objects.create(
            owner=self.user, account=self.account, platform=Integration.PLATFORM_STRIPE, name="No Secret",
        )

        with connection.cursor() as cursor:
            cursor.execute("SELECT api_key FROM books_integration WHERE name = %s", ["No Secret"])
            raw_value = cursor.fetchone()[0]

        self.assertEqual(raw_value, "")

    def test_encrypt_value_and_decrypt_value_round_trip(self):
        ciphertext = encrypt_value("plain-text-secret")
        self.assertNotEqual(ciphertext, "plain-text-secret")
        self.assertEqual(decrypt_value(ciphertext), "plain-text-secret")

    def test_decrypt_value_returns_input_unchanged_if_not_valid_ciphertext(self):
        self.assertEqual(decrypt_value("not-actually-encrypted"), "not-actually-encrypted")

    def test_blank_value_is_not_encrypted(self):
        self.assertEqual(encrypt_value(""), "")
        self.assertEqual(decrypt_value(""), "")


class ShopifyWebhookTests(TestCase):
    """Covers HMAC verification, stock deduction, and replay protection for
    the Shopify orders/create webhook."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="shop_owner", password="pass1234")
        self.account = get_or_create_account_for_user(self.user)
        self.integration = Integration.objects.create(
            owner=self.user, account=self.account, platform=Integration.PLATFORM_SHOPIFY,
            name="My Shopify", webhook_secret="shpss_testsecret123", is_active=True,
        )
        category = Category.objects.create(owner=self.user, account=self.account, name="Fiction")
        self.book = Book.objects.create(
            owner=self.user, account=self.account, title="Race Condition Handbook",
            published_date=date(2024, 1, 1), category=category,
            isbn="9781234567890", stock_on_hand=10, distribution_expense=5,
        )

    def _post_webhook(self, payload):
        body = json.dumps(payload).encode()
        digest = base64.b64encode(
            hmac.new(b"shpss_testsecret123", body, hashlib.sha256).digest()
        ).decode()
        return self.client.post(
            reverse("shopify_webhook", args=[self.integration.id]),
            data=body, content_type="application/json",
            HTTP_X_SHOPIFY_HMAC_SHA256=digest,
        )

    def test_invalid_signature_rejected(self):
        body = json.dumps({"id": 1, "line_items": []}).encode()
        response = self.client.post(
            reverse("shopify_webhook", args=[self.integration.id]),
            data=body, content_type="application/json",
            HTTP_X_SHOPIFY_HMAC_SHA256="not-the-right-signature",
        )
        self.assertEqual(response.status_code, 401)
        self.book.refresh_from_db()
        self.assertEqual(self.book.stock_on_hand, 10)

    def test_valid_order_deducts_stock(self):
        response = self._post_webhook({
            "id": 555,
            "line_items": [{"sku": "9781234567890", "quantity": 3}],
        })
        self.assertEqual(response.status_code, 200)
        self.book.refresh_from_db()
        self.assertEqual(self.book.stock_on_hand, 7)

    def test_replayed_order_does_not_deduct_stock_twice(self):
        payload = {"id": 555, "line_items": [{"sku": "9781234567890", "quantity": 3}]}
        self._post_webhook(payload)
        self._post_webhook(payload)

        self.book.refresh_from_db()
        self.assertEqual(self.book.stock_on_hand, 7)
        self.assertEqual(
            ProcessedShopifyOrder.objects.filter(integration=self.integration, order_id="555").count(), 1,
        )

    def test_different_orders_both_processed(self):
        self._post_webhook({"id": 1, "line_items": [{"sku": "9781234567890", "quantity": 2}]})
        self._post_webhook({"id": 2, "line_items": [{"sku": "9781234567890", "quantity": 1}]})

        self.book.refresh_from_db()
        self.assertEqual(self.book.stock_on_hand, 7)


class MultiTenancyIsolationTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user_a = User.objects.create_user(username="alice", password="pass1234")
        self.user_b = User.objects.create_user(username="bob", password="pass1234")

        for user in (self.user_a, self.user_b):
            grant(
                user,
                "view_book", "add_book", "change_book", "delete_book",
                "view_category", "add_category", "change_category", "delete_category",
                "view_author",
                "view_sale", "add_sale", "change_sale", "delete_sale",
            )

        self.category_a = Category.objects.create(owner=self.user_a, name="Fiction")
        self.book_a = Book.objects.create(
            owner=self.user_a,
            title="Alice's Book",
            publisher="Acme",
            published_date=date(2024, 1, 1),
            category=self.category_a,
            distribution_expense=Decimal("10.00"),
            stock_on_hand=10,
            reorder_threshold=2,
        )
        self.author_a = Author.objects.create(owner=self.user_a, name="Alice Author")
        self.book_a.authors.add(self.author_a)
        self.sale_a = Sale.objects.create(
            owner=self.user_a,
            book=self.book_a,
            quantity=1,
            unit_price=Decimal("10.00"),
            sale_date=date(2024, 1, 5),
        )

        self.category_b = Category.objects.create(owner=self.user_b, name="Non-Fiction")
        self.book_b = Book.objects.create(
            owner=self.user_b,
            title="Bob's Book",
            publisher="Acme",
            published_date=date(2024, 1, 1),
            category=self.category_b,
            distribution_expense=Decimal("10.00"),
            stock_on_hand=10,
            reorder_threshold=2,
        )
        self.author_b = Author.objects.create(owner=self.user_b, name="Bob Author")

    def test_book_list_shows_only_own_books(self):
        self.client.force_login(self.user_a)
        response = self.client.get(reverse("stock_list"))

        books = list(response.context["books"])
        self.assertIn(self.book_a, books)
        self.assertNotIn(self.book_b, books)

    def test_dashboard_shows_only_own_data(self):
        self.client.force_login(self.user_a)
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_categories"], 1)

    def test_report_shows_only_own_sales(self):
        self.client.force_login(self.user_a)
        response = self.client.get(reverse("report"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_revenue"], Decimal("10.00"))

    def test_cross_user_book_detail_returns_404(self):
        self.client.force_login(self.user_a)
        response = self.client.get(reverse("book_detail", args=[self.book_b.id]))
        self.assertEqual(response.status_code, 404)

    def test_cross_user_book_update_returns_404(self):
        self.client.force_login(self.user_a)
        response = self.client.get(reverse("book_update", args=[self.book_b.id]))
        self.assertEqual(response.status_code, 404)

    def test_cross_user_book_delete_returns_404(self):
        self.client.force_login(self.user_a)
        response = self.client.post(reverse("book_delete", args=[self.book_b.id]))
        self.assertEqual(response.status_code, 404)

    def test_category_create_sets_owner_to_request_user(self):
        self.client.force_login(self.user_a)
        response = self.client.post(reverse("category_create"), {"name": "New Category"})

        self.assertEqual(response.status_code, 302)
        category = Category.objects.get(owner=self.user_a, name="New Category")
        self.assertEqual(category.owner, self.user_a)

    def test_book_form_category_and_author_choices_scoped_to_user(self):
        self.client.force_login(self.user_a)
        response = self.client.get(reverse("book_create"))

        form = response.context["form"]
        self.assertIn(self.category_a, form.fields["category"].queryset)
        self.assertNotIn(self.category_b, form.fields["category"].queryset)
        self.assertIn(self.author_a, form.fields["authors"].queryset)
        self.assertNotIn(self.author_b, form.fields["authors"].queryset)

    def test_sale_form_book_choices_scoped_to_user(self):
        self.client.force_login(self.user_a)
        response = self.client.get(reverse("sale_create"))

        form = response.context["form"]
        self.assertIn(self.book_a, form.fields["book"].queryset)
        self.assertNotIn(self.book_b, form.fields["book"].queryset)


class AccessCodeAdminTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_superuser(
            username="owner", password="pass1234", email="owner@example.com"
        )
        self.client.force_login(self.owner)

    def test_creating_access_code_with_recipient_emails_code(self):
        response = self.client.post(
            reverse("admin:books_accesscode_add"),
            {
                "code": "",
                "label": "",
                "recipient_email": "newuser@example.com",
                "expires_at_0": "",
                "expires_at_1": "",
            },
        )

        self.assertEqual(response.status_code, 302)

        access_code = AccessCode.objects.get(recipient_email="newuser@example.com")
        self.assertTrue(access_code.code)

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("newuser@example.com", mail.outbox[0].to)
        self.assertIn(access_code.code, mail.outbox[0].body)

    def test_creating_access_code_without_recipient_sends_no_email(self):
        response = self.client.post(
            reverse("admin:books_accesscode_add"),
            {
                "code": "",
                "label": "",
                "recipient_email": "",
                "expires_at_0": "",
                "expires_at_1": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 0)


class PendingActivationAdminTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_superuser(
            username="owner", password="pass1234", email="owner@example.com"
        )
        self.client.force_login(self.owner)

    def test_lists_only_users_pending_activation(self):
        User = get_user_model()

        verified_pending = User.objects.create_user(
            username="verified_pending", password="pass1234", email="vp@example.com", is_active=False
        )
        profile, _ = Profile.objects.get_or_create(user=verified_pending)
        profile.email_verified = True
        profile.save()

        not_verified = User.objects.create_user(
            username="not_verified", password="pass1234", email="nv@example.com", is_active=False
        )
        Profile.objects.get_or_create(user=not_verified)

        already_activated = User.objects.create_user(
            username="activated", password="pass1234", email="act@example.com"
        )
        profile, _ = Profile.objects.get_or_create(user=already_activated)
        profile.email_verified = True
        profile.access_code_redeemed = True
        profile.save()

        response = self.client.get(reverse("admin:books_pendingactivation_changelist"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "verified_pending")
        self.assertNotContains(response, "not_verified")
        self.assertNotContains(response, "activated")


class CustomerModelTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="owner", password="pass1234")
        self.other = User.objects.create_user(username="other", password="pass1234")

    def test_str_returns_name(self):
        c = Customer.objects.create(owner=self.user, name="Bookshop A")
        self.assertEqual(str(c), "Bookshop A")

    def test_name_unique_per_owner(self):
        Customer.objects.create(owner=self.user, name="Bookshop A")
        with self.assertRaises(Exception):
            Customer.objects.create(owner=self.user, name="Bookshop A")

    def test_same_name_allowed_for_different_owners(self):
        Customer.objects.create(owner=self.user, name="Bookshop A")
        c2 = Customer.objects.create(owner=self.other, name="Bookshop A")
        self.assertEqual(c2.owner, self.other)

    def test_default_ordering_alphabetical(self):
        Customer.objects.create(owner=self.user, name="Zara")
        Customer.objects.create(owner=self.user, name="Alpha")
        names = list(Customer.objects.filter(owner=self.user).values_list("name", flat=True))
        self.assertEqual(names, ["Alpha", "Zara"])


class CustomerViewTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="owner", password="pass1234")
        self.other = User.objects.create_user(username="other", password="pass1234")
        self.client.force_login(self.user)
        grant(self.user, "view_customer", "add_customer", "change_customer", "delete_customer", "change_invoice")
        self.customer = Customer.objects.create(owner=self.user, name="Test Shop", email="shop@example.com")

    def test_list_shows_own_customers_only(self):
        Customer.objects.create(owner=self.other, name="Other Shop")
        response = self.client.get(reverse("customer_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Shop")
        self.assertNotContains(response, "Other Shop")

    def test_create_customer(self):
        response = self.client.post(reverse("customer_create"), {
            "name": "New Shop",
            "email": "new@example.com",
            "phone": "",
            "address": "",
            "notes": "",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Customer.objects.filter(owner=self.user, name="New Shop").exists())

    def test_update_customer(self):
        response = self.client.post(reverse("customer_update", args=[self.customer.id]), {
            "name": "Renamed Shop",
            "email": "shop@example.com",
            "phone": "",
            "address": "",
            "notes": "",
        })
        self.assertEqual(response.status_code, 302)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.name, "Renamed Shop")

    def test_delete_customer(self):
        response = self.client.post(reverse("customer_delete", args=[self.customer.id]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Customer.objects.filter(id=self.customer.id).exists())

    def test_cannot_edit_other_owners_customer(self):
        other_customer = Customer.objects.create(owner=self.other, name="Other Shop")
        response = self.client.post(reverse("customer_update", args=[other_customer.id]), {
            "name": "Hacked",
            "email": "",
            "phone": "",
            "address": "",
            "notes": "",
        })
        self.assertEqual(response.status_code, 404)

    def test_list_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("customer_list"))
        self.assertEqual(response.status_code, 302)

    def test_detail_shows_no_portal_login_when_never_logged_in(self):
        response = self.client.get(reverse("customer_detail", args=[self.customer.id]))
        self.assertEqual(response.context["last_portal_login"], None)

    def test_detail_shows_most_recent_portal_login(self):
        older = timezone.now() - timedelta(days=2)
        newer = timezone.now() - timedelta(hours=1)
        CustomerLoginToken.objects.create(
            customer=self.customer, token="old", expires_at=older + timedelta(minutes=30), used_at=older,
        )
        CustomerLoginToken.objects.create(
            customer=self.customer, token="new", expires_at=newer + timedelta(minutes=30), used_at=newer,
        )

        response = self.client.get(reverse("customer_detail", args=[self.customer.id]))
        self.assertEqual(response.context["last_portal_login"], newer)

    def test_detail_ignores_other_customers_logins(self):
        other_customer = Customer.objects.create(owner=self.user, name="Other Customer", email="o@example.com")
        CustomerLoginToken.objects.create(
            customer=other_customer, token="other-token",
            expires_at=timezone.now() + timedelta(minutes=30), used_at=timezone.now(),
        )

        response = self.client.get(reverse("customer_detail", args=[self.customer.id]))
        self.assertEqual(response.context["last_portal_login"], None)

    def test_detail_shows_mark_sent_action_for_draft_invoice(self):
        invoice = Invoice.objects.create(
            owner=self.user, customer=self.customer, customer_name=self.customer.name,
            invoice_date=date.today(), currency="USD", status=Invoice.STATUS_DRAFT,
        )

        response = self.client.get(reverse("customer_detail", args=[self.customer.id]))
        self.assertContains(response, "Mark Sent")

        self.client.post(reverse("invoice_update_status", args=[invoice.id, "sent"]), {
            "next": reverse("customer_detail", args=[self.customer.id]),
        })
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.STATUS_SENT)


class InvoiceModelTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="owner", password="pass1234")
        self.invoice = Invoice.objects.create(
            owner=self.user,
            customer_name="Test Client",
            invoice_date=date.today(),
            currency="USD",
        )

    def _add_item(self, qty, price, tax_rate=0):
        return InvoiceItem.objects.create(
            invoice=self.invoice,
            description="Book",
            quantity=qty,
            unit_price=Decimal(str(price)),
            tax_rate=Decimal(str(tax_rate)),
        )

    def test_subtotal_sums_qty_times_price(self):
        self._add_item(2, "10.00")
        self._add_item(3, "5.00")
        self.assertEqual(self.invoice.subtotal, Decimal("35.00"))

    def test_grand_total_includes_tax(self):
        self._add_item(1, "100.00", tax_rate="10")
        self.assertEqual(self.invoice.grand_total, Decimal("110.00"))

    def test_grand_total_zero_with_no_items(self):
        self.assertEqual(self.invoice.grand_total, Decimal("0"))


class InvoiceSentNotificationTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="owner", password="pass1234")
        self.client.force_login(self.user)
        grant(self.user, "view_invoice", "change_invoice")

    def test_mark_sent_includes_portal_link_when_customer_linked(self):
        customer = Customer.objects.create(owner=self.user, name="Acme Shop", email="acme@example.com")
        invoice = Invoice.objects.create(
            owner=self.user, customer=customer, customer_name=customer.name,
            customer_email="acme@example.com", invoice_date=date.today(),
            currency="USD", status=Invoice.STATUS_DRAFT,
        )

        self.client.post(reverse("invoice_update_status", args=[invoice.id, "sent"]))

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(reverse("customer_portal_login"), mail.outbox[0].body)

    def test_mark_sent_omits_portal_link_without_linked_customer(self):
        invoice = Invoice.objects.create(
            owner=self.user, customer_name="Walk-in", customer_email="walkin@example.com",
            invoice_date=date.today(), currency="USD", status=Invoice.STATUS_DRAFT,
        )

        self.client.post(reverse("invoice_update_status", args=[invoice.id, "sent"]))

        self.assertEqual(len(mail.outbox), 1)
        self.assertNotIn("portal", mail.outbox[0].body.lower())

    def test_mark_sent_email_body_intact_without_note(self):
        invoice = Invoice.objects.create(
            owner=self.user, customer_name="No Note Co", customer_email="nonote@example.com",
            invoice_date=date.today(), due_date=date.today(), currency="USD",
            status=Invoice.STATUS_DRAFT, note="",
        )

        self.client.post(reverse("invoice_update_status", args=[invoice.id, "sent"]))

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Dear No Note Co", mail.outbox[0].body)
        self.assertIn("Amount due", mail.outbox[0].body)
        self.assertIn("Due date", mail.outbox[0].body)


class InvoiceBulkUpdateTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="owner", password="pass1234")
        self.other_user = User.objects.create_user(username="other", password="pass1234")
        self.client.force_login(self.user)
        grant(self.user, "view_invoice", "change_invoice")

        self.draft = Invoice.objects.create(
            owner=self.user, customer_name="A", invoice_date=date.today(),
            currency="USD", status=Invoice.STATUS_DRAFT,
        )
        self.sent = Invoice.objects.create(
            owner=self.user, customer_name="B", invoice_date=date.today(),
            currency="USD", status=Invoice.STATUS_SENT,
        )
        self.others_invoice = Invoice.objects.create(
            owner=self.other_user, customer_name="C", invoice_date=date.today(),
            currency="USD", status=Invoice.STATUS_DRAFT,
        )

    def test_bulk_mark_sent_updates_only_matching_drafts(self):
        response = self.client.post(reverse("invoice_bulk_update"), {
            "action": "sent",
            "ids": [self.draft.id, self.sent.id],
            "next": reverse("invoice_list"),
        })
        self.assertRedirects(response, reverse("invoice_list"))
        self.draft.refresh_from_db()
        self.sent.refresh_from_db()
        self.assertEqual(self.draft.status, Invoice.STATUS_SENT)
        self.assertEqual(self.sent.status, Invoice.STATUS_SENT)

    def test_bulk_update_ignores_other_owners_invoices(self):
        self.client.post(reverse("invoice_bulk_update"), {
            "action": "sent",
            "ids": [self.others_invoice.id],
            "next": reverse("invoice_list"),
        })
        self.others_invoice.refresh_from_db()
        self.assertEqual(self.others_invoice.status, Invoice.STATUS_DRAFT)

    def test_bulk_update_requires_action_and_ids(self):
        response = self.client.post(reverse("invoice_bulk_update"), {
            "next": reverse("invoice_list"),
        })
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, Invoice.STATUS_DRAFT)
        self.assertRedirects(response, reverse("invoice_list"))


class InvoiceNextRedirectSafetyTests(TestCase):
    """A malicious `next` value must never redirect off-site (open redirect)."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="owner", password="pass1234")
        self.client.force_login(self.user)
        grant(self.user, "view_invoice", "change_invoice")

        self.invoice = Invoice.objects.create(
            owner=self.user, customer_name="A", invoice_date=date.today(),
            currency="USD", status=Invoice.STATUS_DRAFT,
        )

    def test_update_status_follows_safe_relative_next(self):
        response = self.client.post(
            reverse("invoice_update_status", args=[self.invoice.id, "sent"]),
            {"next": reverse("invoice_list")},
        )
        self.assertRedirects(response, reverse("invoice_list"))

    def test_update_status_rejects_protocol_relative_next(self):
        response = self.client.post(
            reverse("invoice_update_status", args=[self.invoice.id, "sent"]),
            {"next": "//evil.example.com/phish"},
        )
        self.assertRedirects(response, reverse("invoice_detail", args=[self.invoice.id]))

    def test_update_status_rejects_absolute_external_next(self):
        response = self.client.post(
            reverse("invoice_update_status", args=[self.invoice.id, "sent"]),
            {"next": "https://evil.example.com/phish"},
        )
        self.assertRedirects(response, reverse("invoice_detail", args=[self.invoice.id]))

    def test_bulk_update_rejects_protocol_relative_next(self):
        response = self.client.post(reverse("invoice_bulk_update"), {
            "action": "sent",
            "ids": [self.invoice.id],
            "next": "//evil.example.com/phish",
        })
        self.assertRedirects(response, reverse("invoice_list"))


class LoginThrottlingTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="throttled", password="correctpass123")

    def _bad_login(self):
        return self.client.post(reverse("login"), {"username": "throttled", "password": "wrongpass"})

    def _good_login(self):
        return self.client.post(reverse("login"), {
            "username": "throttled", "password": "correctpass123",
        })

    def test_correct_password_works_before_lockout(self):
        response = self._good_login()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.client.session.get("_auth_user_id"))

    def test_locked_out_after_failure_limit(self):
        for _ in range(5):
            self._bad_login()

        response = self._good_login()
        self.assertEqual(response.status_code, 429)

    def test_successful_login_resets_failure_count(self):
        for _ in range(4):
            self._bad_login()

        self._good_login()
        self.client.logout()

        self._bad_login()
        response = self._good_login()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.client.session.get("_auth_user_id"))


class AvatarSizeValidationTests(TestCase):

    def test_file_under_limit_passes(self):
        validate_avatar_size(SimpleNamespace(size=1024 * 1024))

    def test_file_over_limit_raises(self):
        with self.assertRaises(ValidationError):
            validate_avatar_size(SimpleNamespace(size=3 * 1024 * 1024))

    def test_file_at_exact_limit_passes(self):
        validate_avatar_size(SimpleNamespace(size=AVATAR_MAX_SIZE_BYTES))


class LogoSizeAndHexColorValidationTests(TestCase):

    def test_file_under_limit_passes(self):
        validate_logo_size(SimpleNamespace(size=1024 * 1024))

    def test_file_over_limit_raises(self):
        with self.assertRaises(ValidationError):
            validate_logo_size(SimpleNamespace(size=3 * 1024 * 1024))

    def test_file_at_exact_limit_passes(self):
        validate_logo_size(SimpleNamespace(size=LOGO_MAX_SIZE_BYTES))

    def test_valid_hex_color_passes(self):
        validate_hex_color("#1f1f1f")

    def test_hex_color_without_hash_rejected(self):
        with self.assertRaises(ValidationError):
            validate_hex_color("1f1f1f")

    def test_short_hex_color_rejected(self):
        with self.assertRaises(ValidationError):
            validate_hex_color("#fff")

    def test_non_hex_characters_rejected(self):
        with self.assertRaises(ValidationError):
            validate_hex_color("#zzzzzz")


class ProfileUpdateTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="owner", password="pass1234", email="old@example.com",
        )
        self.client.force_login(self.user)

    def test_update_email(self):
        response = self.client.post(reverse("profile_update"), {
            "action": "email",
            "email": "new@example.com",
        })
        self.assertRedirects(response, reverse("profile_update"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "new@example.com")

    def test_update_email_rejects_invalid_address(self):
        response = self.client.post(reverse("profile_update"), {
            "action": "email",
            "email": "not-an-email",
        })
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "old@example.com")

    def test_change_password(self):
        response = self.client.post(reverse("profile_update"), {
            "action": "password",
            "old_password": "pass1234",
            "new_password1": "NewPass5678!",
            "new_password2": "NewPass5678!",
        })
        self.assertRedirects(response, reverse("profile_update"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NewPass5678!"))

    def test_change_password_rejects_wrong_old_password(self):
        response = self.client.post(reverse("profile_update"), {
            "action": "password",
            "old_password": "wrongpass",
            "new_password1": "NewPass5678!",
            "new_password2": "NewPass5678!",
        })
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("pass1234"))

    def test_change_password_keeps_session_authenticated(self):
        grant(self.user, "view_book")
        self.client.post(reverse("profile_update"), {
            "action": "password",
            "old_password": "pass1234",
            "new_password1": "NewPass5678!",
            "new_password2": "NewPass5678!",
        })
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)


class ReorderListFilterTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="owner", password="pass1234")
        self.client.force_login(self.user)
        grant(self.user, "view_reorder")

        cat = Category.objects.create(owner=self.user, name="Fiction")
        self.book = Book.objects.create(
            owner=self.user, title="Test Book", publisher="Acme",
            published_date=date(2024, 1, 1), category=cat,
            stock_on_hand=10, reorder_threshold=5, distribution_expense=Decimal("2.00"),
        )
        self.supplier_a = Supplier.objects.create(owner=self.user, name="Supplier A")
        self.supplier_b = Supplier.objects.create(owner=self.user, name="Supplier B")

        self.reorder_a = Reorder.objects.create(
            owner=self.user, book=self.book, supplier=self.supplier_a,
            quantity=10, unit_cost=Decimal("1.00"), status=Reorder.STATUS_PENDING,
        )
        self.reorder_b = Reorder.objects.create(
            owner=self.user, book=self.book, supplier=self.supplier_b,
            quantity=20, unit_cost=Decimal("1.00"), status=Reorder.STATUS_ORDERED,
        )
        Reorder.objects.filter(id=self.reorder_a.id).update(
            created_at=timezone.now() - timedelta(days=10)
        )
        Reorder.objects.filter(id=self.reorder_b.id).update(
            created_at=timezone.now() - timedelta(days=1)
        )

    def test_filter_by_supplier(self):
        response = self.client.get(reverse("reorder_list"), {"supplier": self.supplier_a.id})
        reorders = list(response.context["reorders"])
        self.assertEqual(reorders, [self.reorder_a])

    def test_filter_by_status(self):
        response = self.client.get(reverse("reorder_list"), {"status": Reorder.STATUS_ORDERED})
        reorders = list(response.context["reorders"])
        self.assertEqual(reorders, [self.reorder_b])

    def test_filter_by_date_range(self):
        start = (timezone.now() - timedelta(days=2)).date().isoformat()
        response = self.client.get(reverse("reorder_list"), {"start_date": start})
        reorders = list(response.context["reorders"])
        self.assertEqual(reorders, [self.reorder_b])

    def test_combined_filters(self):
        response = self.client.get(reverse("reorder_list"), {
            "supplier": self.supplier_a.id,
            "status": Reorder.STATUS_PENDING,
        })
        reorders = list(response.context["reorders"])
        self.assertEqual(reorders, [self.reorder_a])

    def test_no_filters_returns_all(self):
        response = self.client.get(reverse("reorder_list"))
        self.assertEqual(len(response.context["reorders"]), 2)


class ArabicPdfFontTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="owner", password="pass1234")
        self.client.force_login(self.user)
        grant(self.user, "view_invoice")
        self.invoice = Invoice.objects.create(
            owner=self.user, customer_name="عميل", invoice_date=date.today(), currency="USD",
        )

    def _get_invoice_pdf_in_arabic(self):
        # LocaleMiddleware picks the active language per-request from
        # Accept-Language (no session/cookie override here), so
        # translation.override() alone wouldn't reach the view - it gets
        # overwritten by the middleware before the view runs.
        return self.client.get(reverse("invoice_pdf", args=[self.invoice.id]), HTTP_ACCEPT_LANGUAGE="ar")

    def test_arabic_invoice_pdf_renders_without_error(self):
        response = self._get_invoice_pdf_in_arabic()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_arabic_invoice_pdf_title_uses_arabic_font(self):
        response = self._get_invoice_pdf_in_arabic()
        self.assertIn(b"NotoSansArabic", response.content)


class InvoicePdfBrandingTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="brand_owner", password="pass1234")
        self.account = get_or_create_account_for_user(self.user)
        self.client.force_login(self.user)
        grant(self.user, "view_invoice")
        self.invoice = Invoice.objects.create(
            owner=self.user, account=self.account,
            customer_name="Jane Doe", invoice_date=date.today(), currency="USD",
        )

    def test_invoice_pdf_renders_without_logo_or_color(self):
        response = self.client.get(reverse("invoice_pdf", args=[self.invoice.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_invoice_pdf_renders_with_custom_logo_and_brand_color(self):
        from io import BytesIO
        from PIL import Image as PILImage
        from django.core.files.uploadedfile import SimpleUploadedFile

        buf = BytesIO()
        PILImage.new("RGB", (10, 10), color=(200, 50, 50)).save(buf, format="PNG")
        buf.seek(0)
        self.account.logo = SimpleUploadedFile("logo.png", buf.read(), content_type="image/png")
        self.account.brand_color = "#a30000"
        self.account.save()

        response = self.client.get(reverse("invoice_pdf", args=[self.invoice.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))


class SafeJsonTests(TestCase):

    def test_escapes_script_close_tag(self):
        payload = "</script><script>alert(1)</script>"
        self.assertNotIn("</script>", _safe_json(payload))
        self.assertIn("<\\/script>", _safe_json(payload))

    def test_round_trips_through_json(self):
        self.assertEqual(json.loads(_safe_json(["a", "b"])), ["a", "b"])

    def test_dashboard_escapes_malicious_category_name(self):
        User = get_user_model()
        user = User.objects.create_user(username="owner", password="pass1234")
        self.client.force_login(user)
        grant(user, "view_book")

        category = Category.objects.create(owner=user, name="</script><script>alert(1)</script>")
        Book.objects.create(
            owner=user, title="Book", publisher="Acme", published_date=date(2024, 1, 1),
            category=category, stock_on_hand=5, reorder_threshold=1, distribution_expense=Decimal("1.00"),
        )
        Sale.objects.create(
            owner=user, book=Book.objects.filter(owner=user).first(), quantity=1,
            unit_price=Decimal("10.00"), sale_date=date.today(),
        )

        response = self.client.get(reverse("dashboard"))
        content = response.content.decode()
        self.assertNotIn("</script><script>alert(1)</script>", content)


class AdjustStockTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="owner", password="pass1234")
        self.account = get_or_create_account_for_user(self.user)
        cat = Category.objects.create(owner=self.user, name="Fiction")
        self.book = Book.objects.create(
            owner=self.user, title="Test Book", publisher="Acme",
            published_date=date(2024, 1, 1), category=cat,
            stock_on_hand=10, reorder_threshold=5, distribution_expense=Decimal("2.00"),
        )

    def test_first_positive_adjustment_adds_to_manually_set_stock(self):
        book = _adjust_stock(self.book.id, 50, self.user, self.account)
        self.assertEqual(book.stock_on_hand, 60)

    def test_first_negative_adjustment_subtracts_from_manually_set_stock(self):
        book = _adjust_stock(self.book.id, -3, self.user, self.account)
        self.assertEqual(book.stock_on_hand, 7)

    def test_second_location_does_not_double_count_existing_stock(self):
        other_location = Location.objects.create(owner=self.user, name="Warehouse 2")

        _adjust_stock(self.book.id, 50, self.user, self.account)
        book = _adjust_stock(self.book.id, 5, self.user, self.account, location=other_location)

        self.assertEqual(book.stock_on_hand, 65)


class PrintRunCompleteTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="owner", password="pass1234")
        self.client.force_login(self.user)
        grant(self.user, "view_book", "view_printrun", "add_printrun", "change_printrun", "delete_printrun")

        cat = Category.objects.create(owner=self.user, name="Fiction")
        self.book = Book.objects.create(
            owner=self.user,
            title="Test Book",
            publisher="Acme",
            published_date=date(2024, 1, 1),
            category=cat,
            stock_on_hand=0,
            reorder_threshold=5,
            distribution_expense=Decimal("2.00"),
        )
        self.run = PrintRun.objects.create(
            owner=self.user,
            book=self.book,
            quantity=50,
            cost_per_unit=Decimal("3.00"),
            run_date=date.today(),
        )

    def test_mark_complete_increases_stock_and_records_adjustment(self):
        response = self.client.post(reverse("print_run_complete", args=[self.run.id]))
        self.assertRedirects(response, reverse("print_run_list"))

        self.run.refresh_from_db()
        self.book.refresh_from_db()
        self.assertEqual(self.run.status, PrintRun.STATUS_COMPLETED)
        self.assertIsNotNone(self.run.completed_at)
        self.assertEqual(self.book.stock_on_hand, 50)

        adjustment = StockAdjustment.objects.get(book=self.book)
        self.assertEqual(adjustment.change, 50)
        self.assertEqual(adjustment.resulting_stock, 50)
        self.assertEqual(adjustment.reason, StockAdjustment.REASON_PRODUCTION)

    def test_mark_complete_twice_is_rejected(self):
        self.client.post(reverse("print_run_complete", args=[self.run.id]))
        self.client.post(reverse("print_run_complete", args=[self.run.id]))

        self.book.refresh_from_db()
        self.assertEqual(self.book.stock_on_hand, 50)
        self.assertEqual(StockAdjustment.objects.filter(book=self.book).count(), 1)

    def test_book_detail_shows_new_print_run_link_with_permission(self):
        response = self.client.get(reverse("book_detail", args=[self.book.id]))
        self.assertContains(response, reverse("print_run_create", args=[self.book.id]))

    def test_book_detail_hides_new_print_run_link_without_permission(self):
        other = get_user_model().objects.create_user(username="nopower_pr", password="pass1234")
        account = get_or_create_account_for_user(self.user)
        AccountMembership.objects.create(account=account, user=other, role=AccountMembership.ROLE_VIEWER)
        sync_user_groups_for_role(other, AccountMembership.ROLE_VIEWER)
        self.client.force_login(other)

        response = self.client.get(reverse("book_detail", args=[self.book.id]))
        self.assertNotContains(response, reverse("print_run_create", args=[self.book.id]))

    def test_completed_print_run_cannot_be_deleted(self):
        self.client.post(reverse("print_run_complete", args=[self.run.id]))
        response = self.client.post(reverse("print_run_delete", args=[self.run.id]))

        self.assertRedirects(response, reverse("print_run_list"))
        self.assertTrue(PrintRun.objects.filter(id=self.run.id).exists())

    def test_pending_print_run_can_be_deleted(self):
        response = self.client.post(reverse("print_run_delete", args=[self.run.id]))

        self.assertRedirects(response, reverse("print_run_list"))
        self.assertFalse(PrintRun.objects.filter(id=self.run.id).exists())


class GlobalSearchTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="owner", password="pass1234")
        self.other_user = User.objects.create_user(username="other", password="pass1234")
        self.client.force_login(self.user)
        grant(self.user, "view_book", "view_customer", "view_invoice")

        cat = Category.objects.create(owner=self.user, name="Fiction")
        self.book = Book.objects.create(
            owner=self.user, title="The Great Gatsby", publisher="Acme",
            published_date=date(2024, 1, 1), category=cat,
            stock_on_hand=5, reorder_threshold=1, distribution_expense=Decimal("1.00"),
        )
        self.customer = Customer.objects.create(owner=self.user, name="Gatsby Reader", email="reader@example.com")
        self.invoice = Invoice.objects.create(
            owner=self.user, customer_name="Gatsby Reader", invoice_date=date.today(),
            currency="USD", invoice_number="INV-GATSBY-01",
        )
        Book.objects.create(
            owner=self.other_user, title="Other Owner Gatsby Book", publisher="X",
            published_date=date(2024, 1, 1), category=Category.objects.create(owner=self.other_user, name="X"),
            stock_on_hand=1, reorder_threshold=1, distribution_expense=Decimal("1.00"),
        )

    def test_search_finds_matches_across_types(self):
        response = self.client.get(reverse("global_search"), {"q": "gatsby"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The Great Gatsby")
        self.assertContains(response, "Gatsby Reader")
        self.assertContains(response, "INV-GATSBY-01")

    def test_search_excludes_other_owners_data(self):
        response = self.client.get(reverse("global_search"), {"q": "gatsby"})
        self.assertNotContains(response, "Other Owner Gatsby Book")

    def test_empty_query_returns_no_results(self):
        response = self.client.get(reverse("global_search"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["has_results"])

    def test_search_respects_missing_permissions(self):
        from django.contrib.auth.models import Permission
        self.user.user_permissions.remove(
            *Permission.objects.filter(content_type__app_label="books", codename="view_invoice")
        )
        response = self.client.get(reverse("global_search"), {"q": "gatsby"})
        self.assertNotContains(response, "INV-GATSBY-01")
        self.assertContains(response, "The Great Gatsby")


class InvoiceAgingReportTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="owner", password="pass1234")
        self.account = get_or_create_account_for_user(self.user)
        self.client.force_login(self.user)
        grant(self.user, "view_invoice")
        self.today = timezone.now().date()

    def _invoice(self, due_date, status=Invoice.STATUS_SENT, amount="100.00"):
        invoice = Invoice.objects.create(
            owner=self.user,
            customer_name="Client",
            invoice_date=self.today,
            due_date=due_date,
            currency="USD",
            status=status,
        )
        InvoiceItem.objects.create(
            invoice=invoice, description="Book", quantity=1, unit_price=Decimal(amount),
        )
        return invoice

    def test_not_yet_due_invoice_in_current_bucket(self):
        self._invoice(due_date=self.today + timedelta(days=10))
        buckets, grand_total = _invoice_aging_data(self.account)
        self.assertEqual(len(buckets["current"]["invoices"]), 1)
        self.assertEqual(grand_total, Decimal("100.00"))

    def test_overdue_invoice_buckets_by_days_late(self):
        self._invoice(due_date=self.today - timedelta(days=10))
        self._invoice(due_date=self.today - timedelta(days=45))
        self._invoice(due_date=self.today - timedelta(days=90))

        buckets, grand_total = _invoice_aging_data(self.account)
        self.assertEqual(len(buckets["0_30"]["invoices"]), 1)
        self.assertEqual(len(buckets["31_60"]["invoices"]), 1)
        self.assertEqual(len(buckets["60_plus"]["invoices"]), 1)
        self.assertEqual(grand_total, Decimal("300.00"))

    def test_paid_invoices_excluded(self):
        self._invoice(due_date=self.today - timedelta(days=10), status=Invoice.STATUS_PAID)
        buckets, grand_total = _invoice_aging_data(self.account)
        self.assertEqual(grand_total, Decimal("0"))

    def test_invoice_without_due_date_is_current(self):
        self._invoice(due_date=None)
        buckets, grand_total = _invoice_aging_data(self.account)
        self.assertEqual(len(buckets["current"]["invoices"]), 1)

    def test_report_view_accessible(self):
        response = self.client.get(reverse("invoice_aging_report"))
        self.assertEqual(response.status_code, 200)


class ProfitLossReportTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="owner", password="pass1234")
        self.account = get_or_create_account_for_user(self.user)
        self.client.force_login(self.user)
        grant(self.user, "view_book")
        cat = Category.objects.create(owner=self.user, name="Fiction")
        self.book = Book.objects.create(
            owner=self.user,
            title="Test Book",
            publisher="Acme",
            published_date=date(2024, 1, 1),
            category=cat,
            stock_on_hand=50,
            reorder_threshold=5,
            distribution_expense=Decimal("2.00"),
        )

    def test_no_sales_returns_empty_rows(self):
        rows, totals = _pl_data(self.account, None, None)
        self.assertEqual(rows, [])
        self.assertEqual(totals["net_profit"], Decimal("0"))

    def test_revenue_calculated_correctly(self):
        Sale.objects.create(
            owner=self.user,
            book=self.book,
            quantity=10,
            unit_price=Decimal("15.00"),
            sale_date=date(2024, 1, 15),
            channel="online",
        )
        rows, totals = _pl_data(self.account, None, None)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["revenue"], Decimal("150.00"))
        self.assertEqual(rows[0]["units"], 10)

    def test_date_filter_excludes_out_of_range_sales(self):
        Sale.objects.create(
            owner=self.user,
            book=self.book,
            quantity=5,
            unit_price=Decimal("10.00"),
            sale_date=date(2023, 12, 1),
            channel="online",
        )
        rows, totals = _pl_data(self.account, "2024-01-01", "2024-12-31")
        self.assertEqual(rows, [])

    def test_report_view_accessible(self):
        response = self.client.get(reverse("profit_loss_report"))
        self.assertEqual(response.status_code, 200)

    def test_isolation_excludes_other_user_sales(self):
        User = get_user_model()
        other = User.objects.create_user(username="other", password="pass1234")
        cat2 = Category.objects.create(owner=other, name="Fiction")
        book2 = Book.objects.create(
            owner=other, title="Other Book", publisher="Acme",
            published_date=date(2024, 1, 1), category=cat2,
            stock_on_hand=10, reorder_threshold=2,
            distribution_expense=Decimal("0.00"),
        )
        Sale.objects.create(
            owner=other, book=book2, quantity=5,
            unit_price=Decimal("20.00"), sale_date=date.today(), channel="online",
        )
        rows, totals = _pl_data(self.account, None, None)
        self.assertEqual(rows, [])


class LowStockDigestTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="owner", password="pass1234", email="owner@example.com"
        )
        cat = Category.objects.create(owner=self.user, name="Fiction")
        self.low_book = Book.objects.create(
            owner=self.user,
            title="Low Stock Book",
            publisher="Acme",
            published_date=date(2024, 1, 1),
            category=cat,
            stock_on_hand=1,
            reorder_threshold=5,
            distribution_expense=Decimal("0.00"),
            low_stock_alert_sent=False,
        )
        self.ok_book = Book.objects.create(
            owner=self.user,
            title="Plenty Book",
            publisher="Acme",
            published_date=date(2024, 1, 1),
            category=cat,
            stock_on_hand=50,
            reorder_threshold=5,
            distribution_expense=Decimal("0.00"),
            low_stock_alert_sent=False,
        )

    def test_sends_digest_email_for_low_stock_books(self):
        call_command("send_low_stock_digest")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("owner@example.com", mail.outbox[0].to)
        self.assertIn("Low Stock Book", mail.outbox[0].body)
        self.assertNotIn("Plenty Book", mail.outbox[0].body)

    def test_marks_books_as_alerted_after_send(self):
        call_command("send_low_stock_digest")
        self.low_book.refresh_from_db()
        self.assertTrue(self.low_book.low_stock_alert_sent)

    def test_skips_already_alerted_books(self):
        self.low_book.low_stock_alert_sent = True
        self.low_book.save()
        call_command("send_low_stock_digest")
        self.assertEqual(len(mail.outbox), 0)

    def test_dry_run_sends_no_email_and_does_not_flag(self):
        call_command("send_low_stock_digest", "--dry-run")
        self.assertEqual(len(mail.outbox), 0)
        self.low_book.refresh_from_db()
        self.assertFalse(self.low_book.low_stock_alert_sent)

    def test_no_email_sent_when_no_low_stock(self):
        self.low_book.stock_on_hand = 99
        self.low_book.save()
        call_command("send_low_stock_digest")
        self.assertEqual(len(mail.outbox), 0)


class RoyaltyPaymentViewTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="owner", password="pass1234")
        self.other = User.objects.create_user(username="other", password="pass1234")
        self.client.force_login(self.user)
        grant(
            self.user,
            "view_royaltypayment", "add_royaltypayment",
            "change_royaltypayment", "delete_royaltypayment",
            "view_royaltyrate",
        )
        self.author = Author.objects.create(owner=self.user, name="Jane Doe")
        self.other_author = Author.objects.create(owner=self.other, name="John Roe")
        self.payment = RoyaltyPayment.objects.create(
            owner=self.user,
            author=self.author,
            amount=Decimal("100.00"),
            currency="USD",
            payment_date=date(2024, 1, 1),
        )

    def test_list_shows_own_payments_only(self):
        RoyaltyPayment.objects.create(
            owner=self.other,
            author=self.other_author,
            amount=Decimal("50.00"),
            currency="USD",
            payment_date=date(2024, 1, 1),
        )
        response = self.client.get(reverse("royalty_payment_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Jane Doe")
        self.assertNotContains(response, "John Roe")

    def test_create_payment(self):
        response = self.client.post(reverse("royalty_payment_create"), {
            "author": self.author.id,
            "amount": "75.50",
            "currency": "USD",
            "payment_date": "2024-02-01",
            "note": "",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            RoyaltyPayment.objects.filter(owner=self.user, amount=Decimal("75.50")).exists()
        )

    def test_create_payment_author_choices_scoped_to_owner(self):
        response = self.client.post(reverse("royalty_payment_create"), {
            "author": self.other_author.id,
            "amount": "10.00",
            "currency": "USD",
            "payment_date": "2024-02-01",
            "note": "",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(RoyaltyPayment.objects.filter(amount=Decimal("10.00")).exists())

    def test_delete_payment(self):
        response = self.client.post(reverse("royalty_payment_delete", args=[self.payment.id]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(RoyaltyPayment.objects.filter(id=self.payment.id).exists())

    def test_cannot_delete_other_owners_payment(self):
        other_payment = RoyaltyPayment.objects.create(
            owner=self.other,
            author=self.other_author,
            amount=Decimal("50.00"),
            currency="USD",
            payment_date=date(2024, 1, 1),
        )
        response = self.client.post(reverse("royalty_payment_delete", args=[other_payment.id]))
        self.assertEqual(response.status_code, 404)

    def test_list_requires_permission(self):
        self.client.force_login(self.other)
        response = self.client.get(reverse("royalty_payment_list"))
        self.assertEqual(response.status_code, 403)

    def test_report_includes_payment_totals(self):
        response = self.client.get(reverse("royalty_report"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Jane Doe")
        self.assertContains(response, "100 USD")


class CustomerPortalTests(TestCase):

    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.owner = User.objects.create_user(username="owner", password="pass1234")
        self.customer = Customer.objects.create(
            owner=self.owner, name="Acme Shop", email="acme@example.com",
        )
        self.other_customer = Customer.objects.create(
            owner=self.owner, name="Other Shop", email="other@example.com",
        )
        self.invoice = Invoice.objects.create(
            owner=self.owner,
            customer=self.customer,
            customer_name=self.customer.name,
            invoice_date=date(2024, 1, 1),
            currency="USD",
            status=Invoice.STATUS_SENT,
        )
        InvoiceItem.objects.create(
            invoice=self.invoice, description="Book", quantity=2, unit_price=Decimal("10.00"),
        )
        self.other_invoice = Invoice.objects.create(
            owner=self.owner,
            customer=self.other_customer,
            customer_name=self.other_customer.name,
            invoice_date=date(2024, 1, 1),
            currency="USD",
            status=Invoice.STATUS_SENT,
        )

    def _request_login_link(self, email):
        return self.client.post(reverse("customer_portal_login"), {"email": email})

    def test_login_request_sends_email_and_creates_token(self):
        response = self._request_login_link("acme@example.com")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("acme@example.com", mail.outbox[0].to)
        self.assertEqual(CustomerLoginToken.objects.filter(customer=self.customer).count(), 1)

    def test_login_request_for_unknown_email_sends_nothing_but_no_error(self):
        response = self._request_login_link("nobody@example.com")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

    def test_repeated_requests_for_same_email_are_cooled_down(self):
        self._request_login_link("acme@example.com")
        self._request_login_link("acme@example.com")
        self._request_login_link("acme@example.com")

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(CustomerLoginToken.objects.filter(customer=self.customer).count(), 1)

    def test_cooldown_is_per_email_not_global(self):
        self._request_login_link("acme@example.com")
        self._request_login_link("other@example.com")

        self.assertEqual(len(mail.outbox), 2)

    def test_valid_token_logs_in_and_reaches_dashboard(self):
        self._request_login_link("acme@example.com")
        token = CustomerLoginToken.objects.get(customer=self.customer)

        response = self.client.get(reverse("customer_portal_verify", args=[token.token]))
        self.assertEqual(response.status_code, 302)

        response = self.client.get(reverse("customer_portal_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Acme Shop")

    def test_login_sets_shorter_session_expiry_than_default(self):
        from django.conf import settings as django_settings

        self._request_login_link("acme@example.com")
        token = CustomerLoginToken.objects.get(customer=self.customer)
        self.client.get(reverse("customer_portal_verify", args=[token.token]))

        self.assertEqual(
            self.client.session.get_expiry_age(),
            django_settings.CUSTOMER_PORTAL_SESSION_AGE_SECONDS,
        )
        self.assertLess(
            django_settings.CUSTOMER_PORTAL_SESSION_AGE_SECONDS,
            django_settings.SESSION_COOKIE_AGE,
        )

    def test_login_flushes_pre_existing_session(self):
        session = self.client.session
        session["pre_existing"] = "fixation-attempt"
        session.save()
        old_session_key = session.session_key

        self._request_login_link("acme@example.com")
        token = CustomerLoginToken.objects.get(customer=self.customer)
        self.client.get(reverse("customer_portal_verify", args=[token.token]))

        self.assertNotEqual(self.client.session.session_key, old_session_key)
        self.assertNotIn("pre_existing", self.client.session)

    def test_token_is_single_use(self):
        self._request_login_link("acme@example.com")
        token = CustomerLoginToken.objects.get(customer=self.customer)

        self.client.get(reverse("customer_portal_verify", args=[token.token]))
        self.client.post(reverse("customer_portal_logout"))

        response = self.client.get(reverse("customer_portal_verify", args=[token.token]))
        self.assertEqual(response.status_code, 400)

    def test_expired_token_rejected(self):
        token = CustomerLoginToken.objects.create(
            customer=self.customer,
            token="expired-token",
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        response = self.client.get(reverse("customer_portal_verify", args=[token.token]))
        self.assertEqual(response.status_code, 400)

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("customer_portal_dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("customer_portal_login"))

    def test_dashboard_shows_only_own_invoices(self):
        self._request_login_link("acme@example.com")
        token = CustomerLoginToken.objects.get(customer=self.customer)
        self.client.get(reverse("customer_portal_verify", args=[token.token]))

        response = self.client.get(reverse("customer_portal_dashboard"))
        self.assertContains(response, reverse("customer_portal_invoice_detail", args=[self.invoice.id]))
        self.assertEqual(response.context["page_obj"].paginator.count, 1)

    def test_cannot_view_other_customers_invoice(self):
        self._request_login_link("acme@example.com")
        token = CustomerLoginToken.objects.get(customer=self.customer)
        self.client.get(reverse("customer_portal_verify", args=[token.token]))

        response = self.client.get(reverse("customer_portal_invoice_detail", args=[self.other_invoice.id]))
        self.assertEqual(response.status_code, 404)

    def test_own_invoice_detail_and_pdf_accessible(self):
        self._request_login_link("acme@example.com")
        token = CustomerLoginToken.objects.get(customer=self.customer)
        self.client.get(reverse("customer_portal_verify", args=[token.token]))

        response = self.client.get(reverse("customer_portal_invoice_detail", args=[self.invoice.id]))
        self.assertEqual(response.status_code, 200)

        response = self.client.get(reverse("customer_portal_invoice_pdf", args=[self.invoice.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_logout_clears_session(self):
        self._request_login_link("acme@example.com")
        token = CustomerLoginToken.objects.get(customer=self.customer)
        self.client.get(reverse("customer_portal_verify", args=[token.token]))
        logged_in_session_key = self.client.session.session_key

        self.client.post(reverse("customer_portal_logout"))

        self.assertNotEqual(self.client.session.session_key, logged_in_session_key)
        response = self.client.get(reverse("customer_portal_dashboard"))
        self.assertEqual(response.status_code, 302)


class CleanupLoginTokensTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="owner", password="pass1234")
        self.customer = Customer.objects.create(owner=self.user, name="Acme Shop", email="acme@example.com")
        now = timezone.now()

        self.fresh_unused = CustomerLoginToken.objects.create(
            customer=self.customer, token="fresh-unused", expires_at=now + timedelta(minutes=30),
        )
        self.stale_unused = CustomerLoginToken.objects.create(
            customer=self.customer, token="stale-unused", expires_at=now - timedelta(days=10),
        )
        self.recently_expired = CustomerLoginToken.objects.create(
            customer=self.customer, token="recently-expired", expires_at=now - timedelta(hours=1),
        )
        self.stale_used = CustomerLoginToken.objects.create(
            customer=self.customer, token="stale-used",
            expires_at=now - timedelta(days=10), used_at=now - timedelta(days=10),
        )
        self.recently_used = CustomerLoginToken.objects.create(
            customer=self.customer, token="recently-used",
            expires_at=now - timedelta(days=10), used_at=now - timedelta(hours=1),
        )

    def test_deletes_only_used_or_expired_tokens_past_retention(self):
        call_command("cleanup_login_tokens", "--days", "7")

        remaining = set(CustomerLoginToken.objects.values_list("token", flat=True))
        self.assertEqual(remaining, {"fresh-unused", "recently-expired", "recently-used"})

    def test_dry_run_deletes_nothing(self):
        call_command("cleanup_login_tokens", "--days", "7", "--dry-run")
        self.assertEqual(CustomerLoginToken.objects.count(), 5)

    def test_days_argument_controls_retention_window(self):
        call_command("cleanup_login_tokens", "--days", "0")

        remaining = set(CustomerLoginToken.objects.values_list("token", flat=True))
        self.assertEqual(remaining, {"fresh-unused"})


class CustomerPortalPaymentTests(TestCase):

    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.owner = User.objects.create_user(username="owner", password="pass1234")
        self.customer = Customer.objects.create(owner=self.owner, name="Acme Shop", email="acme@example.com")
        self.invoice = Invoice.objects.create(
            owner=self.owner, customer=self.customer, customer_name=self.customer.name,
            invoice_date=date(2024, 1, 1), currency="USD", status=Invoice.STATUS_SENT,
        )
        InvoiceItem.objects.create(invoice=self.invoice, description="Book", quantity=2, unit_price=Decimal("10.00"))
        self._login_as_customer()

    def _login_as_customer(self):
        self.client.post(reverse("customer_portal_login"), {"email": "acme@example.com"})
        token = CustomerLoginToken.objects.get(customer=self.customer)
        self.client.get(reverse("customer_portal_verify", args=[token.token]))

    def _add_stripe_integration(self, is_active=True):
        return Integration.objects.create(
            owner=self.owner, platform=Integration.PLATFORM_STRIPE, name="Stripe",
            api_key="sk_test_dummy", webhook_secret="whsec_dummy", is_active=is_active,
        )

    def test_pay_now_hidden_without_stripe_integration(self):
        response = self.client.get(reverse("customer_portal_invoice_detail", args=[self.invoice.id]))
        self.assertNotContains(response, "Pay Now")

    def test_pay_now_shown_with_active_stripe_integration(self):
        self._add_stripe_integration()
        response = self.client.get(reverse("customer_portal_invoice_detail", args=[self.invoice.id]))
        self.assertContains(response, "Pay Now")

    def test_pay_now_hidden_for_inactive_integration(self):
        self._add_stripe_integration(is_active=False)
        response = self.client.get(reverse("customer_portal_invoice_detail", args=[self.invoice.id]))
        self.assertNotContains(response, "Pay Now")

    def test_pay_now_hidden_for_paid_invoice(self):
        self._add_stripe_integration()
        self.invoice.status = Invoice.STATUS_PAID
        self.invoice.save()
        response = self.client.get(reverse("customer_portal_invoice_detail", args=[self.invoice.id]))
        self.assertNotContains(response, "Pay Now")

    @patch("books.views.stripe.StripeClient")
    def test_pay_creates_checkout_session_and_redirects(self, mock_client_cls):
        self._add_stripe_integration()
        fake_session = SimpleNamespace(url="https://checkout.stripe.com/test-session")
        mock_client = MagicMock()
        mock_client.v1.checkout.sessions.create.return_value = fake_session
        mock_client_cls.return_value = mock_client

        response = self.client.post(reverse("customer_portal_invoice_pay", args=[self.invoice.id]))

        self.assertRedirects(response, fake_session.url, fetch_redirect_response=False)
        call_kwargs = mock_client.v1.checkout.sessions.create.call_args.kwargs
        self.assertEqual(call_kwargs["params"]["metadata"]["invoice_id"], str(self.invoice.id))
        self.assertEqual(call_kwargs["params"]["line_items"][0]["price_data"]["unit_amount"], 2000)

    def test_pay_without_integration_shows_error_and_redirects(self):
        response = self.client.post(reverse("customer_portal_invoice_pay", args=[self.invoice.id]), follow=True)
        self.assertContains(response, "Online payment isn")

    def test_pay_already_paid_invoice_does_not_call_stripe(self):
        self._add_stripe_integration()
        self.invoice.status = Invoice.STATUS_PAID
        self.invoice.save()

        with patch("books.views.stripe.StripeClient") as mock_client_cls:
            self.client.post(reverse("customer_portal_invoice_pay", args=[self.invoice.id]))
            mock_client_cls.assert_not_called()

    @patch("books.views.stripe.StripeClient")
    def test_pay_stripe_error_shows_message(self, mock_client_cls):
        self._add_stripe_integration()
        mock_client = MagicMock()
        mock_client.v1.checkout.sessions.create.side_effect = stripe.StripeError("boom")
        mock_client_cls.return_value = mock_client

        response = self.client.post(reverse("customer_portal_invoice_pay", args=[self.invoice.id]), follow=True)
        self.assertContains(response, "Couldn")

    def test_pay_requires_portal_login(self):
        self.client.post(reverse("customer_portal_logout"))
        response = self.client.post(reverse("customer_portal_invoice_pay", args=[self.invoice.id]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("customer_portal_login"))

    def test_cannot_pay_other_customers_invoice(self):
        other_customer = Customer.objects.create(owner=self.owner, name="Other", email="other@example.com")
        other_invoice = Invoice.objects.create(
            owner=self.owner, customer=other_customer, customer_name=other_customer.name,
            invoice_date=date(2024, 1, 1), currency="USD", status=Invoice.STATUS_SENT,
        )
        response = self.client.post(reverse("customer_portal_invoice_pay", args=[other_invoice.id]))
        self.assertEqual(response.status_code, 404)


class StripeWebhookTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(username="owner", password="pass1234")
        self.integration = Integration.objects.create(
            owner=self.owner, platform=Integration.PLATFORM_STRIPE, name="Stripe",
            api_key="sk_test_dummy", webhook_secret="whsec_dummy", is_active=True,
        )
        self.invoice = Invoice.objects.create(
            owner=self.owner, customer_name="Acme", invoice_date=date(2024, 1, 1),
            currency="USD", status=Invoice.STATUS_SENT,
        )
        InvoiceItem.objects.create(invoice=self.invoice, description="Book", quantity=2, unit_price=Decimal("10.00"))

    def _fake_event(self, invoice_id, amount_total=2000, currency="usd", payment_intent="pi_123"):
        # Real Stripe webhook payloads are StripeObject instances, which only
        # support bracket access, not dict.get() - construct_from mirrors that
        # so tests catch the same bugs a real webhook delivery would.
        return stripe.StripeObject.construct_from({
            "type": "checkout.session.completed",
            "data": {"object": {
                "metadata": {"invoice_id": str(invoice_id)},
                "amount_total": amount_total,
                "currency": currency,
                "payment_intent": payment_intent,
            }},
        }, None)

    def _post_webhook(self, integration_id=None):
        return self.client.post(
            reverse("stripe_webhook", args=[integration_id or self.integration.id]),
            data=b"{}", content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=1,v1=fake",
        )

    @patch("books.views.stripe.Webhook.construct_event")
    def test_valid_event_marks_invoice_paid(self, mock_construct):
        mock_construct.return_value = self._fake_event(self.invoice.id)
        response = self._post_webhook()

        self.assertEqual(response.status_code, 200)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.STATUS_PAID)
        self.assertEqual(self.invoice.stripe_payment_intent_id, "pi_123")

    @patch("books.views.stripe.Webhook.construct_event")
    def test_invalid_signature_returns_401(self, mock_construct):
        mock_construct.side_effect = stripe.SignatureVerificationError("bad sig", "sig_header")
        response = self._post_webhook()

        self.assertEqual(response.status_code, 401)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.STATUS_SENT)

    def test_unknown_integration_returns_404(self):
        response = self._post_webhook(integration_id=999999)
        self.assertEqual(response.status_code, 404)

    @patch("books.views.stripe.Webhook.construct_event")
    def test_amount_mismatch_does_not_mark_paid(self, mock_construct):
        mock_construct.return_value = self._fake_event(self.invoice.id, amount_total=999)
        response = self._post_webhook()

        self.assertEqual(response.status_code, 200)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.STATUS_SENT)

    @patch("books.views.stripe.Webhook.construct_event")
    def test_currency_mismatch_does_not_mark_paid(self, mock_construct):
        mock_construct.return_value = self._fake_event(self.invoice.id, currency="eur")
        response = self._post_webhook()

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.STATUS_SENT)

    @patch("books.views.stripe.Webhook.construct_event")
    def test_already_paid_invoice_not_reprocessed(self, mock_construct):
        self.invoice.status = Invoice.STATUS_PAID
        self.invoice.stripe_payment_intent_id = "pi_original"
        self.invoice.save()
        mock_construct.return_value = self._fake_event(self.invoice.id, payment_intent="pi_new")

        response = self._post_webhook()

        self.assertEqual(response.status_code, 200)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.stripe_payment_intent_id, "pi_original")

    def test_get_request_returns_405(self):
        response = self.client.get(reverse("stripe_webhook", args=[self.integration.id]))
        self.assertEqual(response.status_code, 405)

    @patch("books.views.stripe.Webhook.construct_event")
    def test_unrelated_event_type_ignored(self, mock_construct):
        mock_construct.return_value = stripe.StripeObject.construct_from(
            {"type": "payment_intent.created", "data": {"object": {}}}, None,
        )
        response = self._post_webhook()

        self.assertEqual(response.status_code, 200)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.STATUS_SENT)


class SubscriptionRequiredMiddlewareTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="owner", password="pass1234")
        grant(self.user, "view_book")
        self.superuser = User.objects.create_superuser(username="root", password="pass1234", email="r@example.com")

    def test_unauthenticated_not_redirected(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)

    def test_superuser_bypasses_gate(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_user_with_no_subscription_row_is_grandfathered_in(self):
        # No Subscription row at all = predates this feature - must not be
        # retroactively gated, unlike a row that exists but isn't paid up.
        self.client.force_login(self.user)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_user_with_incomplete_subscription_redirected_to_billing_start(self):
        Subscription.objects.create(user=self.user, status=Subscription.STATUS_INCOMPLETE)
        self.client.force_login(self.user)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("billing_start"))

    def test_user_with_active_subscription_allowed(self):
        Subscription.objects.create(
            user=self.user, external_customer_id="cust_1", status=Subscription.STATUS_ACTIVE,
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_user_with_unpaid_subscription_redirected_to_billing_required(self):
        Subscription.objects.create(
            user=self.user, external_customer_id="cust_1", status=Subscription.STATUS_UNPAID,
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("billing_required"))

    def test_canceled_subscription_with_no_customer_id_goes_to_billing_start(self):
        Subscription.objects.create(user=self.user, status=Subscription.STATUS_CANCELED)
        self.client.force_login(self.user)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("billing_start"))

    def test_exempt_paths_not_gated(self):
        self.client.force_login(self.user)
        # billing_required renders directly (no outbound iyzico call) so it's
        # a reliable probe that the middleware didn't intercept this exempt
        # path and bounce it back to billing_start before the view ever ran.
        response = self.client.get(reverse("billing_required"))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(reverse("platform_webhook"))
        self.assertNotEqual(response.status_code, 302)


class IyzicoClientSigningTests(TestCase):
    """Unit tests for the signing algorithm itself, independent of any
    mocking - a mock can't catch a wrong implementation of the algorithm
    it's standing in for, only a hand-computed expected value can."""

    def setUp(self):
        self.override = override_settings(IYZICO_API_KEY="test-api-key", IYZICO_SECRET_KEY="test-secret-key")
        self.override.enable()

    def tearDown(self):
        self.override.disable()

    def test_auth_header_matches_documented_algorithm(self):
        random_key, header = iyzico_client._auth_header("/v2/subscription/products", '{"name":"Plan"}')

        expected_signature = hmac.new(
            b"test-secret-key",
            f'{random_key}/v2/subscription/products{{"name":"Plan"}}'.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        expected_params = f"apiKey:test-api-key&randomKey:{random_key}&signature:{expected_signature}"
        expected_header = f"IYZWSv2 {base64.b64encode(expected_params.encode('utf-8')).decode('ascii')}"

        self.assertEqual(header, expected_header)

    def test_verify_webhook_signature_accepts_correctly_signed_payload(self):
        to_sign = "merchant1test-secret-keysubscription.order.successsub_1order_1cust_1"
        valid_signature = hmac.new(b"test-secret-key", to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        self.assertTrue(iyzico_client.verify_webhook_signature(
            "merchant1", "subscription.order.success", "sub_1", "order_1", "cust_1", valid_signature,
        ))

    def test_verify_webhook_signature_rejects_tampered_payload(self):
        to_sign = "merchant1test-secret-keysubscription.order.successsub_1order_1cust_1"
        valid_signature = hmac.new(b"test-secret-key", to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        self.assertFalse(iyzico_client.verify_webhook_signature(
            "merchant1", "subscription.order.success", "sub_1", "order_1", "cust_999", valid_signature,
        ))

    def test_verify_webhook_signature_rejects_missing_signature(self):
        self.assertFalse(iyzico_client.verify_webhook_signature(
            "merchant1", "subscription.order.success", "sub_1", "order_1", "cust_1", "",
        ))


class BillingViewTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="owner", password="pass1234", email="owner@example.com")
        grant(self.user, "view_book")
        self.client.force_login(self.user)
        self.valid_form_data = {
            "name": "Ada", "surname": "Lovelace", "email": "owner@example.com",
            "gsm_number": "+905551234567", "identity_number": "11111111111",
            "address": "Some street 1", "city": "Istanbul", "country": "Turkey", "zip_code": "",
        }

    def test_billing_start_redirects_to_dashboard_if_already_good_standing(self):
        Subscription.objects.create(
            user=self.user, external_customer_id="cust_1", status=Subscription.STATUS_ACTIVE,
        )
        response = self.client.get(reverse("billing_start"))
        self.assertRedirects(response, reverse("dashboard"))

    def test_billing_start_get_shows_form(self):
        response = self.client.get(reverse("billing_start"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "name")

    @patch("books.views.iyzico_client.initialize_subscription_checkout_form")
    def test_billing_start_post_initializes_checkout_and_renders_embed(self, mock_init):
        mock_init.return_value = {"data": {"token": "tok_123", "checkoutFormContent": "<script>widget</script>"}}

        response = self.client.post(reverse("billing_start"), self.valid_form_data)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "widget")
        call_kwargs = mock_init.call_args.kwargs if mock_init.call_args.kwargs else {}
        call_args = mock_init.call_args.args
        customer = call_args[2] if len(call_args) > 2 else call_kwargs.get("customer")
        self.assertEqual(customer["identityNumber"], "11111111111")
        self.assertEqual(customer["billingAddress"]["city"], "Istanbul")

        subscription = Subscription.objects.get(user=self.user)
        self.assertEqual(subscription.checkout_token, "tok_123")

    @patch("books.views.iyzico_client.initialize_subscription_checkout_form")
    def test_billing_start_post_iyzico_error_redirects_to_billing_required(self, mock_init):
        mock_init.side_effect = iyzico_client.IyzicoError("boom")

        response = self.client.post(reverse("billing_start"), self.valid_form_data)
        self.assertRedirects(response, reverse("billing_required"))

    def test_billing_start_post_invalid_form_reshows_form(self):
        response = self.client.post(reverse("billing_start"), {})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertFalse(Subscription.objects.filter(user=self.user, checkout_token__gt="").exists())

    @patch("books.views.iyzico_client.retrieve_checkout_form")
    def test_billing_callback_activates_subscription(self, mock_retrieve):
        Subscription.objects.create(user=self.user, checkout_token="tok_123", status=Subscription.STATUS_INCOMPLETE)
        mock_retrieve.return_value = {"data": {
            "customerReferenceCode": "cust_new",
            "referenceCode": "sub_new",
            "subscriptionStatus": "ACTIVE",
        }}

        response = self.client.get(reverse("billing_callback"), {"token": "tok_123"})
        self.assertRedirects(response, reverse("dashboard"))

        subscription = Subscription.objects.get(user=self.user)
        self.assertEqual(subscription.external_customer_id, "cust_new")
        self.assertEqual(subscription.external_subscription_id, "sub_new")
        self.assertEqual(subscription.status, Subscription.STATUS_ACTIVE)
        self.assertEqual(subscription.checkout_token, "")

    @patch("books.views.iyzico_client.retrieve_checkout_form")
    def test_billing_callback_pending_status_redirects_to_billing_required(self, mock_retrieve):
        mock_retrieve.return_value = {"data": {
            "customerReferenceCode": "cust_new", "referenceCode": "sub_new", "subscriptionStatus": "PENDING",
        }}

        response = self.client.get(reverse("billing_callback"), {"token": "tok_123"})
        self.assertRedirects(response, reverse("billing_required"))

    @patch("books.views.iyzico_client.retrieve_checkout_form")
    def test_billing_callback_iyzico_error_redirects_to_billing_required(self, mock_retrieve):
        mock_retrieve.side_effect = iyzico_client.IyzicoError("boom")

        response = self.client.get(reverse("billing_callback"), {"token": "tok_123"})
        self.assertRedirects(response, reverse("billing_required"))

    def test_billing_portal_redirects_to_start_without_customer_id(self):
        response = self.client.get(reverse("billing_portal"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("billing_start"))

    @patch("books.views.iyzico_client.initialize_card_update_checkout_form")
    def test_billing_portal_renders_embed(self, mock_init):
        Subscription.objects.create(
            user=self.user, external_customer_id="cust_1", status=Subscription.STATUS_UNPAID,
        )
        mock_init.return_value = {"data": {"checkoutFormContent": "<script>cardwidget</script>"}}

        response = self.client.get(reverse("billing_portal"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cardwidget")

    def test_billing_card_update_callback_redirects_to_billing_required(self):
        response = self.client.get(reverse("billing_card_update_callback"))
        self.assertRedirects(response, reverse("billing_required"))

    def test_billing_required_redirects_to_dashboard_if_good_standing(self):
        Subscription.objects.create(
            user=self.user, external_customer_id="cust_1", status=Subscription.STATUS_ACTIVE,
        )
        response = self.client.get(reverse("billing_required"))
        self.assertRedirects(response, reverse("dashboard"))

    def test_billing_required_shown_when_unpaid(self):
        Subscription.objects.create(
            user=self.user, external_customer_id="cust_1", status=Subscription.STATUS_UNPAID,
        )
        response = self.client.get(reverse("billing_required"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Manage billing")


class PlatformWebhookTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="owner", password="pass1234")
        grant(self.user, "view_book")
        self.override = override_settings(IYZICO_MERCHANT_ID="merchant1", IYZICO_SECRET_KEY="test-secret-key")
        self.override.enable()
        self.addCleanup(self.override.disable)

    def _signed_payload(self, event_type, subscription_ref, order_ref="order_1", customer_ref="cust_1"):
        to_sign = f"merchant1test-secret-key{event_type}{subscription_ref}{order_ref}{customer_ref}"
        signature = hmac.new(b"test-secret-key", to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        payload = {
            "iyziEventType": event_type,
            "subscriptionReferenceCode": subscription_ref,
            "orderReferenceCode": order_ref,
            "customerReferenceCode": customer_ref,
        }
        return payload, signature

    def _post_webhook(self, payload, signature):
        return self.client.post(
            reverse("platform_webhook"),
            data=json.dumps(payload), content_type="application/json",
            HTTP_X_IYZ_SIGNATURE_V3=signature,
        )

    def test_success_event_marks_subscription_active(self):
        Subscription.objects.create(user=self.user, external_subscription_id="sub_1", status=Subscription.STATUS_UNPAID)
        payload, signature = self._signed_payload("subscription.order.success", "sub_1")

        response = self._post_webhook(payload, signature)
        self.assertEqual(response.status_code, 200)

        subscription = Subscription.objects.get(user=self.user)
        self.assertEqual(subscription.status, Subscription.STATUS_ACTIVE)

    def test_failure_event_marks_subscription_unpaid(self):
        Subscription.objects.create(user=self.user, external_subscription_id="sub_1", status=Subscription.STATUS_ACTIVE)
        payload, signature = self._signed_payload("subscription.order.failure", "sub_1")

        response = self._post_webhook(payload, signature)
        self.assertEqual(response.status_code, 200)

        subscription = Subscription.objects.get(user=self.user)
        self.assertEqual(subscription.status, Subscription.STATUS_UNPAID)

    def test_invalid_signature_returns_401(self):
        payload, _ = self._signed_payload("subscription.order.success", "sub_1")
        response = self._post_webhook(payload, "wrong-signature")
        self.assertEqual(response.status_code, 401)

    def test_unknown_subscription_does_not_crash(self):
        payload, signature = self._signed_payload("subscription.order.success", "sub_unknown")
        response = self._post_webhook(payload, signature)
        self.assertEqual(response.status_code, 200)

    def test_invalid_json_returns_400(self):
        response = self.client.post(
            reverse("platform_webhook"), data=b"not json", content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_get_request_returns_405(self):
        response = self.client.get(reverse("platform_webhook"))
        self.assertEqual(response.status_code, 405)


class TeamInviteTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(username="team_admin", password="pass1234", email="admin@example.com")
        self.account = get_or_create_account_for_user(self.admin)
        self.membership = AccountMembership.objects.get(user=self.admin)

    def test_non_admin_cannot_access_team_page(self):
        staff = get_user_model().objects.create_user(username="team_staff", password="pass1234")
        AccountMembership.objects.create(account=self.account, user=staff, role=AccountMembership.ROLE_STAFF)

        self.client.force_login(staff)
        response = self.client.get(reverse("team_members"))
        self.assertEqual(response.status_code, 403)

    def test_admin_can_view_team_page(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("team_members"))
        self.assertEqual(response.status_code, 200)

    def test_admin_can_send_invite(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("team_members"), {"email": "invitee@example.com", "role": "Staff"})
        self.assertRedirects(response, reverse("team_members"))

        invitation = AccountInvitation.objects.get(email="invitee@example.com")
        self.assertEqual(invitation.account, self.account)
        self.assertEqual(invitation.role, "Staff")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(invitation.token, mail.outbox[0].body)

    def test_admin_can_update_account_settings(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("team_members"), {
            "action": "update_settings", "default_tax_rate": "8.5",
        })
        self.assertRedirects(response, reverse("team_members"))

        self.account.refresh_from_db()
        self.assertEqual(self.account.default_tax_rate, Decimal("8.50"))

    def test_non_admin_cannot_update_account_settings(self):
        staff = get_user_model().objects.create_user(username="team_staff2", password="pass1234")
        AccountMembership.objects.create(account=self.account, user=staff, role=AccountMembership.ROLE_STAFF)

        self.client.force_login(staff)
        response = self.client.post(reverse("team_members"), {
            "action": "update_settings", "default_tax_rate": "8.5",
        })
        self.assertEqual(response.status_code, 403)

    @staticmethod
    def _tiny_png():
        from io import BytesIO
        from PIL import Image as PILImage

        buf = BytesIO()
        PILImage.new("RGB", (10, 10), color=(255, 0, 0)).save(buf, format="PNG")
        buf.seek(0)
        return SimpleUploadedFile("logo.png", buf.read(), content_type="image/png")

    def test_admin_can_upload_logo_and_brand_color(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("team_members"), {
            "action": "update_settings", "default_tax_rate": "0",
            "brand_color": "#336699", "logo": self._tiny_png(),
        })
        self.assertRedirects(response, reverse("team_members"))

        self.account.refresh_from_db()
        self.assertEqual(self.account.brand_color, "#336699")
        self.assertTrue(self.account.logo)

    def test_invalid_brand_color_is_rejected(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("team_members"), {
            "action": "update_settings", "default_tax_rate": "0", "brand_color": "not-a-color",
        })
        self.assertEqual(response.status_code, 200)

        self.account.refresh_from_db()
        self.assertEqual(self.account.brand_color, "")

    def test_oversized_logo_is_rejected(self):
        import os
        from io import BytesIO
        from PIL import Image as PILImage

        self.client.force_login(self.admin)

        buf = BytesIO()
        noise = PILImage.frombytes("RGB", (1000, 1000), os.urandom(1000 * 1000 * 3))
        noise.save(buf, format="PNG")
        buf.seek(0)
        self.assertGreater(len(buf.getvalue()), LOGO_MAX_SIZE_BYTES)
        oversized = SimpleUploadedFile("logo.png", buf.read(), content_type="image/png")

        response = self.client.post(reverse("team_members"), {
            "action": "update_settings", "default_tax_rate": "0", "logo": oversized,
        })
        self.assertEqual(response.status_code, 200)

        self.account.refresh_from_db()
        self.assertFalse(self.account.logo)

    def test_accept_invite_creates_membership_and_syncs_groups(self):
        invitation = AccountInvitation.objects.create(
            account=self.account,
            email="invitee@example.com",
            role=AccountMembership.ROLE_STAFF,
            token="test-token-123",
            expires_at=timezone.now() + timedelta(days=7),
        )

        response = self.client.post(
            reverse("team_accept_invite", args=[invitation.token]),
            {"username": "newstaffer", "password1": "S3curePass!!", "password2": "S3curePass!!"},
        )
        self.assertRedirects(response, reverse("dashboard"))

        new_user = get_user_model().objects.get(username="newstaffer")
        membership = AccountMembership.objects.get(user=new_user)
        self.assertEqual(membership.account, self.account)
        self.assertEqual(membership.role, AccountMembership.ROLE_STAFF)
        self.assertEqual(list(new_user.groups.values_list("name", flat=True)), ["Staff"])

        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.accepted_at)

    def test_accept_invite_rejects_expired(self):
        invitation = AccountInvitation.objects.create(
            account=self.account,
            email="invitee@example.com",
            role=AccountMembership.ROLE_STAFF,
            token="expired-token",
            expires_at=timezone.now() - timedelta(days=1),
        )
        response = self.client.get(reverse("team_accept_invite", args=[invitation.token]))
        self.assertEqual(response.status_code, 410)

    def test_accept_invite_rejects_already_accepted(self):
        invitation = AccountInvitation.objects.create(
            account=self.account,
            email="invitee@example.com",
            role=AccountMembership.ROLE_STAFF,
            token="used-token",
            expires_at=timezone.now() + timedelta(days=7),
            accepted_at=timezone.now(),
        )
        response = self.client.get(reverse("team_accept_invite", args=[invitation.token]))
        self.assertEqual(response.status_code, 410)

    def test_accept_invite_rejects_email_with_existing_account(self):
        get_user_model().objects.create_user(username="already_exists", password="pass1234", email="invitee@example.com")
        invitation = AccountInvitation.objects.create(
            account=self.account,
            email="invitee@example.com",
            role=AccountMembership.ROLE_STAFF,
            token="dup-email-token",
            expires_at=timezone.now() + timedelta(days=7),
        )
        response = self.client.get(reverse("team_accept_invite", args=[invitation.token]))
        self.assertEqual(response.status_code, 409)

    def test_cannot_remove_last_admin(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("team_member_remove", args=[self.membership.id]))
        self.assertRedirects(response, reverse("team_members"))
        self.assertTrue(AccountMembership.objects.filter(id=self.membership.id).exists())

    def test_cannot_demote_last_admin(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("team_member_update_role", args=[self.membership.id]), {"role": "Staff"},
        )
        self.assertRedirects(response, reverse("team_members"))
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.role, AccountMembership.ROLE_ADMIN)

    def test_can_remove_non_last_admin_member(self):
        staff_user = get_user_model().objects.create_user(username="removable_staff", password="pass1234")
        staff_membership = AccountMembership.objects.create(
            account=self.account, user=staff_user, role=AccountMembership.ROLE_STAFF,
        )

        self.client.force_login(self.admin)
        response = self.client.post(reverse("team_member_remove", args=[staff_membership.id]))
        self.assertRedirects(response, reverse("team_members"))
        self.assertFalse(AccountMembership.objects.filter(id=staff_membership.id).exists())

    def test_removing_member_revokes_their_group_permissions(self):
        staff_user = get_user_model().objects.create_user(username="ex_staff", password="pass1234")
        staff_membership = AccountMembership.objects.create(
            account=self.account, user=staff_user, role=AccountMembership.ROLE_STAFF,
        )
        sync_user_groups_for_role(staff_user, AccountMembership.ROLE_STAFF)
        self.assertEqual(list(staff_user.groups.values_list("name", flat=True)), ["Staff"])

        self.client.force_login(self.admin)
        self.client.post(reverse("team_member_remove", args=[staff_membership.id]))

        staff_user.refresh_from_db()
        self.assertEqual(list(staff_user.groups.values_list("name", flat=True)), [])

    def test_admin_can_cancel_pending_invitation(self):
        invitation = AccountInvitation.objects.create(
            account=self.account,
            email="cancel-me@example.com",
            role=AccountMembership.ROLE_STAFF,
            token="cancel-token",
            expires_at=timezone.now() + timedelta(days=7),
        )

        self.client.force_login(self.admin)
        response = self.client.post(reverse("team_invite_cancel", args=[invitation.id]))
        self.assertRedirects(response, reverse("team_members"))
        self.assertFalse(AccountInvitation.objects.filter(id=invitation.id).exists())


def _open_library_response(bibkey, record):
    response = MagicMock()
    response.json.return_value = {bibkey: record} if record else {}
    return response


class IsbnLookupClientTests(TestCase):

    def test_lookup_isbn_requires_isbn(self):
        with self.assertRaises(IsbnLookupError):
            lookup_isbn("")

    @patch("books.isbn_lookup.requests.get")
    def test_lookup_isbn_returns_parsed_data(self, mock_get):
        mock_get.return_value = _open_library_response("ISBN:111", {
            "title": "The Last Lighthouse",
            "subtitle": "A Novel",
            "publishers": [{"name": "Acme Press"}],
            "publish_date": "January 1, 2009",
            "authors": [{"name": "Jane Doe"}],
            "cover": {"large": "https://covers.example/large.jpg", "medium": "https://covers.example/medium.jpg"},
        })

        result = lookup_isbn("111")

        self.assertEqual(result["title"], "The Last Lighthouse")
        self.assertEqual(result["subtitle"], "A Novel")
        self.assertEqual(result["publishers"], ["Acme Press"])
        self.assertEqual(result["publish_date"], "January 1, 2009")
        self.assertEqual(result["authors"], ["Jane Doe"])
        self.assertEqual(result["cover_url"], "https://covers.example/large.jpg")

    @patch("books.isbn_lookup.requests.get")
    def test_lookup_isbn_falls_back_to_medium_cover(self, mock_get):
        mock_get.return_value = _open_library_response("ISBN:111", {
            "title": "No Large Cover", "cover": {"medium": "https://covers.example/medium.jpg"},
        })
        result = lookup_isbn("111")
        self.assertEqual(result["cover_url"], "https://covers.example/medium.jpg")

    @patch("books.isbn_lookup.requests.get")
    def test_lookup_isbn_raises_when_not_found(self, mock_get):
        mock_get.return_value = _open_library_response("ISBN:111", None)
        with self.assertRaises(IsbnLookupError):
            lookup_isbn("111")

    @patch("books.isbn_lookup.requests.get")
    def test_lookup_isbn_raises_on_network_error(self, mock_get):
        mock_get.side_effect = requests.RequestException("boom")
        with self.assertRaises(IsbnLookupError):
            lookup_isbn("111")

    @patch("books.isbn_lookup.requests.get")
    def test_lookup_isbn_raises_on_invalid_json(self, mock_get):
        response = MagicMock()
        response.json.side_effect = ValueError("not json")
        mock_get.return_value = response
        with self.assertRaises(IsbnLookupError):
            lookup_isbn("111")


class ParsePublishDateTests(TestCase):

    def test_parses_full_date(self):
        self.assertEqual(_parse_publish_date("January 1, 2009"), "2009-01-01")

    def test_parses_iso_date(self):
        self.assertEqual(_parse_publish_date("2009-01-01"), "2009-01-01")

    def test_parses_bare_year(self):
        self.assertEqual(_parse_publish_date("2009"), "2009-01-01")

    def test_falls_back_to_year_extracted_from_text(self):
        self.assertEqual(_parse_publish_date("circa 1990"), "1990-01-01")

    def test_returns_empty_string_for_unparseable_value(self):
        self.assertEqual(_parse_publish_date("unknown"), "")

    def test_returns_empty_string_for_blank_value(self):
        self.assertEqual(_parse_publish_date(""), "")


class IsbnLookupViewTests(TestCase):

    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.user = User.objects.create_user(username="lookup_user", password="pass1234")
        self.account = get_or_create_account_for_user(self.user)
        self.client.force_login(self.user)

    def test_get_not_allowed(self):
        response = self.client.get(reverse("isbn_lookup"), {"isbn": "111"})
        self.assertEqual(response.status_code, 405)

    def test_requires_isbn_param(self):
        response = self.client.post(reverse("isbn_lookup"))
        self.assertEqual(response.status_code, 400)

    @patch("books.views.lookup_isbn")
    def test_returns_404_when_not_found(self, mock_lookup):
        mock_lookup.side_effect = IsbnLookupError("No book found for this ISBN.")
        response = self.client.post(reverse("isbn_lookup"), {"isbn": "999"})
        self.assertEqual(response.status_code, 404)
        self.assertIn("error", response.json())

    @patch("books.views.lookup_isbn")
    def test_returns_data_and_creates_authors(self, mock_lookup):
        mock_lookup.return_value = {
            "title": "The Last Lighthouse",
            "subtitle": "A Novel",
            "publishers": ["Acme Press"],
            "publish_date": "2009",
            "authors": ["Jane Doe"],
            "cover_url": "https://covers.example/large.jpg",
        }

        response = self.client.post(reverse("isbn_lookup"), {"isbn": "111"})
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["title"], "The Last Lighthouse")
        self.assertEqual(data["publisher"], "Acme Press")
        self.assertEqual(data["published_date"], "2009-01-01")
        self.assertEqual(data["cover_url"], "https://covers.example/large.jpg")
        self.assertEqual(len(data["authors"]), 1)
        self.assertEqual(data["authors"][0]["name"], "Jane Doe")

        self.assertTrue(Author.objects.filter(account=self.account, name="Jane Doe").exists())

    @patch("books.views.lookup_isbn")
    def test_does_not_duplicate_existing_author(self, mock_lookup):
        Author.objects.create(owner=self.user, account=self.account, name="Jane Doe")
        mock_lookup.return_value = {
            "title": "Book", "subtitle": "", "publishers": [], "publish_date": "",
            "authors": ["Jane Doe"], "cover_url": "",
        }

        self.client.post(reverse("isbn_lookup"), {"isbn": "111"})

        self.assertEqual(Author.objects.filter(account=self.account, name="Jane Doe").count(), 1)

    @patch("books.views.lookup_isbn")
    def test_second_rapid_request_is_rate_limited(self, mock_lookup):
        mock_lookup.return_value = {
            "title": "Book", "subtitle": "", "publishers": [], "publish_date": "",
            "authors": [], "cover_url": "",
        }

        first = self.client.post(reverse("isbn_lookup"), {"isbn": "111"})
        second = self.client.post(reverse("isbn_lookup"), {"isbn": "222"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertIn("error", second.json())


class BookCoverUrlFormTests(TestCase):
    """cover_url is only ever set by the ISBN lookup JS (hidden field), but
    it still needs to round-trip correctly through the real form views."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="cover_user", password="pass1234")
        self.account = get_or_create_account_for_user(self.user)
        self.client.force_login(self.user)
        grant(self.user, "add_book", "change_book", "view_book")
        self.category = Category.objects.create(owner=self.user, account=self.account, name="Fiction")

    def _book_payload(self, **overrides):
        payload = {
            "isbn": "111", "title": "A Book", "subtitle": "", "authors": [],
            "publisher": "Acme", "published_date": "2024-01-01",
            "category": self.category.id, "distribution_expense": "10.00",
            "reorder_threshold": "5", "cover_url": "https://covers.example/large.jpg",
        }
        payload.update(overrides)
        return payload

    def test_create_saves_cover_url(self):
        self.client.post(reverse("book_create"), self._book_payload())
        book = Book.objects.get(account=self.account, title="A Book")
        self.assertEqual(book.cover_url, "https://covers.example/large.jpg")

    def test_create_without_cover_url_is_fine(self):
        self.client.post(reverse("book_create"), self._book_payload(cover_url=""))
        book = Book.objects.get(account=self.account, title="A Book")
        self.assertEqual(book.cover_url, "")

    def test_update_changes_cover_url(self):
        self.client.post(reverse("book_create"), self._book_payload())
        book = Book.objects.get(account=self.account, title="A Book")

        self.client.post(
            reverse("book_update", args=[book.id]),
            self._book_payload(cover_url="https://covers.example/updated.jpg"),
        )
        book.refresh_from_db()
        self.assertEqual(book.cover_url, "https://covers.example/updated.jpg")


class PasswordResetFlowTests(TestCase):
    """End-to-end: the feature was wired (django.contrib.auth.urls) and
    templates existed, but nothing exercised the real request/email/token/
    set-new-password round trip."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="reset_user", password="OldPass123!", email="reset_user@example.com",
        )

    def test_full_reset_round_trip(self):
        response = self.client.post(reverse("password_reset"), {"email": "reset_user@example.com"})
        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)

        match = re.search(r"/accounts/reset/(?P<uidb64>[^/]+)/(?P<token>[^/\s]+)/", mail.outbox[0].body)
        self.assertIsNotNone(match)
        confirm_url = match.group(0)

        # First GET validates the token and redirects to a session-backed
        # "set-password" URL (Django swaps the token out of the URL so it
        # can't be reused/bookmarked).
        response = self.client.get(confirm_url, follow=True)
        self.assertEqual(response.status_code, 200)
        set_password_url = response.redirect_chain[-1][0]
        self.assertIn("set-password", set_password_url)

        response = self.client.post(set_password_url, {
            "new_password1": "BrandNewPass456!",
            "new_password2": "BrandNewPass456!",
        })
        self.assertRedirects(response, reverse("password_reset_complete"))

        old_password_login = self.client.post(reverse("login"), {
            "username": "reset_user", "password": "OldPass123!",
        })
        self.assertFalse(old_password_login.wsgi_request.user.is_authenticated)

        new_password_login = self.client.post(reverse("login"), {
            "username": "reset_user", "password": "BrandNewPass456!",
        })
        self.assertEqual(new_password_login.status_code, 302)
        self.assertTrue(self.client.session.get("_auth_user_id"))

    def test_unknown_email_does_not_reveal_account_existence(self):
        response = self.client.post(reverse("password_reset"), {"email": "nobody@example.com"})
        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    def test_invalid_token_is_rejected(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        response = self.client.get(
            reverse("password_reset_confirm", kwargs={"uidb64": uid, "token": "bad-token"}),
            follow=True,
        )
        self.assertContains(response, "invalid", status_code=200)


class WholesalerFeedUploadTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="feedimporter", password="pass1234")
        self.account = get_or_create_account_for_user(self.user)
        self.client.force_login(self.user)
        grant(self.user, "add_wholesalerfeeditem", "view_wholesalerfeeditem", "delete_wholesalerfeeditem")
        self.supplier = Supplier.objects.create(owner=self.user, account=self.account, name="Acme Distribution")

    def _upload(self, content, supplier_id=None, replace=False, name="feed.csv"):
        data = {
            "supplier": supplier_id if supplier_id is not None else self.supplier.id,
            "csv_file": SimpleUploadedFile(name, content.encode("utf-8"), content_type="text/csv"),
        }
        if replace:
            data["replace_existing"] = "1"
        return self.client.post(reverse("wholesaler_feed_upload"), data)

    def test_upload_creates_items(self):
        csv_content = "isbn,title,price,stock\n111,Some Book,9.50,40\n222,Other Book,5.25,12\n"
        response = self._upload(csv_content)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(WholesalerFeedItem.objects.filter(account=self.account).count(), 2)
        item = WholesalerFeedItem.objects.get(account=self.account, isbn="111")
        self.assertEqual(item.wholesale_price, Decimal("9.50"))
        self.assertEqual(item.stock_quantity, 40)
        self.assertEqual(item.supplier, self.supplier)

    def test_reupload_updates_existing_item_for_same_supplier_isbn(self):
        self._upload("isbn,title,price,stock\n111,Some Book,9.50,40\n")
        self._upload("isbn,title,price,stock\n111,Some Book,7.00,5\n")

        self.assertEqual(WholesalerFeedItem.objects.filter(account=self.account, isbn="111").count(), 1)
        item = WholesalerFeedItem.objects.get(account=self.account, isbn="111")
        self.assertEqual(item.wholesale_price, Decimal("7.00"))
        self.assertEqual(item.stock_quantity, 5)

    def test_missing_isbn_row_is_skipped_not_fatal(self):
        csv_content = "isbn,title,price,stock\n,No ISBN Book,9.50,40\n222,Good Book,5.25,12\n"
        response = self._upload(csv_content)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(WholesalerFeedItem.objects.filter(account=self.account).count(), 1)
        self.assertTrue(WholesalerFeedItem.objects.filter(account=self.account, isbn="222").exists())

    def test_invalid_price_row_keeps_row_but_leaves_price_blank(self):
        csv_content = "isbn,title,price,stock\n111,Some Book,not-a-number,40\n"
        response = self._upload(csv_content, name="bad_price.csv")

        item = WholesalerFeedItem.objects.get(account=self.account, isbn="111")
        self.assertIsNone(item.wholesale_price)
        self.assertContains(response, "invalid price")

    def test_oversized_file_is_rejected(self):
        header = "isbn,title,price,stock\n"
        row = "111,Some Book,9.50,40\n"
        big_csv = header + row * 260000
        big_bytes = big_csv.encode("utf-8")
        self.assertGreater(len(big_bytes), settings.CSV_IMPORT_MAX_SIZE_BYTES)

        response = self.client.post(
            reverse("wholesaler_feed_upload"),
            {
                "supplier": self.supplier.id,
                "csv_file": SimpleUploadedFile("big.csv", big_bytes, content_type="text/csv"),
            },
            follow=True,
        )
        self.assertContains(response, "too large")
        self.assertFalse(WholesalerFeedItem.objects.filter(account=self.account).exists())

    def test_replace_existing_only_clears_items_for_chosen_supplier(self):
        other_supplier = Supplier.objects.create(owner=self.user, account=self.account, name="Other Supplier")
        WholesalerFeedItem.objects.create(
            owner=self.user, account=self.account, supplier=self.supplier, isbn="999", wholesale_price=Decimal("1.00"),
        )
        WholesalerFeedItem.objects.create(
            owner=self.user, account=self.account, supplier=other_supplier, isbn="888", wholesale_price=Decimal("2.00"),
        )

        self._upload("isbn,title,price,stock\n111,New Book,9.50,40\n", replace=True)

        self.assertFalse(WholesalerFeedItem.objects.filter(account=self.account, isbn="999").exists())
        self.assertTrue(WholesalerFeedItem.objects.filter(account=self.account, isbn="888").exists())
        self.assertTrue(WholesalerFeedItem.objects.filter(account=self.account, isbn="111").exists())

    def test_upload_requires_permission(self):
        other = get_user_model().objects.create_user(username="nopower_feed", password="pass1234")
        AccountMembership.objects.create(account=self.account, user=other, role=AccountMembership.ROLE_VIEWER)
        sync_user_groups_for_role(other, AccountMembership.ROLE_VIEWER)
        self.client.force_login(other)

        response = self._upload("isbn,title,price,stock\n111,Some Book,9.50,40\n")
        self.assertEqual(response.status_code, 403)

    def test_cannot_upload_against_another_accounts_supplier(self):
        other_user = get_user_model().objects.create_user(username="otheraccount_feed", password="pass1234")
        other_account = get_or_create_account_for_user(other_user)
        other_supplier = Supplier.objects.create(owner=other_user, account=other_account, name="Foreign Supplier")

        response = self._upload("isbn,title,price,stock\n111,Some Book,9.50,40\n", supplier_id=other_supplier.id)

        self.assertEqual(response.status_code, 302)
        self.assertFalse(WholesalerFeedItem.objects.filter(supplier=other_supplier).exists())

    def test_delete_view_is_account_scoped(self):
        other_user = get_user_model().objects.create_user(username="otheraccount_feed2", password="pass1234")
        other_account = get_or_create_account_for_user(other_user)
        other_supplier = Supplier.objects.create(owner=other_user, account=other_account, name="Foreign Supplier 2")
        foreign_item = WholesalerFeedItem.objects.create(
            owner=other_user, account=other_account, supplier=other_supplier, isbn="777", wholesale_price=Decimal("3.00"),
        )

        response = self.client.post(reverse("wholesaler_feed_delete", args=[foreign_item.id]))

        self.assertEqual(response.status_code, 404)
        self.assertTrue(WholesalerFeedItem.objects.filter(id=foreign_item.id).exists())


class WholesalerFeedBookDetailTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="feedviewer", password="pass1234")
        self.account = get_or_create_account_for_user(self.user)
        self.client.force_login(self.user)
        grant(self.user, "view_book", "view_wholesalerfeeditem")

        cat = Category.objects.create(owner=self.user, account=self.account, name="Fiction")
        self.book = Book.objects.create(
            owner=self.user, account=self.account, title="Matched Book", isbn="555",
            publisher="Acme", published_date=date(2024, 1, 1), category=cat,
            distribution_expense=Decimal("5.00"), stock_on_hand=1, reorder_threshold=1,
        )
        self.supplier = Supplier.objects.create(owner=self.user, account=self.account, name="Acme Distribution")

    def test_matching_offer_is_shown_with_permission(self):
        WholesalerFeedItem.objects.create(
            owner=self.user, account=self.account, supplier=self.supplier, isbn="555",
            wholesale_price=Decimal("4.20"), stock_quantity=30,
        )

        response = self.client.get(reverse("book_detail", args=[self.book.id]))
        self.assertContains(response, "Acme Distribution")
        self.assertContains(response, "4.20")

    def test_offer_hidden_without_permission(self):
        other = get_user_model().objects.create_user(username="nopower_feedview", password="pass1234")
        AccountMembership.objects.create(account=self.account, user=other, role=AccountMembership.ROLE_VIEWER)
        grant(other, "view_book")
        self.client.force_login(other)

        WholesalerFeedItem.objects.create(
            owner=self.user, account=self.account, supplier=self.supplier, isbn="555",
            wholesale_price=Decimal("4.20"), stock_quantity=30,
        )

        response = self.client.get(reverse("book_detail", args=[self.book.id]))
        self.assertNotContains(response, "Acme Distribution")

    def test_no_offers_for_unmatched_isbn(self):
        WholesalerFeedItem.objects.create(
            owner=self.user, account=self.account, supplier=self.supplier, isbn="not-this-book",
            wholesale_price=Decimal("4.20"), stock_quantity=30,
        )

        response = self.client.get(reverse("book_detail", args=[self.book.id]))
        self.assertContains(response, "No wholesaler feed data")


class WholesalerFeedListTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="feedlister", password="pass1234")
        self.account = get_or_create_account_for_user(self.user)
        self.client.force_login(self.user)
        grant(self.user, "view_wholesalerfeeditem")
        self.supplier = Supplier.objects.create(owner=self.user, account=self.account, name="Acme Distribution")

    def test_list_is_account_scoped(self):
        other_user = get_user_model().objects.create_user(username="otheraccount_feedlist", password="pass1234")
        other_account = get_or_create_account_for_user(other_user)
        other_supplier = Supplier.objects.create(owner=other_user, account=other_account, name="Foreign Supplier")
        WholesalerFeedItem.objects.create(
            owner=other_user, account=other_account, supplier=other_supplier, isbn="333", wholesale_price=Decimal("1.00"),
        )
        WholesalerFeedItem.objects.create(
            owner=self.user, account=self.account, supplier=self.supplier, isbn="444", wholesale_price=Decimal("2.00"),
        )

        response = self.client.get(reverse("wholesaler_feed_list"))
        self.assertContains(response, "444")
        self.assertNotContains(response, "333")

    def test_search_filters_by_isbn_or_title(self):
        WholesalerFeedItem.objects.create(
            owner=self.user, account=self.account, supplier=self.supplier, isbn="111", title="Matching Title",
        )
        WholesalerFeedItem.objects.create(
            owner=self.user, account=self.account, supplier=self.supplier, isbn="222", title="Other",
        )

        response = self.client.get(reverse("wholesaler_feed_list"), {"q": "Matching"})
        self.assertContains(response, "111")
        self.assertNotContains(response, "222")

    def test_list_requires_permission(self):
        other = get_user_model().objects.create_user(username="nopower_feedlist", password="pass1234")
        AccountMembership.objects.create(account=self.account, user=other, role=AccountMembership.ROLE_VIEWER)
        self.client.force_login(other)

        response = self.client.get(reverse("wholesaler_feed_list"))
        self.assertEqual(response.status_code, 403)

    def test_upload_link_hidden_without_add_permission(self):
        response = self.client.get(reverse("wholesaler_feed_list"))
        self.assertNotContains(response, reverse("wholesaler_feed_upload"))
