import json
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db.models import IntegerField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import Book, Category, Customer, Invoice, RoyaltyPayment, RoyaltyRate, Sale, Supplier
from .reorder_logic import (
    REORDER_COVER_DAYS, REORDER_VELOCITY_WINDOW_DAYS, suggested_reorder_quantity,
)


SYSTEM_PROMPT = """You are the RumiPress Assistant, embedded as a chat widget \
in RumiPress, a Django app for managing a small book publisher's catalog, \
stock and sales.

RumiPress has these sections:
- Dashboard: overview of total books, units sold, low stock count, revenue, \
profit, category charts, sales trend, low-stock books, top sellers and \
recent sales.
- Books: catalog of titles (ISBN, title, subtitle, authors, publisher, \
publish date, category, distribution expense), searchable/filterable and \
exportable to CSV/Excel/PDF.
- Stock: stock on hand and reorder thresholds for every book; books at or \
below their threshold are flagged "Low stock".
- Categories: groupings used to organize books for filtering and reporting.
- Authors: authors linked to books, with a count of books per author.
- Sales: every sale transaction (book, quantity, unit price, revenue, date, \
channel); recording a sale reduces stock and is refused if it would exceed \
available stock. Exportable to CSV/Excel/PDF.
- Reports: filterable distribution report (expense, revenue, profit) with \
charts and a sales trend, exportable to CSV/Excel/PDF.
- Profile: each user can upload a profile photo; access to sections is \
controlled by permissions.
- Reorders: tracks purchase orders to suppliers (pending/ordered/received), \
with reorder suggestions based on stock and recent sales velocity.
- Suppliers: name, contact, email and phone for each supplier used on reorders.
- Customers: saved billing contacts, each with their own invoice history and \
running balance.
- Invoices: draft/sent/paid billing documents per customer, with a due date; \
unpaid invoices past their due date are "overdue".
- Royalties: a per-book, per-author royalty rate (% of revenue); royalty \
payments record what's actually been paid out, so an author can be owed \
money even with no unpaid invoices involved.

For general questions about how the app works, answer directly from this \
description. For questions about the user's actual books, stock or sales, \
use the provided tools to look up real data rather than guessing. If a tool \
is not available to you, it means the current user doesn't have permission \
to view that data - tell them so. Keep answers concise.

You can suggest what to reorder and why (get_reorder_suggestions), point out \
slow-moving stock (get_slow_moving_books), and draft a reorder email to a \
supplier (draft_supplier_email - call get_reorder_suggestions first to pick \
which books/quantities to include). You never create a reorder or send an \
email yourself - always hand off to the user to take the actual action. When \
a tool result includes a "reorder_url", mention the action as a markdown \
link, e.g. [Create reorder](/reorders/add/3/), so the user can click through.

If a request is ambiguous - a customer/supplier/author name you weren't \
given, or one that matches nothing - ask a short clarifying question instead \
of guessing or calling a tool with empty/made-up input."""


TOOL_SPECS = [
    {
        "name": "get_dashboard_overview",
        "description": (
            "Get overall catalog totals: number of books, units sold, "
            "number of low-stock books, total revenue and total profit."
        ),
        "input_schema": {"type": "object", "properties": {}},
        "permission": "books.view_book",
    },
    {
        "name": "list_books",
        "description": (
            "List books in the catalog, with category, authors, stock on "
            "hand, reorder threshold and distribution expense. Supports "
            "optional filters by category, author, stock on hand range and "
            "distribution expense (price) range. Use this to browse, count "
            "or filter the catalog, e.g. 'what books do you have', 'books "
            "by Jane Doe', 'books under $20', 'books with more than 100 in "
            "stock'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Only include books in this category (optional).",
                },
                "author": {
                    "type": "string",
                    "description": "Only include books with an author whose name contains this text (optional).",
                },
                "min_stock": {
                    "type": "integer",
                    "description": "Only include books with at least this much stock on hand (optional).",
                },
                "max_stock": {
                    "type": "integer",
                    "description": "Only include books with at most this much stock on hand (optional).",
                },
                "min_price": {
                    "type": "number",
                    "description": "Only include books with a distribution expense (price) at least this much (optional).",
                },
                "max_price": {
                    "type": "number",
                    "description": "Only include books with a distribution expense (price) at most this much (optional).",
                },
            },
        },
        "permission": "books.view_book",
    },
    {
        "name": "search_books",
        "description": (
            "Search the book catalog by title, subtitle, author name or "
            "ISBN. Returns matching books with their category, stock on "
            "hand, reorder threshold and distribution expense."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text to search for in title, subtitle, author or ISBN.",
                },
            },
            "required": ["query"],
        },
        "permission": "books.view_book",
    },
    {
        "name": "get_low_stock_books",
        "description": (
            "List books whose stock on hand is at or below their reorder "
            "threshold."
        ),
        "input_schema": {"type": "object", "properties": {}},
        "permission": "books.view_book",
    },
    {
        "name": "get_sales_summary",
        "description": (
            "Get total quantity sold and total revenue, optionally limited "
            "to a date range."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {
                    "type": "string",
                    "description": "Earliest sale date to include, as YYYY-MM-DD (optional).",
                },
                "end_date": {
                    "type": "string",
                    "description": "Latest sale date to include, as YYYY-MM-DD (optional).",
                },
            },
        },
        "permission": "books.view_sale",
    },
    {
        "name": "get_top_selling_books",
        "description": (
            "List the best-selling books by total quantity sold, optionally "
            "limited to a date range."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of books to return (default 5).",
                },
                "start_date": {
                    "type": "string",
                    "description": "Earliest sale date to include, as YYYY-MM-DD (optional).",
                },
                "end_date": {
                    "type": "string",
                    "description": "Latest sale date to include, as YYYY-MM-DD (optional).",
                },
            },
        },
        "permission": "books.view_sale",
    },
    {
        "name": "get_categories",
        "description": "List all categories with the number of books in each.",
        "input_schema": {"type": "object", "properties": {}},
        "permission": "books.view_category",
    },
    {
        "name": "get_reorder_suggestions",
        "description": (
            "List books that should be reordered soon, with reasoning: "
            "current stock, daily sales velocity, estimated days of stock "
            "remaining, and a suggested reorder quantity. Each result "
            "includes a reorder_url the user can follow to actually create "
            "the reorder. Use this for questions like 'what should I "
            "reorder', 'what's about to run out', or 'how much should I "
            "order of X'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of suggestions to return (optional).",
                },
            },
        },
        "permission": "books.view_reorder",
    },
    {
        "name": "get_slow_moving_books",
        "description": (
            "List books in stock that haven't sold at all in the last 30 "
            "days, sorted by how much capital is tied up in their stock "
            "(highest first). Use this for questions like 'what's not "
            "selling', 'what's been sitting on the shelf', or 'what should "
            "I discount or return'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of books to return (optional).",
                },
            },
        },
        "permission": "books.view_sale",
    },
    {
        "name": "draft_supplier_email",
        "description": (
            "Draft (but do not send) a reorder email to a supplier, given "
            "the supplier's name and a list of books with quantities. Call "
            "get_reorder_suggestions first to decide which books and "
            "quantities to include, then pass them here. Returns subject/"
            "body text and the supplier's email address for the user to "
            "review and send themselves - this tool never sends anything."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "supplier_name": {
                    "type": "string",
                    "description": "Name (or partial name) of the supplier to draft the email to.",
                },
                "items": {
                    "type": "array",
                    "description": "Books to request, each with a title and quantity.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "quantity": {"type": "integer"},
                        },
                        "required": ["title", "quantity"],
                    },
                },
            },
            "required": ["supplier_name", "items"],
        },
        "permission": "books.view_supplier",
    },
    {
        "name": "get_overdue_invoices",
        "description": (
            "List unpaid invoices that are past their due date, with "
            "customer name, invoice number, due date, amount and how many "
            "days overdue, sorted most-overdue first. Use this for "
            "questions like 'what invoices are overdue' or 'who owes me "
            "money that's late'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of invoices to return (optional).",
                },
            },
        },
        "permission": "books.view_invoice",
    },
    {
        "name": "get_customer_balance",
        "description": (
            "Get a customer's billing summary: total billed, outstanding "
            "(unpaid) balance per currency, and how many of their invoices "
            "are overdue. Use this for questions like 'how much does X owe "
            "me' or 'what's the balance for customer X'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_name": {
                    "type": "string",
                    "description": "Name (or partial name) of the customer.",
                },
            },
            "required": ["customer_name"],
        },
        "permission": "books.view_customer",
    },
    {
        "name": "get_royalty_summary",
        "description": (
            "Get royalty totals per author: how much they've earned (based "
            "on their royalty rate and book revenue), how much has actually "
            "been paid out, and the outstanding amount still owed. Use this "
            "for questions like 'what do I owe in royalties' or 'how much "
            "does author X still owe/earn'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "author_name": {
                    "type": "string",
                    "description": "Only include this author (optional - omit for all authors with a royalty rate).",
                },
            },
        },
        "permission": "books.view_royaltyrate",
    },
]


def _book_summary(book):
    return {
        "title": book.title,
        "isbn": book.isbn,
        "category": book.category.name,
        "authors": [author.name for author in book.authors.all()],
        "stock_on_hand": book.stock_on_hand,
        "reorder_threshold": book.reorder_threshold,
        "is_low_stock": book.is_low_stock,
        "distribution_expense": str(book.distribution_expense),
    }


def get_dashboard_overview(_input, account):
    books = Book.objects.filter(account=account)
    sales = Sale.objects.filter(account=account)

    total_units_sold = sales.aggregate(total=Sum("quantity"))["total"] or 0
    total_revenue = sum((sale.revenue for sale in sales), start=0)
    total_expense = sum((book.distribution_expense for book in books), start=0)

    return {
        "total_books": books.count(),
        "total_units_sold": total_units_sold,
        "low_stock_count": sum(1 for book in books if book.is_low_stock),
        "total_revenue": str(total_revenue),
        "total_profit": str(total_revenue - total_expense),
    }


def list_books(tool_input, account):
    books = Book.objects.filter(account=account)

    category = (tool_input.get("category") or "").strip()
    if category:
        books = books.filter(category__name__iexact=category)

    author = (tool_input.get("author") or "").strip()
    if author:
        books = books.filter(authors__name__icontains=author)

    if tool_input.get("min_stock") is not None:
        books = books.filter(stock_on_hand__gte=tool_input["min_stock"])
    if tool_input.get("max_stock") is not None:
        books = books.filter(stock_on_hand__lte=tool_input["max_stock"])

    if tool_input.get("min_price") is not None:
        books = books.filter(distribution_expense__gte=tool_input["min_price"])
    if tool_input.get("max_price") is not None:
        books = books.filter(distribution_expense__lte=tool_input["max_price"])

    return {"books": [_book_summary(book) for book in books.distinct()]}


def search_books(tool_input, account):
    query = (tool_input.get("query") or "").strip()
    if not query:
        return {"books": []}

    books = (
        Book.objects.filter(account=account)
        .filter(
            Q(title__icontains=query)
            | Q(subtitle__icontains=query)
            | Q(isbn__icontains=query)
            | Q(authors__name__icontains=query)
        )
        .distinct()[:20]
    )
    return {"books": [_book_summary(book) for book in books]}


def get_low_stock_books(_input, account):
    books = [book for book in Book.objects.filter(account=account) if book.is_low_stock]
    return {"books": [_book_summary(book) for book in books]}


def _parse_date(value):
    if not value:
        return None
    from datetime import date

    return date.fromisoformat(value)


def get_sales_summary(tool_input, account):
    sales = Sale.objects.filter(account=account)

    start_date = _parse_date(tool_input.get("start_date"))
    end_date = _parse_date(tool_input.get("end_date"))
    if start_date:
        sales = sales.filter(sale_date__gte=start_date)
    if end_date:
        sales = sales.filter(sale_date__lte=end_date)

    total_units = sales.aggregate(total=Sum("quantity"))["total"] or 0
    total_revenue = sum((sale.revenue for sale in sales), start=0)

    return {
        "start_date": tool_input.get("start_date"),
        "end_date": tool_input.get("end_date"),
        "total_units_sold": total_units,
        "total_revenue": str(total_revenue),
        "sale_count": sales.count(),
    }


def get_top_selling_books(tool_input, account):
    sales = Sale.objects.filter(account=account)

    start_date = _parse_date(tool_input.get("start_date"))
    end_date = _parse_date(tool_input.get("end_date"))
    if start_date:
        sales = sales.filter(sale_date__gte=start_date)
    if end_date:
        sales = sales.filter(sale_date__lte=end_date)

    limit = tool_input.get("limit") or 5

    totals = {}
    for sale in sales.select_related("book"):
        entry = totals.setdefault(
            sale.book.title, {"title": sale.book.title, "units_sold": 0, "revenue": 0}
        )
        entry["units_sold"] += sale.quantity
        entry["revenue"] += sale.revenue

    ranked = sorted(totals.values(), key=lambda entry: entry["units_sold"], reverse=True)
    ranked = ranked[:limit]
    for entry in ranked:
        entry["revenue"] = str(entry["revenue"])

    return {"books": ranked}


def get_categories(_input, account):
    categories = Category.objects.filter(account=account)
    return {
        "categories": [
            {"name": category.name, "book_count": category.book_set.count()}
            for category in categories
        ]
    }


def _recent_sales_annotation():
    cutoff = timezone.now().date() - timedelta(days=REORDER_VELOCITY_WINDOW_DAYS)
    recent_sales = (
        Sale.objects.filter(book=OuterRef("pk"), sale_date__gte=cutoff)
        .values("book")
        .annotate(total=Sum("quantity"))
        .values("total")
    )
    return Coalesce(
        Subquery(recent_sales, output_field=IntegerField()),
        Value(0, output_field=IntegerField()),
    )


def get_reorder_suggestions(tool_input, account):
    books = Book.objects.filter(account=account).annotate(
        units_sold_recent=_recent_sales_annotation()
    )

    suggestions = []
    for book in books:
        velocity = book.units_sold_recent / REORDER_VELOCITY_WINDOW_DAYS
        days_of_stock = book.stock_on_hand / velocity if velocity > 0 else None
        needs_reorder = book.is_low_stock or (
            days_of_stock is not None and days_of_stock <= REORDER_COVER_DAYS
        )
        if not needs_reorder:
            continue

        suggestions.append({
            "title": book.title,
            "isbn": book.isbn,
            "stock_on_hand": book.stock_on_hand,
            "daily_sales_velocity": round(velocity, 2),
            "days_of_stock": round(days_of_stock, 1) if days_of_stock is not None else None,
            "suggested_quantity": suggested_reorder_quantity(book, velocity=velocity),
            "reorder_url": f"/reorders/add/{book.id}/",
        })

    suggestions.sort(
        key=lambda item: item["days_of_stock"] if item["days_of_stock"] is not None else -1
    )

    limit = tool_input.get("limit")
    if limit:
        suggestions = suggestions[:limit]

    return {"suggestions": suggestions}


def get_slow_moving_books(tool_input, account):
    books = (
        Book.objects.filter(account=account, stock_on_hand__gt=0)
        .annotate(units_sold_recent=_recent_sales_annotation())
        .filter(units_sold_recent=0)
    )

    results = [
        {
            "title": book.title,
            "isbn": book.isbn,
            "stock_on_hand": book.stock_on_hand,
            "stock_value": book.stock_on_hand * book.distribution_expense,
        }
        for book in books
    ]
    results.sort(key=lambda item: item["stock_value"], reverse=True)

    limit = tool_input.get("limit")
    if limit:
        results = results[:limit]

    for item in results:
        item["stock_value"] = str(item["stock_value"])

    return {"books": results}


def draft_supplier_email(tool_input, account):
    supplier_name = (tool_input.get("supplier_name") or "").strip()
    if not supplier_name:
        return {"error": "supplier_name is required."}

    supplier = Supplier.objects.filter(account=account, name__icontains=supplier_name).first()
    if supplier is None:
        return {"error": f"No supplier found matching '{supplier_name}'."}

    lines = []
    for item in tool_input.get("items") or []:
        title = (item.get("title") or "").strip()
        quantity = item.get("quantity")
        if not title or not quantity:
            continue
        book = Book.objects.filter(account=account, title__icontains=title).first()
        if book is None:
            continue
        lines.append(f"- {book.title} (ISBN {book.isbn or 'n/a'}): {quantity} units")

    if not lines:
        return {"error": "No matching books found for the requested items."}

    contact = supplier.contact_name or supplier.name
    subject = f"Reorder request - {len(lines)} title(s)"
    body = (
        f"Hi {contact},\n\n"
        "We'd like to place a reorder for the following titles:\n\n"
        + "\n".join(lines)
        + "\n\nPlease let us know expected availability and pricing.\n\nThanks!"
    )

    return {
        "supplier_name": supplier.name,
        "supplier_email": supplier.email,
        "subject": subject,
        "body": body,
        "note": "" if supplier.email else "No email on file for this supplier - share this draft manually.",
    }


def get_overdue_invoices(tool_input, account):
    invoices = Invoice.objects.filter(account=account).exclude(status=Invoice.STATUS_PAID)
    today = timezone.now().date()

    overdue = []
    for invoice in invoices:
        if not invoice.is_overdue:
            continue
        overdue.append({
            "customer_name": invoice.customer_name,
            "invoice_number": invoice.invoice_number,
            "due_date": invoice.due_date.isoformat(),
            "days_overdue": (today - invoice.due_date).days,
            "grand_total": str(invoice.grand_total),
            "currency": invoice.currency,
        })

    overdue.sort(key=lambda item: item["days_overdue"], reverse=True)

    limit = tool_input.get("limit")
    if limit:
        overdue = overdue[:limit]

    return {"invoices": overdue}


def get_customer_balance(tool_input, account):
    customer_name = (tool_input.get("customer_name") or "").strip()
    if not customer_name:
        return {"error": "customer_name is required."}

    customer = Customer.objects.filter(account=account, name__icontains=customer_name).first()
    if customer is None:
        return {"error": f"No customer found matching '{customer_name}'."}

    billed_by_currency = {}
    outstanding_by_currency = {}
    overdue_count = 0

    for invoice in customer.invoices.filter(account=account):
        billed_by_currency[invoice.currency] = (
            billed_by_currency.get(invoice.currency, 0) + invoice.grand_total
        )
        if invoice.status != Invoice.STATUS_PAID:
            outstanding_by_currency[invoice.currency] = (
                outstanding_by_currency.get(invoice.currency, 0) + invoice.grand_total
            )
        if invoice.is_overdue:
            overdue_count += 1

    return {
        "customer_name": customer.name,
        "billed_by_currency": {k: str(v) for k, v in billed_by_currency.items()},
        "outstanding_by_currency": {k: str(v) for k, v in outstanding_by_currency.items()},
        "overdue_count": overdue_count,
    }


def get_royalty_summary(tool_input, account):
    author_name = (tool_input.get("author_name") or "").strip()

    rates = RoyaltyRate.objects.filter(account=account).select_related("book", "author")
    if author_name:
        rates = rates.filter(author__name__icontains=author_name)

    sales = Sale.objects.filter(account=account)
    revenue_by_book = {}
    for sale in sales:
        revenue_by_book[sale.book_id] = revenue_by_book.get(sale.book_id, 0) + sale.revenue

    earned_by_author = {}
    for rate in rates:
        revenue = revenue_by_book.get(rate.book_id, 0)
        earned_by_author[rate.author.name] = (
            earned_by_author.get(rate.author.name, 0) + revenue * rate.rate / 100
        )

    payments = RoyaltyPayment.objects.filter(account=account)
    if author_name:
        payments = payments.filter(author__name__icontains=author_name)
    paid_by_author = {}
    for payment in payments:
        paid_by_author[payment.author.name] = (
            paid_by_author.get(payment.author.name, 0) + payment.amount
        )

    cents = Decimal("0.01")
    authors = sorted(set(earned_by_author) | set(paid_by_author))
    summary = []
    for author in authors:
        earned = Decimal(earned_by_author.get(author, 0)).quantize(cents, rounding=ROUND_HALF_UP)
        paid = Decimal(paid_by_author.get(author, 0)).quantize(cents, rounding=ROUND_HALF_UP)
        summary.append({
            "author": author,
            "total_earned": str(earned),
            "total_paid": str(paid),
            "outstanding": str(earned - paid),
        })

    return {"authors": summary}


TOOL_FUNCTIONS = {
    "get_dashboard_overview": get_dashboard_overview,
    "list_books": list_books,
    "search_books": search_books,
    "get_low_stock_books": get_low_stock_books,
    "get_sales_summary": get_sales_summary,
    "get_top_selling_books": get_top_selling_books,
    "get_categories": get_categories,
    "get_reorder_suggestions": get_reorder_suggestions,
    "get_slow_moving_books": get_slow_moving_books,
    "draft_supplier_email": draft_supplier_email,
    "get_overdue_invoices": get_overdue_invoices,
    "get_customer_balance": get_customer_balance,
    "get_royalty_summary": get_royalty_summary,
}


def build_tools_for_user(user):
    return [
        {key: value for key, value in spec.items() if key != "permission"}
        for spec in TOOL_SPECS
        if user.has_perm(spec["permission"])
    ]


def execute_tool(name, tool_input, user, account):
    spec = next((spec for spec in TOOL_SPECS if spec["name"] == name), None)
    if spec is None or not user.has_perm(spec["permission"]):
        return {"error": "You don't have permission to access this information."}

    return TOOL_FUNCTIONS[name](tool_input, account)


NOT_CONFIGURED_REPLY = (
    "The AI assistant isn't configured yet. Ask an administrator to set "
    "ANTHROPIC_API_KEY for this app."
)

MAX_TOOL_ITERATIONS = 4


def get_chat_reply(user, account, message, history):
    if not settings.ANTHROPIC_API_KEY:
        return NOT_CONFIGURED_REPLY, history

    import anthropic

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    tools = build_tools_for_user(user)

    messages = list(history) + [{"role": "user", "content": message}]

    for _ in range(MAX_TOOL_ITERATIONS):
        response = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=messages,
            tools=tools,
        )

        content_blocks = [block.model_dump() for block in response.content]
        messages.append({"role": "assistant", "content": content_blocks})

        if response.stop_reason != "tool_use":
            text = "".join(
                block.text for block in response.content if block.type == "text"
            )
            return text, messages

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            result = execute_tool(block.name, block.input, user, account)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                }
            )

        messages.append({"role": "user", "content": tool_results})

    return (
        "Sorry, I couldn't finish looking that up. Please try asking again.",
        messages,
    )
