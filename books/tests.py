import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import ai_chat
from .models import (
    AccessCode, Author, Book, Category, Customer, Invoice, InvoiceItem, PrintRun,
    Profile, Reorder, Sale, StockAdjustment, Supplier,
)
from .views import _invoice_aging_data, _pl_data


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
        mocked.assert_called_once_with(self.user, "Hi there", [])


class AiChatToolTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="tooluser", password="pass1234")

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
        result = ai_chat.list_books({}, self.user)
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

        result = ai_chat.list_books({"category": "Fiction"}, self.user)
        titles = {book["title"] for book in result["books"]}
        self.assertEqual(titles, {"Low Stock Book", "Well Stocked Book"})

    def test_list_books_filters_by_author(self):
        result = ai_chat.list_books({"author": "Jane"}, self.user)
        titles = {book["title"] for book in result["books"]}
        self.assertEqual(titles, {"Low Stock Book"})

    def test_list_books_filters_by_stock_range(self):
        result = ai_chat.list_books({"min_stock": 10}, self.user)
        titles = {book["title"] for book in result["books"]}
        self.assertEqual(titles, {"Well Stocked Book"})

        result = ai_chat.list_books({"max_stock": 10}, self.user)
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

        result = ai_chat.list_books({"max_price": 20}, self.user)
        titles = {book["title"] for book in result["books"]}
        self.assertEqual(titles, {"Low Stock Book", "Well Stocked Book"})

        result = ai_chat.list_books({"min_price": 20}, self.user)
        titles = {book["title"] for book in result["books"]}
        self.assertEqual(titles, {"Pricey Book"})

    def test_get_low_stock_books(self):
        result = ai_chat.get_low_stock_books({}, self.user)
        titles = {book["title"] for book in result["books"]}
        self.assertEqual(titles, {"Low Stock Book"})

    def test_search_books_matches_author(self):
        result = ai_chat.search_books({"query": "Jane"}, self.user)
        titles = {book["title"] for book in result["books"]}
        self.assertEqual(titles, {"Low Stock Book"})

    def test_get_sales_summary_filters_by_date(self):
        result = ai_chat.get_sales_summary({"start_date": "2024-02-01"}, self.user)
        self.assertEqual(result["total_units_sold"], 1)
        self.assertEqual(result["sale_count"], 1)

    def test_get_top_selling_books(self):
        result = ai_chat.get_top_selling_books({"limit": 1}, self.user)
        self.assertEqual(len(result["books"]), 1)
        self.assertEqual(result["books"][0]["title"], "Low Stock Book")
        self.assertEqual(result["books"][0]["units_sold"], 3)

    def test_get_categories(self):
        result = ai_chat.get_categories({}, self.user)
        self.assertEqual(
            result["categories"],
            [{"name": "Fiction", "book_count": 2}],
        )

    def test_execute_tool_denies_without_permission(self):
        result = ai_chat.execute_tool("get_categories", {}, self.user)
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

        self.assertRedirects(response, reverse("dashboard"))

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
        grant(self.user, "view_customer", "add_customer", "change_customer", "delete_customer")
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
        buckets, grand_total = _invoice_aging_data(self.user)
        self.assertEqual(len(buckets["current"]["invoices"]), 1)
        self.assertEqual(grand_total, Decimal("100.00"))

    def test_overdue_invoice_buckets_by_days_late(self):
        self._invoice(due_date=self.today - timedelta(days=10))
        self._invoice(due_date=self.today - timedelta(days=45))
        self._invoice(due_date=self.today - timedelta(days=90))

        buckets, grand_total = _invoice_aging_data(self.user)
        self.assertEqual(len(buckets["0_30"]["invoices"]), 1)
        self.assertEqual(len(buckets["31_60"]["invoices"]), 1)
        self.assertEqual(len(buckets["60_plus"]["invoices"]), 1)
        self.assertEqual(grand_total, Decimal("300.00"))

    def test_paid_invoices_excluded(self):
        self._invoice(due_date=self.today - timedelta(days=10), status=Invoice.STATUS_PAID)
        buckets, grand_total = _invoice_aging_data(self.user)
        self.assertEqual(grand_total, Decimal("0"))

    def test_invoice_without_due_date_is_current(self):
        self._invoice(due_date=None)
        buckets, grand_total = _invoice_aging_data(self.user)
        self.assertEqual(len(buckets["current"]["invoices"]), 1)

    def test_report_view_accessible(self):
        response = self.client.get(reverse("invoice_aging_report"))
        self.assertEqual(response.status_code, 200)


class ProfitLossReportTests(TestCase):

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="owner", password="pass1234")
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
        rows, totals = _pl_data(self.user, None, None)
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
        rows, totals = _pl_data(self.user, None, None)
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
        rows, totals = _pl_data(self.user, "2024-01-01", "2024-12-31")
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
        rows, totals = _pl_data(self.user, None, None)
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
