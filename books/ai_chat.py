import json
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db.models import Count, IntegerField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .analytics import PURCHASE_COST_EXPRESSION, REVENUE_EXPRESSION
from .models import (
    Book, Category, Customer, Invoice, PrintRun, Return, RoyaltyPayment, RoyaltyRate, Sale, SaleTransaction,
    StockLevel, Supplier,
)
from .reorder_logic import (
    REORDER_COVER_DAYS, REORDER_VELOCITY_WINDOW_DAYS, suggested_reorder_quantity,
)


SYSTEM_PROMPT = """You are the RumiPress Assistant, embedded as a chat widget \
in RumiPress, a Django app for managing a small book publisher's catalog, \
stock and sales.

Formatting: you're rendered in a narrow chat panel that only supports plain \
text and one special pattern, [label](/path/) for a clickable link - it does \
NOT render markdown tables, headers (###), bold (**text**), or emoji into \
anything but literal characters, so avoid all of those. Write in short plain \
sentences or simple "- " dashed lines instead of tables/headers/bullet \
symbols. For lists of items (books, invoices, customers, etc.), use one \
dashed line per item with the key facts inline, e.g. "- The Last Lighthouse: \
2 in stock, threshold 10" rather than a table.

When a user asks "how do I do X" or "how does X work", walk them through the \
real steps below - which screen to open, which button to click, in order - \
not just a one-line description of the feature. Use the dashed-line style \
above for the steps themselves. Only describe the parts of a flow relevant \
to their question; you don't need to repeat the whole section every time.

Dashboard ([Dashboard](/)): the home page after login. Shows total books, \
units sold, low stock count, revenue, profit, a category revenue/profit \
chart, a sales trend chart (3/6/12 months or all time), a sales-by-channel \
breakdown, low-stock books, top sellers and recent sales. This is the best \
starting page any time someone asks "where do I see X".

Adding/managing books (sidebar: Catalog > Books, [Books](/books/)):
- To add one book by hand: click "Add Book" on the Books list, fill in \
title/subtitle/authors/publisher/category/ISBN/etc., save.
- To skip typing details: on the Add/Edit Book form, click "Scan" to open \
the camera and scan the ISBN barcode, or click "Look up" after typing/scanning \
an ISBN to auto-fill title, subtitle, publisher, publish date, authors and \
cover art from the ISBN. Look up only fills in fields that are still empty, \
so it never overwrites something already typed.
- To add many books at once: use "Import CSV" on the Books list - download \
the template first to see the expected columns, then upload a filled-in \
file. Rows with a bad/missing date or category are skipped individually \
with an error shown, so one bad row doesn't fail the whole import.
- Each book's detail page shows its full stock history (sales, returns, \
adjustments) and a "Wholesale Availability" panel comparing price/stock \
across suppliers who've uploaded a feed for that ISBN (see Wholesaler feed \
below).
- "Adjust Stock" on a book's detail page is for manual corrections (e.g. \
damaged copies, inventory recount) - it's logged with a reason, separate \
from sales/returns/print runs which adjust stock automatically.

Stock & low stock (sidebar: Catalog > Stock, [Stock](/stock/)): shows stock \
on hand and reorder threshold per book; anything at or below its threshold \
is flagged "Low stock". A digest email of low-stock books goes out \
automatically; there's no manual "send digest" button.

Categories and Authors (sidebar: Catalog): simple add/edit/delete lists used \
to organize and filter books elsewhere - no special workflow beyond that.

Recording sales - there are two different ways, and which one to use matters:
- Sales ([Sales](/sales/)): for entering a single past/individual sale by \
hand (one book, one form) - used for backfilling data or sales made outside \
a normal counter transaction, e.g. through Shopify.
- Checkout ([Checkout](/checkout/)): the real point-of-sale flow for ringing \
up a customer at the counter, used for most day-to-day in-person sales. \
Steps: search a book by title/ISBN or click "Scan" to scan its barcode, it's \
added to the cart; adjust quantity/price/tax per line if needed; pick a \
customer, location, payment method and currency; review the live total; \
click to complete the sale. This creates one transaction covering every line \
together (a receipt number, not separate Sale rows entered one at a time), \
and checks stock for every line before committing anything - if one line \
doesn't have enough stock, nothing in the cart is sold. After completing, \
you land on a printable/downloadable PDF receipt with the account's logo and \
brand color (if set, see Account settings below).
- [Transaction history](/checkout/history/) lists past checkout transactions \
(searchable by receipt number or customer, exportable), with a link back to \
each receipt. "Void transaction" on a receipt refunds every line in that \
transaction in one step (it reuses the same accounting as Returns below, so \
refunded checkout sales show up correctly everywhere stock/revenue is \
reported).

Returns ([Returns](/returns/)): for refunding part of an individual Sale \
(not a whole checkout transaction) - pick the original sale, enter a reason \
and refund amount, and it's recorded with stock adjusted accordingly.

Reorders & Suppliers (sidebar: Catalog > Suppliers, [Reorders](/reorders/)):
- [Suppliers](/suppliers/): add a supplier's name/contact/email/phone first - \
reorders are placed against a supplier.
- [Suggestions](/reorders/suggestions/) on the Reorders page recommends what \
to reorder and how much, based on current stock and recent sales velocity - \
use the "Create reorder" link there (or ask me, since get_reorder_suggestions \
does the same calculation) rather than guessing quantities by hand.
- Creating a reorder records book/supplier/quantity/unit cost with a \
pending/ordered/received status. "Mark Received" on a reorder automatically \
adds the quantity to stock and logs a stock adjustment - you don't need to \
also manually adjust stock after receiving a reorder.

Wholesaler feed (sidebar: Catalog > Wholesaler Feed, \
[Wholesaler feed](/wholesaler-feed/)): a way to track a supplier's catalog \
price/stock per ISBN without a real distributor API integration. Upload a \
CSV with isbn/title/price/stock columns for one supplier at a time; check \
"Replace this supplier's existing feed entries" if the file is that \
supplier's full current catalog (this deletes that supplier's old rows \
first, so discontinued items don't linger forever) - leave it unchecked for \
a partial/incremental update. The book detail page then shows every \
supplier's price for that ISBN side by side, cheapest first.

Customers & Invoices (sidebar: Billing):
- [Customers](/customers/): add a customer's contact info first; their \
detail page shows total billed, outstanding balance and their full invoice \
history.
- [Invoices](/invoices/): create an invoice for a customer, add line items, \
set a due date. "Mark Sent" emails the customer (with a portal login link if \
they have a Customer record - see below) and "Mark Paid" records payment; \
both exist as bulk actions too (checkboxes + select-all) for handling \
several invoices at once. Unpaid invoices past their due date are flagged \
"overdue" automatically. [Invoice Aging](/reports/invoice-aging/) buckets \
outstanding invoices by how overdue they are (current/1-30/31-60/60+ days).
- Customers can also pay online and see their own invoices without a staff \
login, through a separate magic-link portal (no password) - that's not \
something staff configure per-customer, it just works once "Mark Sent" has \
been used on an invoice tied to a Customer record.

Royalties (sidebar: Production > Royalties, [Royalties](/royalties/)): set a \
royalty rate (% of revenue) per book/author first. [Royalty \
report](/royalties/report/) shows what's been earned from sales vs. what's \
actually been paid - an author can be "owed" money with no unpaid invoices \
involved, since royalties are tracked separately from invoicing. \
[Royalty payments](/royalties/payments/) is where you record an actual \
payout once made.

Print runs (sidebar: Production > Print Runs, [Print runs](/print-runs/)): \
record a production batch (book, quantity, cost per unit, run date) as \
pending; you can start one directly from a book's detail page via "New Print \
Run". "Mark Complete" on a pending run adds its quantity to stock \
automatically (logged as a stock adjustment, reason "production") - once \
completed, a run can't be deleted, since that would leave the stock change \
unaccounted for.

Locations & stock transfer (sidebar: Operations > Locations, \
[Locations](/locations/)): only relevant if stock is split across more than \
one physical place (multiple stores/warehouses). Add each location, then use \
[Stock transfer](/stock/transfer/) to move quantity from one location to \
another; each location's own page shows its stock breakdown.

Team & roles (sidebar: Team, [Team](/team/), Admin-only): invite a new \
person by email under "Invite someone" - they get a link (7-day expiry) to \
set their own username/password. Roles are Admin (full access), Staff (can \
do day-to-day work but not delete/manage integrations/royalties/team), or \
Viewer (read-only). An account always needs at least one Admin, so the last \
Admin can't be removed or demoted. The same page's "Account settings" panel \
is where the account's logo, brand color and default tax rate are set - \
logo/brand color apply to invoice PDFs and checkout receipts; an account \
that hasn't set these shows no branding at all rather than a RumiPress logo.

Subscription/billing for the RumiPress account itself (separate from a \
customer's invoices) is at [Manage billing](/billing/start/) or, once \
subscribed, [Manage billing](/billing/portal/) to update the card on file - \
this only matters if the account's subscription is unpaid/inactive, in which \
case the whole account gets blocked until it's resolved.

Profile ([Profile](/profile/)): upload a profile photo, change your email, \
or change your password - three separate forms on one page.

Reports & exports: [Distribution report](/report/) (expense/revenue/profit, \
filterable, with charts) and [Profit & Loss report](/reports/profit-loss/) \
are the two summary reports; almost every list page (books, sales, \
reorders, invoices, checkout history) has its own CSV/Excel/PDF export \
matching whatever filters are currently applied on that page.

Integrations (sidebar: Operations > Integrations, [Integrations](/integrations/), \
Admin-only): connect Shopify (auto-deducts stock when an order comes in) or \
Stripe (lets customers pay invoices online through the portal) by entering \
that service's API credentials here - this is a one-time setup step per \
service, not something done per-sale or per-invoice.

Global search (the search box in the top bar, [Search](/search/)): searches \
books, customers and invoices you have permission to view, all at once, \
capped at 25 results per type - the fastest way to jump straight to a \
specific record by name/title/ISBN instead of browsing a list.

For general "how do I" or "how does X work" questions, answer directly from \
the walkthroughs above. For questions about the user's actual books, stock \
or sales, use the provided tools to look up real data rather than guessing. \
If a tool is not available to you, it means the current user doesn't have \
permission to view that data - tell them so. Keep answers concise, but give \
the full relevant steps when someone asks how to do something - don't \
truncate a real walkthrough just to be brief.

You can suggest what to reorder and why (get_reorder_suggestions), point out \
slow-moving stock (get_slow_moving_books), and draft a reorder email to a \
supplier (draft_supplier_email - call get_reorder_suggestions first to pick \
which books/quantities to include). You never create a reorder or send an \
email yourself - always hand off to the user to take the actual action. When \
a tool result includes a "reorder_url", mention the action as a markdown \
link, e.g. [Create reorder](/reorders/add/3/), so the user can click through.

If a request is ambiguous - a customer/supplier/author name you weren't \
given, or one that matches nothing - ask a short clarifying question instead \
of guessing or calling a tool with empty/made-up input.

For open-ended questions like "how's my business doing" or "what should I \
focus on", lead with get_business_insights - it already combines the trend, \
stock, billing and royalty signals into one prioritized list, so you don't \
need to call every tool individually. For a specific trend, category, or \
customer-ranking question, use get_sales_trend, get_category_performance, or \
get_top_customers directly instead."""


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
    {
        "name": "get_sales_trend",
        "description": (
            "Compare total revenue and units sold over the last N days "
            "against the same-length period before that, with percent "
            "change and a direction ('up'/'down'/'flat'). Use this for "
            "questions like 'is my business growing', 'how do sales this "
            "month compare to last month', or 'are we trending up or down'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Length of the period to compare, in days (default 30).",
                },
            },
        },
        "permission": "books.view_sale",
    },
    {
        "name": "get_category_performance",
        "description": (
            "Revenue and profit per category, sorted by revenue (highest "
            "first). Use this for questions like 'which category makes me "
            "the most money' or 'how is each category performing'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Only include sales from the last N days (optional - omit for all-time).",
                },
            },
        },
        "permission": "books.view_book",
    },
    {
        "name": "get_top_customers",
        "description": (
            "Rank customers by total amount billed across all their "
            "invoices. Use this for questions like 'who are my best "
            "customers' or 'who has bought the most from me'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of customers to return (default 5).",
                },
            },
        },
        "permission": "books.view_customer",
    },
    {
        "name": "get_business_insights",
        "description": (
            "Get a prioritized list of things that need attention right "
            "now - overdue invoices, low stock, slow-moving stock, "
            "outstanding royalties, and the recent sales trend - each with "
            "a headline and why it matters. Use this for open-ended "
            "questions like 'how's my business doing', 'what should I "
            "focus on', or 'give me a health check'."
        ),
        "input_schema": {"type": "object", "properties": {}},
        "permission": "books.view_book",
    },
    {
        "name": "get_recent_transactions",
        "description": (
            "List recent checkout/point-of-sale transactions (receipt "
            "number, date, customer, location, payment method, total, "
            "and refund status). Use this for questions like 'what did "
            "I sell today' or 'show my recent checkout transactions'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of transactions to return (default 10).",
                },
            },
        },
        "permission": "books.view_saletransaction",
    },
    {
        "name": "get_transaction_by_receipt",
        "description": (
            "Get full details of one checkout transaction by its receipt "
            "number, including every line item and its refund status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "receipt_number": {
                    "type": "string",
                    "description": "The receipt number to look up, e.g. 'RCT-2026-0001'.",
                },
            },
            "required": ["receipt_number"],
        },
        "permission": "books.view_saletransaction",
    },
    {
        "name": "get_stock_by_location",
        "description": (
            "Get stock quantities broken down by location (warehouse/store), "
            "optionally filtered to one book. Use this for questions like "
            "'how much stock do we have at each location' or 'where is "
            "this book stocked'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "book_title": {
                    "type": "string",
                    "description": "Optional book title (or partial title) to filter to.",
                },
            },
        },
        "permission": "books.view_stocklevel",
    },
    {
        "name": "get_print_runs",
        "description": (
            "List print runs (book, quantity, cost per unit, total cost, "
            "run date, status). Use this for questions about printing "
            "history or production costs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["pending", "completed"],
                    "description": "Optional status filter.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of print runs to return (default 10).",
                },
            },
        },
        "permission": "books.view_printrun",
    },
    {
        "name": "get_returns_summary",
        "description": (
            "List recent sales returns (book, quantity, reason, refund "
            "amount, date) within a day window. Use this for questions "
            "about returns or refunds."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "How many days back to look (default 30).",
                },
            },
        },
        "permission": "books.view_return",
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
    books = Book.objects.filter(account=account).select_related("category").prefetch_related("authors")

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
        .select_related("category")
        .prefetch_related("authors")
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
    books = Book.objects.filter(account=account).select_related("category").prefetch_related("authors")
    return {"books": [_book_summary(book) for book in books if book.is_low_stock]}


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
    categories = Category.objects.filter(account=account).annotate(book_count=Count("book"))
    return {
        "categories": [
            {"name": category.name, "book_count": category.book_count}
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


def _money(value):
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def get_sales_trend(tool_input, account):
    days = tool_input.get("days") or 30
    today = timezone.now().date()
    current_start = today - timedelta(days=days)
    previous_start = today - timedelta(days=2 * days)

    current = Sale.objects.filter(account=account, sale_date__gte=current_start, sale_date__lt=today)
    previous = Sale.objects.filter(account=account, sale_date__gte=previous_start, sale_date__lt=current_start)

    current_revenue = current.aggregate(total=Sum(REVENUE_EXPRESSION))["total"] or Decimal(0)
    previous_revenue = previous.aggregate(total=Sum(REVENUE_EXPRESSION))["total"] or Decimal(0)
    current_units = current.aggregate(total=Sum("quantity"))["total"] or 0
    previous_units = previous.aggregate(total=Sum("quantity"))["total"] or 0

    if previous_revenue == 0:
        revenue_change = None if current_revenue == 0 else 100.0
    else:
        revenue_change = float((current_revenue - previous_revenue) / previous_revenue * 100)

    direction = "flat"
    if revenue_change is not None:
        if revenue_change > 1:
            direction = "up"
        elif revenue_change < -1:
            direction = "down"

    return {
        "days": days,
        "current_revenue": str(_money(current_revenue)),
        "previous_revenue": str(_money(previous_revenue)),
        "revenue_change_percent": round(revenue_change, 1) if revenue_change is not None else None,
        "current_units": current_units,
        "previous_units": previous_units,
        "direction": direction,
    }


def get_category_performance(tool_input, account):
    days = tool_input.get("days")
    books = Book.objects.filter(account=account)
    sales = Sale.objects.filter(account=account)
    if days:
        cutoff = timezone.now().date() - timedelta(days=days)
        sales = sales.filter(sale_date__gte=cutoff)

    expense_by_category = {
        item["category__name"]: item["expense"] or 0
        for item in books.values("category__name").annotate(expense=Sum("distribution_expense"))
    }
    revenue_by_category = {
        item["book__category__name"]: item["revenue"] or 0
        for item in sales.values("book__category__name").annotate(revenue=Sum(REVENUE_EXPRESSION))
    }

    results = []
    for name in sorted(set(expense_by_category) | set(revenue_by_category)):
        revenue = revenue_by_category.get(name, 0)
        expense = expense_by_category.get(name, 0)
        results.append({
            "category": name,
            "revenue": revenue,
            "profit": revenue - expense,
        })

    results.sort(key=lambda item: item["revenue"], reverse=True)
    for item in results:
        item["revenue"] = str(_money(item["revenue"]))
        item["profit"] = str(_money(item["profit"]))

    return {"categories": results}


def get_top_customers(tool_input, account):
    limit = tool_input.get("limit") or 5
    customers = Customer.objects.filter(account=account).prefetch_related("invoices__items")

    ranked = []
    for customer in customers:
        total_billed = sum((invoice.grand_total for invoice in customer.invoices.all()), Decimal(0))
        if total_billed <= 0:
            continue
        ranked.append({"customer": customer.name, "total_billed": total_billed})

    ranked.sort(key=lambda item: item["total_billed"], reverse=True)
    ranked = ranked[:limit]
    for item in ranked:
        item["total_billed"] = str(item["total_billed"])

    return {"customers": ranked}


def get_business_insights(tool_input, account, user):
    insights = []

    if user.has_perm("books.view_invoice"):
        overdue = get_overdue_invoices({}, account)["invoices"]
        if overdue:
            total = sum((Decimal(item["grand_total"]) for item in overdue), Decimal(0))
            insights.append({
                "headline": f"{len(overdue)} overdue invoice(s) totaling {total}",
                "detail": "Customers who are late paying - following up directly affects cash flow.",
                "severity": "high",
            })

    low_stock = get_low_stock_books({}, account)["books"]
    if low_stock:
        insights.append({
            "headline": f"{len(low_stock)} book(s) at or below their reorder threshold",
            "detail": "Restock soon to avoid running out - see get_reorder_suggestions for quantities.",
            "severity": "high",
        })

    if user.has_perm("books.view_sale"):
        slow = get_slow_moving_books({}, account)["books"]
        if slow:
            tied_up = sum((Decimal(item["stock_value"]) for item in slow), Decimal(0))
            insights.append({
                "headline": f"{len(slow)} book(s) with no recent sales, {tied_up} tied up in stock",
                "detail": "Candidates to discount, promote, or return to free up capital.",
                "severity": "medium",
            })

        trend = get_sales_trend({}, account)
        if trend["revenue_change_percent"] is not None and abs(trend["revenue_change_percent"]) >= 5:
            insights.append({
                "headline": (
                    f"Revenue is {trend['direction']} {abs(trend['revenue_change_percent'])}% "
                    f"vs the prior {trend['days']} days"
                ),
                "detail": "Based on comparing the last two equal-length periods of sales.",
                "severity": "medium" if trend["direction"] == "down" else "low",
            })

    if user.has_perm("books.view_royaltyrate"):
        royalties = get_royalty_summary({}, account)["authors"]
        outstanding_total = sum((Decimal(item["outstanding"]) for item in royalties), Decimal(0))
        if outstanding_total > 0:
            insights.append({
                "headline": f"{outstanding_total} in royalties currently owed",
                "detail": "See get_royalty_summary for the per-author breakdown.",
                "severity": "low",
            })

    severity_order = {"high": 0, "medium": 1, "low": 2}
    insights.sort(key=lambda item: severity_order.get(item["severity"], 3))

    if not insights:
        insights.append({
            "headline": "Nothing urgent right now",
            "detail": "No overdue invoices, low stock, slow movers, or outstanding royalties found.",
            "severity": "low",
        })

    return {"insights": insights}


def get_recent_transactions(tool_input, account):
    transactions = SaleTransaction.objects.filter(account=account).select_related("customer", "location")

    limit = tool_input.get("limit") or 10
    results = []
    for tx in transactions[: limit]:
        if tx.is_fully_refunded:
            status = "refunded"
        elif tx.has_any_refund:
            status = "partially_refunded"
        else:
            status = "completed"

        results.append({
            "receipt_number": tx.receipt_number,
            "date": tx.created_at.date().isoformat(),
            "customer": tx.customer.name if tx.customer else None,
            "location": tx.location.name if tx.location else None,
            "payment_method": tx.get_payment_method_display(),
            "total": str(tx.total),
            "status": status,
        })

    return {"transactions": results}


def get_transaction_by_receipt(tool_input, account):
    receipt_number = (tool_input.get("receipt_number") or "").strip()
    if not receipt_number:
        return {"error": "receipt_number is required."}

    tx = SaleTransaction.objects.filter(account=account, receipt_number__iexact=receipt_number).first()
    if tx is None:
        return {"error": f"No transaction found with receipt number '{receipt_number}'."}

    items = []
    for item in tx.line_items.all():
        if item.quantity == 0:
            status = "refunded"
        elif item.returned_quantity > 0:
            status = "partially_refunded"
        else:
            status = "completed"

        items.append({
            "title": item.book.title,
            "quantity": item.original_quantity,
            "unit_price": str(item.unit_price),
            "subtotal": str(item.original_revenue),
            "status": status,
        })

    return {
        "receipt_number": tx.receipt_number,
        "date": tx.created_at.date().isoformat(),
        "customer": tx.customer.name if tx.customer else None,
        "location": tx.location.name if tx.location else None,
        "payment_method": tx.get_payment_method_display(),
        "items": items,
        "subtotal": str(tx.subtotal),
        "tax_total": str(tx.tax_total),
        "total": str(tx.total),
    }


def get_stock_by_location(tool_input, account):
    book_title = (tool_input.get("book_title") or "").strip()

    levels = StockLevel.objects.filter(account=account).select_related("book", "location")
    if book_title:
        levels = levels.filter(book__title__icontains=book_title)

    results = [
        {
            "book": level.book.title,
            "location": level.location.name,
            "quantity": level.quantity,
        }
        for level in levels
    ]

    return {"stock_levels": results}


def get_print_runs(tool_input, account):
    runs = PrintRun.objects.filter(account=account).select_related("book")

    status = (tool_input.get("status") or "").strip()
    if status:
        runs = runs.filter(status=status)

    limit = tool_input.get("limit") or 10
    results = [
        {
            "title": run.book.title,
            "quantity": run.quantity,
            "cost_per_unit": str(run.cost_per_unit),
            "total_cost": str(run.total_cost),
            "run_date": run.run_date.isoformat(),
            "status": run.status,
        }
        for run in runs[: limit]
    ]

    return {"print_runs": results}


def get_returns_summary(tool_input, account):
    days = tool_input.get("days") or 30
    cutoff = timezone.now().date() - timedelta(days=days)

    returns = Return.objects.filter(account=account, return_date__gte=cutoff).select_related("sale", "sale__book")

    results = [
        {
            "title": ret.sale.book.title,
            "quantity": ret.quantity,
            "reason": ret.reason,
            "refund_amount": str(ret.refund_amount),
            "currency": ret.sale.currency,
            "return_date": ret.return_date.isoformat(),
        }
        for ret in returns
    ]

    return {"returns": results, "days": days}


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
    "get_sales_trend": get_sales_trend,
    "get_category_performance": get_category_performance,
    "get_top_customers": get_top_customers,
    "get_business_insights": get_business_insights,
    "get_recent_transactions": get_recent_transactions,
    "get_transaction_by_receipt": get_transaction_by_receipt,
    "get_stock_by_location": get_stock_by_location,
    "get_print_runs": get_print_runs,
    "get_returns_summary": get_returns_summary,
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

    if name == "get_business_insights":
        return get_business_insights(tool_input, account, user)

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
