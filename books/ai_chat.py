import json

from django.conf import settings
from django.db.models import Q, Sum

from .models import Book, Category, Sale


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

For general questions about how the app works, answer directly from this \
description. For questions about the user's actual books, stock or sales, \
use the provided tools to look up real data rather than guessing. If a tool \
is not available to you, it means the current user doesn't have permission \
to view that data - tell them so. Keep answers concise."""


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


def get_dashboard_overview(_input):
    books = Book.objects.all()
    sales = Sale.objects.all()

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


def list_books(tool_input):
    books = Book.objects.all()

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


def search_books(tool_input):
    query = (tool_input.get("query") or "").strip()
    if not query:
        return {"books": []}

    books = (
        Book.objects.filter(
            Q(title__icontains=query)
            | Q(subtitle__icontains=query)
            | Q(isbn__icontains=query)
            | Q(authors__name__icontains=query)
        )
        .distinct()[:20]
    )
    return {"books": [_book_summary(book) for book in books]}


def get_low_stock_books(_input):
    books = [book for book in Book.objects.all() if book.is_low_stock]
    return {"books": [_book_summary(book) for book in books]}


def _parse_date(value):
    if not value:
        return None
    from datetime import date

    return date.fromisoformat(value)


def get_sales_summary(tool_input):
    sales = Sale.objects.all()

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


def get_top_selling_books(tool_input):
    sales = Sale.objects.all()

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


def get_categories(_input):
    categories = Category.objects.all()
    return {
        "categories": [
            {"name": category.name, "book_count": category.book_set.count()}
            for category in categories
        ]
    }


TOOL_FUNCTIONS = {
    "get_dashboard_overview": get_dashboard_overview,
    "list_books": list_books,
    "search_books": search_books,
    "get_low_stock_books": get_low_stock_books,
    "get_sales_summary": get_sales_summary,
    "get_top_selling_books": get_top_selling_books,
    "get_categories": get_categories,
}


def build_tools_for_user(user):
    return [
        {key: value for key, value in spec.items() if key != "permission"}
        for spec in TOOL_SPECS
        if user.has_perm(spec["permission"])
    ]


def execute_tool(name, tool_input, user):
    spec = next((spec for spec in TOOL_SPECS if spec["name"] == name), None)
    if spec is None or not user.has_perm(spec["permission"]):
        return {"error": "You don't have permission to access this information."}

    return TOOL_FUNCTIONS[name](tool_input)


NOT_CONFIGURED_REPLY = (
    "The AI assistant isn't configured yet. Ask an administrator to set "
    "ANTHROPIC_API_KEY for this app."
)

MAX_TOOL_ITERATIONS = 4


def get_chat_reply(user, message, history):
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
            result = execute_tool(block.name, block.input, user)
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
