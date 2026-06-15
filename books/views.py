import csv
import json
import random
from datetime import timedelta
from io import BytesIO

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import TruncMonth
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext, gettext_lazy
from django.views.decorators.http import require_POST

from . import ai_chat
from .forms import (
    AuthorForm,
    BookForm,
    CategoryForm,
    ProfileForm,
    RedeemAccessCodeForm,
    ReorderForm,
    ReturnForm,
    SaleForm,
    SignupForm,
    SupplierForm,
    VerifyEmailForm,
)
from .models import AccessCode, Author, Book, Category, Profile, Reorder, Return, Sale, Supplier
from .permissions import ensure_roles


def _book_export_headers():
    return [
        gettext("ISBN"),
        gettext("Title"),
        gettext("Subtitle"),
        gettext("Authors"),
        gettext("Publisher"),
        gettext("Published Date"),
        gettext("Category"),
        gettext("Distribution Expense"),
    ]


LEARNING_QUOTES = [
    # Learning & growth
    gettext_lazy('"The beautiful thing about learning is that no one can take it away from you." — B.B. King'),
    gettext_lazy('"Live as if you were to die tomorrow. Learn as if you were to live forever." — Mahatma Gandhi'),
    gettext_lazy('"An investment in knowledge always pays the best interest." — Benjamin Franklin'),
    gettext_lazy('"The capacity to learn is a gift; the ability to learn is a skill; the willingness to learn is a choice." — Brian Herbert'),
    gettext_lazy('"Develop a passion for learning. If you do, you will never cease to grow." — Anthony J. D\'Angelo'),
    gettext_lazy('"Each small task of everyday life is part of the total harmony of the universe." — Saint Therese'),
    gettext_lazy('"Growth is painful. Change is painful. But nothing is as painful as staying stuck somewhere you don\'t belong." — N.R. Narayana Murthy'),
    gettext_lazy('"The expert in anything was once a beginner." — Helen Hayes'),
    gettext_lazy('"Success is the sum of small efforts repeated day in and day out." — Robert Collier'),
    gettext_lazy('"You don\'t have to be great to start, but you have to start to be great." — Zig Ziglar'),

    # Books & reading
    gettext_lazy('"A room without books is like a body without a soul." — Marcus Tullius Cicero'),
    gettext_lazy('"Books are a uniquely portable magic." — Stephen King'),
    gettext_lazy('"Today a reader, tomorrow a leader." — Margaret Fuller'),
    gettext_lazy('"Reading is to the mind what exercise is to the body." — Joseph Addison'),
    gettext_lazy('"Once you learn to read, you will be forever free." — Frederick Douglass'),
    gettext_lazy('"I have always imagined that Paradise will be a kind of library." — Jorge Luis Borges'),
    gettext_lazy('"So many books, so little time." — Frank Zappa'),
    gettext_lazy('"There is no friend as loyal as a book." — Ernest Hemingway'),
    gettext_lazy('"A reader lives a thousand lives before he dies. The man who never reads lives only one." — George R.R. Martin'),
    gettext_lazy('"Books are mirrors: you only see in them what you already have inside you." — Carlos Ruiz Zafón'),

    # Book distribution mission
    gettext_lazy('"Every book has a destination, and every reader has a journey." — Book Distribution Philosophy'),
    gettext_lazy('"We do not simply move books; we move knowledge, ideas, and imagination." — Book Distribution Philosophy'),
    gettext_lazy('"A warehouse full of books is a warehouse full of possibilities." — Book Distribution Philosophy'),
    gettext_lazy('"Every delivered book is a new story beginning somewhere." — Book Distribution Philosophy'),
    gettext_lazy('"Behind every order is a reader waiting for discovery." — Book Distribution Philosophy'),
    gettext_lazy('"Distribution turns printed pages into shared experiences." — Book Distribution Philosophy'),
    gettext_lazy('"Every package carries imagination, knowledge, and opportunity." — Book Distribution Philosophy'),
    gettext_lazy('"A distributor is the bridge between authors and readers." — Book Distribution Philosophy'),
    gettext_lazy('"The journey of knowledge begins with accessibility." — Book Distribution Philosophy'),
    gettext_lazy('"Books travel so minds can explore." — Book Distribution Philosophy'),

    # Business & teamwork
    gettext_lazy('"The goal as a company is to have customer service that is not just the best but legendary." — Sam Walton'),
    gettext_lazy('"Quality is the best business plan." — John Lasseter'),
    gettext_lazy('"Great things in business are never done by one person. They are done by a team of people." — Steve Jobs'),
    gettext_lazy('"Efficiency is doing better what is already being done." — Peter Drucker'),
    gettext_lazy('"The best way to predict the future is to create it." — Peter Drucker'),
]


def _time_based_greeting():
    hour = timezone.localtime().hour

    if hour < 12:
        return gettext("Good morning")
    if hour < 18:
        return gettext("Good afternoon")
    return gettext("Good evening")


def _book_export_rows(books):
    for book in books:
        yield [
            book.isbn or "",
            book.title,
            book.subtitle,
            ", ".join(author.name for author in book.authors.all()),
            book.publisher,
            book.published_date.isoformat(),
            book.category.name,
            book.distribution_expense,
        ]


def _sale_export_headers():
    return [
        gettext("Date"),
        gettext("Book"),
        gettext("Category"),
        gettext("Quantity"),
        gettext("Unit Price"),
        gettext("Revenue"),
        gettext("Channel"),
    ]


_PDF_FONTS_REGISTERED = False


def _register_pdf_fonts():
    global _PDF_FONTS_REGISTERED

    if _PDF_FONTS_REGISTERED:
        return

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    try:
        pdfmetrics.registerFont(TTFont("Tahoma", r"C:\Windows\Fonts\tahoma.ttf"))
        pdfmetrics.registerFont(TTFont("Tahoma-Bold", r"C:\Windows\Fonts\tahomabd.ttf"))
    except Exception:
        pass

    _PDF_FONTS_REGISTERED = True


def _pdf_fonts():
    if translation.get_language() == "ar":
        return "Tahoma", "Tahoma-Bold"

    return "Helvetica", "Helvetica-Bold"


def _pdf_text(value):
    text = str(value)

    if translation.get_language() == "ar":
        import arabic_reshaper
        from bidi.algorithm import get_display

        return get_display(arabic_reshaper.reshape(text))

    return text


def _sale_export_rows(sales):
    for sale in sales:
        yield [
            sale.sale_date.isoformat(),
            sale.book.title,
            sale.book.category.name,
            sale.quantity,
            sale.unit_price,
            sale.revenue,
            sale.channel,
        ]


def _reorder_export_headers():
    return [
        gettext("Date"),
        gettext("Book"),
        gettext("Supplier"),
        gettext("Quantity"),
        gettext("Status"),
        gettext("Note"),
        gettext("Received"),
    ]


def _reorder_export_rows(reorders):
    for reorder in reorders:
        yield [
            reorder.created_at.date().isoformat(),
            reorder.book.title,
            reorder.supplier.name if reorder.supplier else "",
            reorder.quantity,
            reorder.get_status_display(),
            reorder.note,
            reorder.received_at.date().isoformat() if reorder.received_at else "",
        ]


def _book_filters(request):
    books = Book.objects.filter(owner=request.user).select_related("category").prefetch_related("authors")

    search = request.GET.get("q", "").strip()
    category = request.GET.get("category", "")
    publisher = request.GET.get("publisher", "").strip()
    year = request.GET.get("year", "").strip()
    start_date = request.GET.get("start_date", "").strip()
    end_date = request.GET.get("end_date", "").strip()

    if search:
        books = books.filter(
            Q(title__icontains=search)
            | Q(subtitle__icontains=search)
            | Q(authors__name__icontains=search)
            | Q(publisher__icontains=search)
            | Q(isbn__icontains=search)
        ).distinct()

    if category and category.isdigit():
        books = books.filter(category_id=category)

    if publisher:
        books = books.filter(publisher=publisher)

    if year:
        books = books.filter(published_date__year=year)

    if start_date:
        books = books.filter(published_date__gte=start_date)

    if end_date:
        books = books.filter(published_date__lte=end_date)

    return books


def _filtered_books_for_export(request):
    return _book_filters(request).order_by("title")


def _filter_context(request):
    query_params = request.GET.copy()

    if "page" in query_params:
        query_params.pop("page")

    return {
        "categories": Category.objects.filter(owner=request.user).order_by("name"),
        "publishers": (
            Book.objects.filter(owner=request.user)
            .exclude(publisher="")
            .order_by("publisher")
            .values_list("publisher", flat=True)
            .distinct()
        ),
        "years": Book.objects.filter(owner=request.user).dates("published_date", "year", order="DESC"),
        "filters": request.GET,
        "query_string": query_params.urlencode(),
    }


@login_required
@permission_required("books.view_book", raise_exception=True)
def book_list(request):
    books = _book_filters(request)
    sort = request.GET.get("sort", "title")

    allowed_sorts = {
        "title": "title",
        "-title": "-title",
        "category": "category__name",
        "-category": "-category__name",
        "date": "published_date",
        "-date": "-published_date",
        "expense": "distribution_expense",
        "-expense": "-distribution_expense",
    }

    books = books.order_by(allowed_sorts.get(sort, "title"))
    paginator = Paginator(books, 10)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = _filter_context(request)
    context.update(
        {
            "books": page_obj.object_list,
            "page_obj": page_obj,
            "sort": sort,
        }
    )

    return render(request, "books/list.html", context)


@login_required
@permission_required("books.view_book", raise_exception=True)
def book_detail(request, id):
    book = get_object_or_404(
        Book.objects.select_related("category").prefetch_related("authors"),
        id=id,
        owner=request.user,
    )

    sales = book.sales.all()
    totals = sales.aggregate(
        total_quantity=Sum("quantity"),
        total_revenue=Sum(REVENUE_EXPRESSION),
    )

    context = {
        "book": book,
        "sales": sales[:10],
        "total_quantity_sold": totals["total_quantity"] or 0,
        "total_revenue": totals["total_revenue"] or 0,
    }

    return render(request, "books/detail.html", context)


@login_required
@permission_required("books.add_book", raise_exception=True)
def book_create(request):
    form = BookForm(user=request.user)

    if request.method == "POST":
        form = BookForm(request.POST, user=request.user)

        if form.is_valid():
            book = form.save(commit=False)
            book.owner = request.user
            book.save()
            form.save_m2m()
            messages.success(request, gettext("Book created."))
            return redirect("book_list")

    return render(request, "books/form.html", {"form": form})


@login_required
@permission_required("books.change_book", raise_exception=True)
def book_update(request, id):
    book = get_object_or_404(Book, id=id, owner=request.user)
    form = BookForm(instance=book, user=request.user)

    if request.method == "POST":
        form = BookForm(request.POST, instance=book, user=request.user)

        if form.is_valid():
            form.save()
            messages.success(request, gettext("Book updated."))
            return redirect("book_list")

    return render(request, "books/form.html", {"form": form})


@login_required
@permission_required("books.delete_book", raise_exception=True)
def book_delete(request, id):
    book = get_object_or_404(Book, id=id, owner=request.user)

    if request.method == "POST":
        book.delete()
        messages.success(request, gettext("Book deleted."))
        return redirect("book_list")

    return render(
        request,
        "books/confirm_delete.html",
        {
            "object_type": gettext("book"),
            "object_name": book.title,
            "cancel_url": reverse("book_list"),
        },
    )


@login_required
@permission_required("books.view_book", raise_exception=True)
def stock_list(request):
    books = Book.objects.filter(owner=request.user).select_related("category").order_by("stock_on_hand", "title")

    if request.GET.get("low") == "1":
        books = books.filter(stock_on_hand__lte=F("reorder_threshold"))

    return render(
        request,
        "books/stock_list.html",
        {
            "books": books,
            "low_only": request.GET.get("low") == "1",
        },
    )


@login_required
@permission_required("books.view_category", raise_exception=True)
def category_list(request):
    categories = Category.objects.filter(owner=request.user).annotate(book_count=Count("book")).order_by("name")
    return render(request, "books/category_list.html", {"categories": categories})


@login_required
@permission_required("books.add_category", raise_exception=True)
def category_create(request):
    form = CategoryForm()

    if request.method == "POST":
        form = CategoryForm(request.POST)

        if form.is_valid():
            category = form.save(commit=False)
            category.owner = request.user
            category.save()
            messages.success(request, gettext("Category created."))
            return redirect("category_list")

    return render(request, "books/category_form.html", {"form": form})


@login_required
@permission_required("books.change_category", raise_exception=True)
def category_update(request, id):
    category = get_object_or_404(Category, id=id, owner=request.user)
    form = CategoryForm(instance=category)

    if request.method == "POST":
        form = CategoryForm(request.POST, instance=category)

        if form.is_valid():
            form.save()
            messages.success(request, gettext("Category updated."))
            return redirect("category_list")

    return render(
        request,
        "books/category_form.html",
        {
            "form": form,
            "category": category,
        },
    )


@login_required
@permission_required("books.delete_category", raise_exception=True)
def category_delete(request, id):
    category = get_object_or_404(Category, id=id, owner=request.user)
    book_count = category.book_set.count()

    if request.method == "POST":
        if book_count:
            messages.error(
                request,
                gettext("Move or delete this category's books before deleting the category."),
            )
            return redirect("category_list")

        category.delete()
        messages.success(request, gettext("Category deleted."))
        return redirect("category_list")

    return render(
        request,
        "books/confirm_delete.html",
        {
            "object_type": gettext("category"),
            "object_name": category.name,
            "cancel_url": reverse("category_list"),
            "warning": (
                gettext("This category contains books and cannot be deleted yet.")
                if book_count
                else ""
            ),
            "disable_delete": bool(book_count),
        },
    )


@login_required
@permission_required("books.view_author", raise_exception=True)
def author_list(request):
    authors = Author.objects.filter(owner=request.user).annotate(book_count=Count("books")).order_by("name")
    return render(request, "books/author_list.html", {"authors": authors})


@login_required
@permission_required("books.add_author", raise_exception=True)
def author_create(request):
    form = AuthorForm()

    if request.method == "POST":
        form = AuthorForm(request.POST)

        if form.is_valid():
            author = form.save(commit=False)
            author.owner = request.user
            author.save()
            messages.success(request, gettext("Author created."))
            return redirect("author_list")

    return render(request, "books/author_form.html", {"form": form})


@login_required
@permission_required("books.change_author", raise_exception=True)
def author_update(request, id):
    author = get_object_or_404(Author, id=id, owner=request.user)
    form = AuthorForm(instance=author)

    if request.method == "POST":
        form = AuthorForm(request.POST, instance=author)

        if form.is_valid():
            form.save()
            messages.success(request, gettext("Author updated."))
            return redirect("author_list")

    return render(
        request,
        "books/author_form.html",
        {
            "form": form,
            "author": author,
        },
    )


@login_required
@permission_required("books.delete_author", raise_exception=True)
def author_delete(request, id):
    author = get_object_or_404(Author, id=id, owner=request.user)
    book_count = author.books.count()

    if request.method == "POST":
        if book_count:
            messages.error(
                request,
                gettext("Remove this author from their books before deleting them."),
            )
            return redirect("author_list")

        author.delete()
        messages.success(request, gettext("Author deleted."))
        return redirect("author_list")

    return render(
        request,
        "books/confirm_delete.html",
        {
            "object_type": gettext("author"),
            "object_name": author.name,
            "cancel_url": reverse("author_list"),
            "warning": (
                gettext("This author is linked to books and cannot be deleted yet.")
                if book_count
                else ""
            ),
            "disable_delete": bool(book_count),
        },
    )


@login_required
@permission_required("books.view_supplier", raise_exception=True)
def supplier_list(request):
    suppliers = Supplier.objects.filter(owner=request.user).annotate(reorder_count=Count("reorders")).order_by("name")
    return render(request, "books/supplier_list.html", {"suppliers": suppliers})


@login_required
@permission_required("books.add_supplier", raise_exception=True)
def supplier_create(request):
    form = SupplierForm()

    if request.method == "POST":
        form = SupplierForm(request.POST)

        if form.is_valid():
            supplier = form.save(commit=False)
            supplier.owner = request.user
            supplier.save()
            messages.success(request, gettext("Supplier created."))
            return redirect("supplier_list")

    return render(request, "books/supplier_form.html", {"form": form})


@login_required
@permission_required("books.change_supplier", raise_exception=True)
def supplier_update(request, id):
    supplier = get_object_or_404(Supplier, id=id, owner=request.user)
    form = SupplierForm(instance=supplier)

    if request.method == "POST":
        form = SupplierForm(request.POST, instance=supplier)

        if form.is_valid():
            form.save()
            messages.success(request, gettext("Supplier updated."))
            return redirect("supplier_list")

    return render(
        request,
        "books/supplier_form.html",
        {
            "form": form,
            "supplier": supplier,
        },
    )


@login_required
@permission_required("books.delete_supplier", raise_exception=True)
def supplier_delete(request, id):
    supplier = get_object_or_404(Supplier, id=id, owner=request.user)
    reorder_count = supplier.reorders.count()

    if request.method == "POST":
        if reorder_count:
            messages.error(
                request,
                gettext("Remove this supplier from its reorders before deleting it."),
            )
            return redirect("supplier_list")

        supplier.delete()
        messages.success(request, gettext("Supplier deleted."))
        return redirect("supplier_list")

    return render(
        request,
        "books/confirm_delete.html",
        {
            "object_type": gettext("supplier"),
            "object_name": supplier.name,
            "cancel_url": reverse("supplier_list"),
            "warning": (
                gettext("This supplier is linked to reorders and cannot be deleted yet.")
                if reorder_count
                else ""
            ),
            "disable_delete": bool(reorder_count),
        },
    )


REVENUE_EXPRESSION = ExpressionWrapper(
    F("quantity") * F("unit_price"),
    output_field=DecimalField(max_digits=10, decimal_places=2),
)


@login_required
@permission_required("books.view_book", raise_exception=True)
def report(request):
    filtered_books = _book_filters(request)

    data = (
        filtered_books
        .values("category__name")
        .annotate(total=Sum("distribution_expense"), count=Count("id"))
        .order_by("category__name")
    )

    revenue_by_category = {
        item["book__category__name"]: item["revenue"] or 0
        for item in (
            Sale.objects.filter(book__in=filtered_books)
            .values("book__category__name")
            .annotate(revenue=Sum(REVENUE_EXPRESSION))
        )
    }

    labels = []
    values = []
    counts = []
    revenues = []
    profits = []

    for item in data:
        category_name = item["category__name"]
        expense = float(item["total"])
        revenue = float(revenue_by_category.get(category_name, 0))

        labels.append(category_name)
        values.append(expense)
        counts.append(item["count"])
        revenues.append(revenue)
        profits.append(revenue - expense)

    totals = filtered_books.aggregate(
        total=Sum("distribution_expense"),
        count=Count("id"),
    )

    total_revenue = (
        Sale.objects.filter(book__in=filtered_books).aggregate(
            total=Sum(REVENUE_EXPRESSION)
        )["total"]
        or 0
    )
    total_expense = totals["total"] or 0

    trend_data = (
        Sale.objects.filter(book__in=filtered_books)
        .annotate(month=TruncMonth("sale_date"))
        .values("month")
        .annotate(units=Sum("quantity"), revenue=Sum(REVENUE_EXPRESSION))
        .order_by("month")
    )

    trend_labels = []
    trend_units = []
    trend_revenues = []

    for item in trend_data:
        trend_labels.append(item["month"].strftime("%b %Y"))
        trend_units.append(item["units"] or 0)
        trend_revenues.append(float(item["revenue"] or 0))

    context = _filter_context(request)
    context.update(
        {
            "values": json.dumps(values),
            "counts": json.dumps(counts),
            "labels": json.dumps(labels),
            "revenues": json.dumps(revenues),
            "profits": json.dumps(profits),
            "trend_labels": json.dumps(trend_labels),
            "trend_units": json.dumps(trend_units),
            "trend_revenues": json.dumps(trend_revenues),
            "total_expense": total_expense,
            "total_books": totals["count"],
            "total_revenue": total_revenue,
            "total_profit": total_revenue - total_expense,
        }
    )

    return render(request, "books/report.html", context)


@login_required
@permission_required("books.view_book", raise_exception=True)
def dashboard(request):
    books = Book.objects.filter(owner=request.user).select_related("category")
    sales = Sale.objects.filter(owner=request.user)

    total_expense = books.aggregate(total=Sum("distribution_expense"))["total"] or 0
    total_revenue = sales.aggregate(total=Sum(REVENUE_EXPRESSION))["total"] or 0
    total_profit = total_revenue - total_expense
    total_units_sold = sales.aggregate(total=Sum("quantity"))["total"] or 0

    low_stock_books = books.filter(
        stock_on_hand__lte=F("reorder_threshold")
    ).order_by("stock_on_hand", "title")

    category_expenses = (
        books.values("category__name")
        .annotate(expense=Sum("distribution_expense"))
        .order_by("category__name")
    )

    revenue_by_category = {
        item["book__category__name"]: item["revenue"] or 0
        for item in (
            sales.values("book__category__name")
            .annotate(revenue=Sum(REVENUE_EXPRESSION))
        )
    }

    labels = []
    revenues = []
    profits = []

    for item in category_expenses:
        category_name = item["category__name"]
        expense = float(item["expense"] or 0)
        revenue = float(revenue_by_category.get(category_name, 0))

        labels.append(category_name)
        revenues.append(revenue)
        profits.append(revenue - expense)

    trend_data = (
        sales.annotate(month=TruncMonth("sale_date"))
        .values("month")
        .annotate(units=Sum("quantity"), revenue=Sum(REVENUE_EXPRESSION))
        .order_by("month")
    )

    trend_labels = []
    trend_units = []
    trend_revenues = []

    for item in trend_data:
        trend_labels.append(item["month"].strftime("%b %Y"))
        trend_units.append(item["units"] or 0)
        trend_revenues.append(float(item["revenue"] or 0))

    top_books = (
        books.annotate(units_sold=Sum("sales__quantity"))
        .filter(units_sold__gt=0)
        .order_by("-units_sold")[:5]
    )

    recent_sales = sales.select_related("book", "book__category")[:5]

    pending_reorders_count = Reorder.objects.filter(
        owner=request.user,
        status__in=[Reorder.STATUS_PENDING, Reorder.STATUS_ORDERED],
    ).count()

    context = {
        "greeting": _time_based_greeting(),
        "quote": random.choice(LEARNING_QUOTES),
        "all_quotes": [str(quote) for quote in LEARNING_QUOTES],
        "total_books": books.count(),
        "total_authors": Author.objects.filter(owner=request.user).count(),
        "total_categories": Category.objects.filter(owner=request.user).count(),
        "low_stock_count": low_stock_books.count(),
        "low_stock_books": low_stock_books[:5],
        "total_revenue": total_revenue,
        "total_expense": total_expense,
        "total_profit": total_profit,
        "total_units_sold": total_units_sold,
        "labels": json.dumps(labels),
        "revenues": json.dumps(revenues),
        "profits": json.dumps(profits),
        "trend_labels": json.dumps(trend_labels),
        "trend_units": json.dumps(trend_units),
        "trend_revenues": json.dumps(trend_revenues),
        "top_books": top_books,
        "recent_sales": recent_sales,
        "pending_reorders_count": pending_reorders_count,
    }

    return render(request, "books/dashboard.html", context)


@login_required
def profile_update(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)
    form = ProfileForm(instance=profile)

    if request.method == "POST":
        form = ProfileForm(request.POST, request.FILES, instance=profile)

        if form.is_valid():
            form.save()
            messages.success(request, gettext("Profile photo updated."))
            return redirect("dashboard")

    return render(request, "books/profile_form.html", {"form": form, "profile": profile})


@login_required
def about(request):
    return render(request, "books/about.html")


@login_required
@require_POST
def chat_api(request):
    try:
        payload = json.loads(request.body)
    except ValueError:
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    message = (payload.get("message") or "").strip()
    history = payload.get("history") or []

    if not message:
        return JsonResponse({"error": "Message is required."}, status=400)

    try:
        reply, updated_history = ai_chat.get_chat_reply(request.user, message, history)
    except Exception:
        reply = gettext("Sorry, something went wrong talking to the AI assistant. Please try again.")
        updated_history = history

    return JsonResponse({"reply": reply, "history": updated_history})


def _adjust_stock(book_id, delta, owner):
    book = Book.objects.get(id=book_id, owner=owner)
    book.stock_on_hand = max(0, book.stock_on_hand + delta)

    if book.is_low_stock and not book.low_stock_alert_sent:
        _send_low_stock_email(owner, book)
        book.low_stock_alert_sent = True
    elif not book.is_low_stock and book.low_stock_alert_sent:
        book.low_stock_alert_sent = False

    book.save(update_fields=["stock_on_hand", "low_stock_alert_sent"])
    return book


def _send_low_stock_email(user, book):
    if not user.email:
        return

    send_mail(
        subject=f"RumiPress: Low stock alert - {book.title}",
        message=(
            f"Hi {user.username},\n\n"
            f"'{book.title}' is running low on stock: {book.stock_on_hand} remaining "
            f"(reorder threshold {book.reorder_threshold}).\n\n"
            "Consider creating a reorder to restock this title."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=True,
    )


def _send_reorder_status_email(user, reorder):
    if not user.email:
        return

    status_label = reorder.get_status_display()

    send_mail(
        subject=f"RumiPress: Reorder {status_label} - {reorder.book.title}",
        message=(
            f"Hi {user.username},\n\n"
            f"Your reorder for '{reorder.book.title}' (quantity {reorder.quantity}) "
            f"is now marked as {status_label.lower()}."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=True,
    )


def _notify_stock_level(request, book):
    if book.is_low_stock:
        messages.warning(
            request,
            gettext(
                "Low stock: '%(title)s' has %(stock)s remaining "
                "(reorder threshold %(threshold)s)."
            )
            % {
                "title": book.title,
                "stock": book.stock_on_hand,
                "threshold": book.reorder_threshold,
            },
        )
    else:
        messages.info(
            request,
            gettext("'%(title)s' now has %(stock)s in stock.")
            % {"title": book.title, "stock": book.stock_on_hand},
        )


@login_required
@permission_required("books.view_sale", raise_exception=True)
def sale_list(request):
    sales = Sale.objects.filter(owner=request.user).select_related("book", "book__category").order_by("-sale_date")

    paginator = Paginator(sales, 10)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "books/sale_list.html",
        {
            "sales": page_obj.object_list,
            "page_obj": page_obj,
        },
    )


@login_required
@permission_required("books.add_sale", raise_exception=True)
def sale_create(request):
    form = SaleForm(user=request.user)

    if request.method == "POST":
        form = SaleForm(request.POST, user=request.user)

        if form.is_valid():
            book = form.cleaned_data["book"]
            quantity = form.cleaned_data["quantity"]

            if book.stock_on_hand < quantity:
                messages.error(
                    request,
                    gettext("Cannot record sale: '%(title)s' only has %(stock)s in stock.")
                    % {"title": book.title, "stock": book.stock_on_hand},
                )
            else:
                sale = form.save(commit=False)
                sale.owner = request.user
                sale.save()
                book = _adjust_stock(sale.book_id, -sale.quantity, request.user)
                messages.success(request, gettext("Sale recorded."))
                _notify_stock_level(request, book)
                return redirect("sale_list")

    return render(request, "books/sale_form.html", {"form": form})


@login_required
@permission_required("books.change_sale", raise_exception=True)
def sale_update(request, id):
    sale = get_object_or_404(Sale, id=id, owner=request.user)
    form = SaleForm(instance=sale, user=request.user)

    if request.method == "POST":
        previous_book_id = sale.book_id
        previous_quantity = sale.quantity

        form = SaleForm(request.POST, instance=sale, user=request.user)

        if form.is_valid():
            new_book = form.cleaned_data["book"]
            new_quantity = form.cleaned_data["quantity"]

            available = new_book.stock_on_hand
            if new_book.id == previous_book_id:
                available += previous_quantity

            if available < new_quantity:
                messages.error(
                    request,
                    gettext("Cannot update sale: '%(title)s' only has %(stock)s available.")
                    % {"title": new_book.title, "stock": available},
                )
            else:
                sale = form.save()
                _adjust_stock(previous_book_id, previous_quantity, request.user)
                book = _adjust_stock(sale.book_id, -sale.quantity, request.user)
                messages.success(request, gettext("Sale updated."))
                _notify_stock_level(request, book)
                return redirect("sale_list")

    return render(
        request,
        "books/sale_form.html",
        {
            "form": form,
            "sale": sale,
        },
    )


@login_required
@permission_required("books.delete_sale", raise_exception=True)
def sale_delete(request, id):
    sale = get_object_or_404(Sale, id=id, owner=request.user)

    if request.method == "POST":
        _adjust_stock(sale.book_id, sale.quantity, request.user)
        sale.delete()
        messages.success(request, gettext("Sale deleted."))
        return redirect("sale_list")

    return render(
        request,
        "books/confirm_delete.html",
        {
            "object_type": gettext("sale"),
            "object_name": f"{sale.book.title} ({sale.sale_date})",
            "cancel_url": reverse("sale_list"),
        },
    )


@login_required
@permission_required("books.view_return", raise_exception=True)
def return_list(request):
    returns = Return.objects.filter(owner=request.user).select_related("sale", "sale__book")

    paginator = Paginator(returns, 10)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "books/return_list.html",
        {
            "returns": page_obj.object_list,
            "page_obj": page_obj,
        },
    )


@login_required
@permission_required("books.add_return", raise_exception=True)
def return_create(request, sale_id):
    sale = get_object_or_404(Sale, id=sale_id, owner=request.user)

    if sale.quantity <= 0:
        messages.error(request, gettext("This sale has already been fully returned."))
        return redirect("sale_list")

    if request.method == "POST":
        form = ReturnForm(request.POST)

        if form.is_valid():
            quantity = form.cleaned_data["quantity"]

            if quantity > sale.quantity:
                messages.error(
                    request,
                    gettext("Cannot return more than the %(quantity)s sold.")
                    % {"quantity": sale.quantity},
                )
            else:
                return_obj = form.save(commit=False)
                return_obj.owner = request.user
                return_obj.sale = sale
                return_obj.save()

                sale.quantity -= quantity
                sale.save(update_fields=["quantity"])

                book = _adjust_stock(sale.book_id, quantity, request.user)
                messages.success(request, gettext("Return recorded and stock updated."))
                _notify_stock_level(request, book)
                return redirect("return_list")
    else:
        form = ReturnForm(initial={"quantity": sale.quantity, "return_date": timezone.now().date()})

    return render(
        request,
        "books/return_form.html",
        {
            "form": form,
            "sale": sale,
        },
    )


@login_required
@permission_required("books.delete_return", raise_exception=True)
def return_delete(request, id):
    return_obj = get_object_or_404(Return, id=id, owner=request.user)

    if request.method == "POST":
        sale = return_obj.sale
        sale.quantity += return_obj.quantity
        sale.save(update_fields=["quantity"])
        _adjust_stock(sale.book_id, -return_obj.quantity, request.user)
        return_obj.delete()
        messages.success(request, gettext("Return deleted."))
        return redirect("return_list")

    return render(
        request,
        "books/confirm_delete.html",
        {
            "object_type": gettext("return"),
            "object_name": f"{return_obj.sale.book.title} ({return_obj.return_date})",
            "cancel_url": reverse("return_list"),
        },
    )


@login_required
@permission_required("books.view_reorder", raise_exception=True)
def reorder_list(request):
    reorders = Reorder.objects.filter(owner=request.user).select_related("book", "book__category", "supplier")

    status = request.GET.get("status")
    if status in dict(Reorder.STATUS_CHOICES):
        reorders = reorders.filter(status=status)

    paginator = Paginator(reorders, 10)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "books/reorder_list.html",
        {
            "reorders": page_obj.object_list,
            "page_obj": page_obj,
            "status_choices": Reorder.STATUS_CHOICES,
            "selected_status": status,
        },
    )


@login_required
@permission_required("books.add_reorder", raise_exception=True)
def reorder_create(request, book_id):
    book = get_object_or_404(Book, id=book_id, owner=request.user)

    suggested_quantity = max(book.reorder_threshold * 2 - book.stock_on_hand, book.reorder_threshold, 1)

    if request.method == "POST":
        form = ReorderForm(request.POST, user=request.user)

        if form.is_valid():
            reorder = form.save(commit=False)
            reorder.owner = request.user
            reorder.book = book
            reorder.save()
            messages.success(request, gettext("Reorder created."))
            return redirect("reorder_list")
    else:
        form = ReorderForm(initial={"quantity": suggested_quantity}, user=request.user)

    return render(
        request,
        "books/reorder_form.html",
        {
            "form": form,
            "book": book,
        },
    )


@login_required
@permission_required("books.change_reorder", raise_exception=True)
@require_POST
def reorder_update_status(request, id, action):
    reorder = get_object_or_404(Reorder, id=id, owner=request.user)

    transitions = {
        "ordered": (Reorder.STATUS_PENDING, Reorder.STATUS_ORDERED),
        "received": (Reorder.STATUS_ORDERED, Reorder.STATUS_RECEIVED),
        "cancelled": (None, Reorder.STATUS_CANCELLED),
    }

    if action not in transitions:
        return redirect("reorder_list")

    required_status, new_status = transitions[action]

    if action == "cancelled":
        if reorder.status in (Reorder.STATUS_RECEIVED, Reorder.STATUS_CANCELLED):
            messages.error(request, gettext("This reorder can't be updated from its current status."))
            return redirect("reorder_list")
    elif reorder.status != required_status:
        messages.error(request, gettext("This reorder can't be updated from its current status."))
        return redirect("reorder_list")

    reorder.status = new_status

    if new_status == Reorder.STATUS_RECEIVED:
        reorder.received_at = timezone.now()
        reorder.save(update_fields=["status", "received_at"])
        book = _adjust_stock(reorder.book_id, reorder.quantity, request.user)
        messages.success(request, gettext("Reorder received and stock updated."))
        _notify_stock_level(request, book)
    else:
        reorder.save(update_fields=["status"])

        if new_status == Reorder.STATUS_ORDERED:
            messages.success(request, gettext("Reorder marked as ordered."))
        else:
            messages.success(request, gettext("Reorder cancelled."))

    _send_reorder_status_email(request.user, reorder)

    return redirect("reorder_list")


@login_required
@permission_required("books.delete_reorder", raise_exception=True)
def reorder_delete(request, id):
    reorder = get_object_or_404(Reorder, id=id, owner=request.user)

    if reorder.status != Reorder.STATUS_CANCELLED:
        messages.error(request, gettext("Only cancelled reorders can be deleted."))
        return redirect("reorder_list")

    if request.method == "POST":
        reorder.delete()
        messages.success(request, gettext("Reorder deleted."))
        return redirect("reorder_list")

    return render(
        request,
        "books/confirm_delete.html",
        {
            "object_type": gettext("reorder"),
            "object_name": f"{reorder.book.title} ({reorder.quantity})",
            "cancel_url": reverse("reorder_list"),
        },
    )


@login_required
@permission_required("books.view_book", raise_exception=True)
def export_books_csv(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="rumi-press-books.csv"'

    writer = csv.writer(response)
    writer.writerow(_book_export_headers())

    for row in _book_export_rows(_filtered_books_for_export(request)):
        writer.writerow(row)

    return response


@login_required
@permission_required("books.view_book", raise_exception=True)
def export_books_excel(request):
    from openpyxl import Workbook

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Books"
    worksheet.append(_book_export_headers())

    for row in _book_export_rows(_filtered_books_for_export(request)):
        worksheet.append(row)

    for column in worksheet.columns:
        max_length = max(len(str(cell.value or "")) for cell in column)
        worksheet.column_dimensions[column[0].column_letter].width = min(
            max_length + 2,
            40,
        )

    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)

    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="rumi-press-books.xlsx"'

    return response


@login_required
@permission_required("books.view_book", raise_exception=True)
def export_books_pdf(request):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    _register_pdf_fonts()
    body_font, bold_font = _pdf_fonts()

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(letter),
        leftMargin=24,
        rightMargin=24,
        topMargin=24,
        bottomMargin=24,
    )

    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    title_style.fontName = bold_font

    elements = [
        Paragraph(_pdf_text(gettext("Rumi Press Books")), title_style),
        Spacer(1, 12),
    ]

    rows = [[_pdf_text(value) for value in _book_export_headers()]]

    for row in _book_export_rows(_filtered_books_for_export(request)):
        rows.append([_pdf_text(value) for value in row])

    table = Table(
        rows,
        repeatRows=1,
        colWidths=[76, 120, 105, 120, 100, 70, 90, 70],
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f1f1f")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), bold_font),
                ("FONTNAME", (0, 1), (-1, -1), body_font),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )

    elements.append(table)
    document.build(elements)
    buffer.seek(0)

    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="rumi-press-books.pdf"'

    return response


@login_required
@permission_required("books.view_sale", raise_exception=True)
def export_sales_csv(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="rumi-press-sales.csv"'

    writer = csv.writer(response)
    writer.writerow(_sale_export_headers())

    sales = Sale.objects.filter(owner=request.user).select_related("book", "book__category")

    for row in _sale_export_rows(sales):
        writer.writerow(row)

    return response


@login_required
@permission_required("books.view_sale", raise_exception=True)
def export_sales_excel(request):
    from openpyxl import Workbook

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Sales"
    worksheet.append(_sale_export_headers())

    sales = Sale.objects.filter(owner=request.user).select_related("book", "book__category")

    for row in _sale_export_rows(sales):
        worksheet.append(row)

    for column in worksheet.columns:
        max_length = max(len(str(cell.value or "")) for cell in column)
        worksheet.column_dimensions[column[0].column_letter].width = min(
            max_length + 2,
            40,
        )

    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)

    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="rumi-press-sales.xlsx"'

    return response


@login_required
@permission_required("books.view_sale", raise_exception=True)
def export_sales_pdf(request):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    _register_pdf_fonts()
    body_font, bold_font = _pdf_fonts()

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(letter),
        leftMargin=24,
        rightMargin=24,
        topMargin=24,
        bottomMargin=24,
    )

    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    title_style.fontName = bold_font

    elements = [
        Paragraph(_pdf_text(gettext("Rumi Press Sales")), title_style),
        Spacer(1, 12),
    ]

    sales = Sale.objects.filter(owner=request.user).select_related("book", "book__category")

    rows = [[_pdf_text(value) for value in _sale_export_headers()]]

    for row in _sale_export_rows(sales):
        rows.append([_pdf_text(value) for value in row])

    table = Table(
        rows,
        repeatRows=1,
        colWidths=[70, 160, 100, 70, 70, 70, 100],
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f1f1f")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), bold_font),
                ("FONTNAME", (0, 1), (-1, -1), body_font),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )

    elements.append(table)
    document.build(elements)
    buffer.seek(0)

    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="rumi-press-sales.pdf"'

    return response


@login_required
@permission_required("books.view_reorder", raise_exception=True)
def export_reorders_csv(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="rumi-press-reorders.csv"'

    writer = csv.writer(response)
    writer.writerow(_reorder_export_headers())

    reorders = Reorder.objects.filter(owner=request.user).select_related("book", "supplier")

    for row in _reorder_export_rows(reorders):
        writer.writerow(row)

    return response


@login_required
@permission_required("books.view_reorder", raise_exception=True)
def export_reorders_excel(request):
    from openpyxl import Workbook

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Reorders"
    worksheet.append(_reorder_export_headers())

    reorders = Reorder.objects.filter(owner=request.user).select_related("book", "supplier")

    for row in _reorder_export_rows(reorders):
        worksheet.append(row)

    for column in worksheet.columns:
        max_length = max(len(str(cell.value or "")) for cell in column)
        worksheet.column_dimensions[column[0].column_letter].width = min(
            max_length + 2,
            40,
        )

    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)

    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="rumi-press-reorders.xlsx"'

    return response


@login_required
@permission_required("books.view_reorder", raise_exception=True)
def export_reorders_pdf(request):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    _register_pdf_fonts()
    body_font, bold_font = _pdf_fonts()

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(letter),
        leftMargin=24,
        rightMargin=24,
        topMargin=24,
        bottomMargin=24,
    )

    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    title_style.fontName = bold_font

    elements = [
        Paragraph(_pdf_text(gettext("Rumi Press Reorders")), title_style),
        Spacer(1, 12),
    ]

    reorders = Reorder.objects.filter(owner=request.user).select_related("book", "supplier")

    rows = [[_pdf_text(value) for value in _reorder_export_headers()]]

    for row in _reorder_export_rows(reorders):
        rows.append([_pdf_text(value) for value in row])

    table = Table(
        rows,
        repeatRows=1,
        colWidths=[70, 140, 100, 50, 60, 130, 70],
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f1f1f")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), bold_font),
                ("FONTNAME", (0, 1), (-1, -1), body_font),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )

    elements.append(table)
    document.build(elements)
    buffer.seek(0)

    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="rumi-press-reorders.pdf"'

    return response


def _generate_verification_code():
    return f"{random.randint(0, 999999):06d}"


def _send_verification_email(user, code):
    send_mail(
        subject="Your RumiPress verification code",
        message=(
            f"Hi {user.username},\n\n"
            f"Your RumiPress email verification code is: {code}\n"
            f"This code expires in {settings.VERIFICATION_CODE_TTL_MINUTES} minutes.\n\n"
            "If you didn't request this, you can ignore this email."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=True,
    )


def _notify_owners_of_pending_activation(user):
    owner_emails = list(
        User.objects.filter(is_superuser=True, email__gt="")
        .exclude(email="")
        .values_list("email", flat=True)
    )

    if not owner_emails:
        return

    send_mail(
        subject="RumiPress: new user awaiting access code",
        message=(
            f"User '{user.username}' ({user.email}) has verified their email "
            "and is waiting for an access code to activate their account.\n\n"
            "Generate an access code in the admin site under Books > Access codes."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=owner_emails,
        fail_silently=True,
    )


def _get_pending_user(request):
    user_id = request.session.get("pending_user_id")
    if not user_id:
        return None

    try:
        return User.objects.get(id=user_id, is_active=False)
    except User.DoesNotExist:
        return None


def signup(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    form = SignupForm()

    if request.method == "POST":
        form = SignupForm(request.POST)

        if form.is_valid():
            user = User.objects.create_user(
                username=form.cleaned_data["username"],
                email=form.cleaned_data["email"],
                password=form.cleaned_data["password1"],
                is_active=False,
            )

            code = _generate_verification_code()
            profile, _ = Profile.objects.get_or_create(user=user)
            profile.verification_code = code
            profile.verification_code_expires_at = timezone.now() + timedelta(
                minutes=settings.VERIFICATION_CODE_TTL_MINUTES
            )
            profile.save()

            _send_verification_email(user, code)

            request.session["pending_user_id"] = user.id
            return redirect("verify_email")

    return render(request, "registration/signup.html", {"form": form})


def verify_email(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    user = _get_pending_user(request)
    if user is None:
        return redirect("signup")

    profile, _ = Profile.objects.get_or_create(user=user)

    if profile.email_verified:
        return redirect("redeem_access_code")

    form = VerifyEmailForm()

    if request.method == "POST":
        if request.POST.get("action") == "resend":
            code = _generate_verification_code()
            profile.verification_code = code
            profile.verification_code_expires_at = timezone.now() + timedelta(
                minutes=settings.VERIFICATION_CODE_TTL_MINUTES
            )
            profile.save()
            _send_verification_email(user, code)
            messages.success(request, gettext("A new verification code has been sent to your email."))
        else:
            form = VerifyEmailForm(request.POST)

            if form.is_valid():
                code = form.cleaned_data["code"]
                expires_at = profile.verification_code_expires_at

                if (
                    profile.verification_code
                    and code == profile.verification_code
                    and expires_at
                    and timezone.now() <= expires_at
                ):
                    profile.email_verified = True
                    profile.verification_code = ""
                    profile.verification_code_expires_at = None
                    profile.save()
                    _notify_owners_of_pending_activation(user)
                    return redirect("redeem_access_code")

                form.add_error("code", gettext("That code is invalid or has expired."))

    return render(
        request,
        "registration/verify_email.html",
        {"form": form, "email": user.email},
    )


def redeem_access_code(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    user = _get_pending_user(request)
    if user is None:
        return redirect("signup")

    profile, _ = Profile.objects.get_or_create(user=user)

    if not profile.email_verified:
        return redirect("verify_email")

    form = RedeemAccessCodeForm()

    if request.method == "POST":
        form = RedeemAccessCodeForm(request.POST)

        if form.is_valid():
            code_value = form.cleaned_data["code"].strip()

            try:
                access_code = AccessCode.objects.get(code__iexact=code_value)
            except AccessCode.DoesNotExist:
                access_code = None

            if access_code is None or not access_code.is_valid:
                form.add_error("code", gettext("That access code is invalid, used, or expired."))
            else:
                access_code.is_used = True
                access_code.used_by = user
                access_code.used_at = timezone.now()
                access_code.save()

                profile.access_code_redeemed = True
                profile.save()

                user.is_active = True
                user.save()

                admin_group = ensure_roles()["Admin"]
                user.groups.add(admin_group)

                Category.objects.get_or_create(owner=user, name="General")

                del request.session["pending_user_id"]

                login(request, user)
                messages.success(request, gettext("Your account is active. Welcome to RumiPress!"))
                return redirect("dashboard")

    return render(request, "registration/redeem_access_code.html", {"form": form})
