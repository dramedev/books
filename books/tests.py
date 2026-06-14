import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from . import ai_chat
from .models import Author, Book, Category, Sale


def grant(user, *codenames):
    permissions = Permission.objects.filter(
        content_type__app_label="books",
        codename__in=codenames,
    )
    user.user_permissions.add(*permissions)


class AuthorModelTests(TestCase):

    def test_str_returns_name(self):
        author = Author.objects.create(name="Jane Doe")
        self.assertEqual(str(author), "Jane Doe")

    def test_name_is_unique(self):
        Author.objects.create(name="Jane Doe")

        with self.assertRaises(Exception):
            Author.objects.create(name="Jane Doe")

    def test_ordering_by_name(self):
        Author.objects.create(name="Zed")
        Author.objects.create(name="Amy")

        names = list(Author.objects.values_list("name", flat=True))
        self.assertEqual(names, ["Amy", "Zed"])


class BookModelTests(TestCase):

    def setUp(self):
        self.category = Category.objects.create(name="Fiction")

    def _make_book(self, stock_on_hand, reorder_threshold):
        return Book.objects.create(
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
        category = Category.objects.create(name="Fiction")
        self.book = Book.objects.create(
            title="Test Book",
            publisher="Acme",
            published_date=date(2024, 1, 1),
            category=category,
            distribution_expense=Decimal("10.00"),
        )

    def test_revenue_is_quantity_times_unit_price(self):
        sale = Sale.objects.create(
            book=self.book,
            quantity=3,
            unit_price=Decimal("12.50"),
            sale_date=date(2024, 1, 10),
        )
        self.assertEqual(sale.revenue, Decimal("37.50"))

    def test_ordering_is_most_recent_first(self):
        older = Sale.objects.create(
            book=self.book,
            quantity=1,
            unit_price=Decimal("10.00"),
            sale_date=date(2024, 1, 1),
        )
        newer = Sale.objects.create(
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

        self.category = Category.objects.create(name="Fiction")
        self.book = Book.objects.create(
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
            book=self.book,
            quantity=2,
            unit_price=Decimal("10.00"),
            sale_date=date(2024, 1, 5),
        )
        Sale.objects.create(
            book=self.book,
            quantity=3,
            unit_price=Decimal("5.00"),
            sale_date=date(2024, 1, 6),
        )

        response = self.client.get(reverse("book_detail", args=[self.book.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_quantity_sold"], 5)
        self.assertEqual(response.context["total_revenue"], Decimal("35.00"))
        self.assertEqual(len(response.context["sales"]), 2)

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

        category = Category.objects.create(name="Fiction")
        self.low_book = Book.objects.create(
            title="Low Stock Book",
            publisher="Acme",
            published_date=date(2024, 1, 1),
            category=category,
            distribution_expense=Decimal("10.00"),
            stock_on_hand=2,
            reorder_threshold=5,
        )
        self.ok_book = Book.objects.create(
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

        category = Category.objects.create(name="Fiction")
        self.book = Book.objects.create(
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

        self.author = Author.objects.create(name="Jane Doe")

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
        category = Category.objects.create(name="Fiction")
        book = Book.objects.create(
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

        self.category = Category.objects.create(name="Fiction")
        self.book = Book.objects.create(
            title="Test Book",
            publisher="Acme",
            published_date=date(2024, 1, 1),
            category=self.category,
            distribution_expense=Decimal("100.00"),
        )
        Sale.objects.create(
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

        self.fiction = Category.objects.create(name="Fiction")
        self.author = Author.objects.create(name="Jane Doe")

        self.low_book = Book.objects.create(
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
            book=self.low_book,
            quantity=3,
            unit_price=Decimal("10.00"),
            sale_date=date(2024, 1, 15),
        )
        Sale.objects.create(
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
        result = ai_chat.list_books({})
        titles = {book["title"] for book in result["books"]}
        self.assertEqual(titles, {"Low Stock Book", "Well Stocked Book"})

    def test_list_books_filters_by_category(self):
        other = Category.objects.create(name="Non-Fiction")
        Book.objects.create(
            title="Other Category Book",
            isbn="333",
            publisher="Acme",
            published_date=date(2024, 1, 1),
            category=other,
            distribution_expense=Decimal("10.00"),
            stock_on_hand=10,
            reorder_threshold=5,
        )

        result = ai_chat.list_books({"category": "Fiction"})
        titles = {book["title"] for book in result["books"]}
        self.assertEqual(titles, {"Low Stock Book", "Well Stocked Book"})

    def test_list_books_filters_by_author(self):
        result = ai_chat.list_books({"author": "Jane"})
        titles = {book["title"] for book in result["books"]}
        self.assertEqual(titles, {"Low Stock Book"})

    def test_list_books_filters_by_stock_range(self):
        result = ai_chat.list_books({"min_stock": 10})
        titles = {book["title"] for book in result["books"]}
        self.assertEqual(titles, {"Well Stocked Book"})

        result = ai_chat.list_books({"max_stock": 10})
        titles = {book["title"] for book in result["books"]}
        self.assertEqual(titles, {"Low Stock Book"})

    def test_list_books_filters_by_price_range(self):
        Book.objects.create(
            title="Pricey Book",
            isbn="444",
            publisher="Acme",
            published_date=date(2024, 1, 1),
            category=self.fiction,
            distribution_expense=Decimal("50.00"),
            stock_on_hand=5,
            reorder_threshold=5,
        )

        result = ai_chat.list_books({"max_price": 20})
        titles = {book["title"] for book in result["books"]}
        self.assertEqual(titles, {"Low Stock Book", "Well Stocked Book"})

        result = ai_chat.list_books({"min_price": 20})
        titles = {book["title"] for book in result["books"]}
        self.assertEqual(titles, {"Pricey Book"})

    def test_get_low_stock_books(self):
        result = ai_chat.get_low_stock_books({})
        titles = {book["title"] for book in result["books"]}
        self.assertEqual(titles, {"Low Stock Book"})

    def test_search_books_matches_author(self):
        result = ai_chat.search_books({"query": "Jane"})
        titles = {book["title"] for book in result["books"]}
        self.assertEqual(titles, {"Low Stock Book"})

    def test_get_sales_summary_filters_by_date(self):
        result = ai_chat.get_sales_summary({"start_date": "2024-02-01"})
        self.assertEqual(result["total_units_sold"], 1)
        self.assertEqual(result["sale_count"], 1)

    def test_get_top_selling_books(self):
        result = ai_chat.get_top_selling_books({"limit": 1})
        self.assertEqual(len(result["books"]), 1)
        self.assertEqual(result["books"][0]["title"], "Low Stock Book")
        self.assertEqual(result["books"][0]["units_sold"], 3)

    def test_get_categories(self):
        result = ai_chat.get_categories({})
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
        self.assertEqual(
            viewer_codenames,
            {"view_book", "view_category", "view_author", "view_sale"},
        )
