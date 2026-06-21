import base64
import hashlib
import hmac
import json
import stripe
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import ai_chat, iyzico_client
from .models import (
    AccessCode, AVATAR_MAX_SIZE_BYTES, Account, AccountInvitation, AccountMembership, Author, Book, Category,
    Customer, CustomerLoginToken, get_or_create_account_for_user,
    Integration, Invoice, InvoiceItem, Location, PrintRun, Profile, Reorder, RoyaltyPayment,
    Sale, StockAdjustment, Subscription, Supplier, validate_avatar_size,
)
from .views import _adjust_stock, _invoice_aging_data, _pl_data, _safe_json


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


class AccessCodeRedemptionTests(TestCase):

    def setUp(self):
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
        grant(self.user, "view_printrun", "add_printrun", "change_printrun", "delete_printrun")

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
