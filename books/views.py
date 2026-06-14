import csv
import json
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import TruncMonth
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import AuthorForm, BookForm, CategoryForm, ProfileForm, SaleForm
from .models import Author, Book, Category, Profile, Sale


BOOK_EXPORT_HEADERS = [
    "ISBN",
    "Title",
    "Subtitle",
    "Authors",
    "Publisher",
    "Published Date",
    "Category",
    "Distribution Expense",
]


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


SALE_EXPORT_HEADERS = [
    "Date",
    "Book",
    "Category",
    "Quantity",
    "Unit Price",
    "Revenue",
    "Channel",
]


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


def _book_filters(request):
    books = Book.objects.select_related("category").prefetch_related("authors")

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
        "categories": Category.objects.order_by("name"),
        "publishers": (
            Book.objects.exclude(publisher="")
            .order_by("publisher")
            .values_list("publisher", flat=True)
            .distinct()
        ),
        "years": Book.objects.dates("published_date", "year", order="DESC"),
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
    form = BookForm()

    if request.method == "POST":
        form = BookForm(request.POST)

        if form.is_valid():
            form.save()
            messages.success(request, "Book created.")
            return redirect("book_list")

    return render(request, "books/form.html", {"form": form})


@login_required
@permission_required("books.change_book", raise_exception=True)
def book_update(request, id):
    book = get_object_or_404(Book, id=id)
    form = BookForm(instance=book)

    if request.method == "POST":
        form = BookForm(request.POST, instance=book)

        if form.is_valid():
            form.save()
            messages.success(request, "Book updated.")
            return redirect("book_list")

    return render(request, "books/form.html", {"form": form})


@login_required
@permission_required("books.delete_book", raise_exception=True)
def book_delete(request, id):
    book = get_object_or_404(Book, id=id)

    if request.method == "POST":
        book.delete()
        messages.success(request, "Book deleted.")
        return redirect("book_list")

    return render(
        request,
        "books/confirm_delete.html",
        {
            "object_type": "book",
            "object_name": book.title,
            "cancel_url": reverse("book_list"),
        },
    )


@login_required
@permission_required("books.view_book", raise_exception=True)
def stock_list(request):
    books = Book.objects.select_related("category").order_by("stock_on_hand", "title")

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
    categories = Category.objects.annotate(book_count=Count("book")).order_by("name")
    return render(request, "books/category_list.html", {"categories": categories})


@login_required
@permission_required("books.add_category", raise_exception=True)
def category_create(request):
    form = CategoryForm()

    if request.method == "POST":
        form = CategoryForm(request.POST)

        if form.is_valid():
            form.save()
            messages.success(request, "Category created.")
            return redirect("category_list")

    return render(request, "books/category_form.html", {"form": form})


@login_required
@permission_required("books.change_category", raise_exception=True)
def category_update(request, id):
    category = get_object_or_404(Category, id=id)
    form = CategoryForm(instance=category)

    if request.method == "POST":
        form = CategoryForm(request.POST, instance=category)

        if form.is_valid():
            form.save()
            messages.success(request, "Category updated.")
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
    category = get_object_or_404(Category, id=id)
    book_count = category.book_set.count()

    if request.method == "POST":
        if book_count:
            messages.error(
                request,
                "Move or delete this category's books before deleting the category.",
            )
            return redirect("category_list")

        category.delete()
        messages.success(request, "Category deleted.")
        return redirect("category_list")

    return render(
        request,
        "books/confirm_delete.html",
        {
            "object_type": "category",
            "object_name": category.name,
            "cancel_url": reverse("category_list"),
            "warning": (
                "This category contains books and cannot be deleted yet."
                if book_count
                else ""
            ),
            "disable_delete": bool(book_count),
        },
    )


@login_required
@permission_required("books.view_author", raise_exception=True)
def author_list(request):
    authors = Author.objects.annotate(book_count=Count("books")).order_by("name")
    return render(request, "books/author_list.html", {"authors": authors})


@login_required
@permission_required("books.add_author", raise_exception=True)
def author_create(request):
    form = AuthorForm()

    if request.method == "POST":
        form = AuthorForm(request.POST)

        if form.is_valid():
            form.save()
            messages.success(request, "Author created.")
            return redirect("author_list")

    return render(request, "books/author_form.html", {"form": form})


@login_required
@permission_required("books.change_author", raise_exception=True)
def author_update(request, id):
    author = get_object_or_404(Author, id=id)
    form = AuthorForm(instance=author)

    if request.method == "POST":
        form = AuthorForm(request.POST, instance=author)

        if form.is_valid():
            form.save()
            messages.success(request, "Author updated.")
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
    author = get_object_or_404(Author, id=id)
    book_count = author.books.count()

    if request.method == "POST":
        if book_count:
            messages.error(
                request,
                "Remove this author from their books before deleting them.",
            )
            return redirect("author_list")

        author.delete()
        messages.success(request, "Author deleted.")
        return redirect("author_list")

    return render(
        request,
        "books/confirm_delete.html",
        {
            "object_type": "author",
            "object_name": author.name,
            "cancel_url": reverse("author_list"),
            "warning": (
                "This author is linked to books and cannot be deleted yet."
                if book_count
                else ""
            ),
            "disable_delete": bool(book_count),
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
    books = Book.objects.select_related("category")

    total_expense = books.aggregate(total=Sum("distribution_expense"))["total"] or 0
    total_revenue = Sale.objects.aggregate(total=Sum(REVENUE_EXPRESSION))["total"] or 0
    total_profit = total_revenue - total_expense
    total_units_sold = Sale.objects.aggregate(total=Sum("quantity"))["total"] or 0

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
            Sale.objects.values("book__category__name")
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
        Sale.objects.annotate(month=TruncMonth("sale_date"))
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

    recent_sales = Sale.objects.select_related("book", "book__category")[:5]

    context = {
        "total_books": books.count(),
        "total_authors": Author.objects.count(),
        "total_categories": Category.objects.count(),
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
            messages.success(request, "Profile photo updated.")
            return redirect("dashboard")

    return render(request, "books/profile_form.html", {"form": form, "profile": profile})


def _adjust_stock(book_id, delta):
    book = Book.objects.get(id=book_id)
    book.stock_on_hand = max(0, book.stock_on_hand + delta)
    book.save(update_fields=["stock_on_hand"])
    return book


def _notify_stock_level(request, book):
    if book.is_low_stock:
        messages.warning(
            request,
            f"Low stock: '{book.title}' has {book.stock_on_hand} remaining "
            f"(reorder threshold {book.reorder_threshold}).",
        )
    else:
        messages.info(
            request,
            f"'{book.title}' now has {book.stock_on_hand} in stock.",
        )


@login_required
@permission_required("books.view_sale", raise_exception=True)
def sale_list(request):
    sales = Sale.objects.select_related("book", "book__category").order_by("-sale_date")

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
    form = SaleForm()

    if request.method == "POST":
        form = SaleForm(request.POST)

        if form.is_valid():
            book = form.cleaned_data["book"]
            quantity = form.cleaned_data["quantity"]

            if book.stock_on_hand < quantity:
                messages.error(
                    request,
                    f"Cannot record sale: '{book.title}' only has "
                    f"{book.stock_on_hand} in stock.",
                )
            else:
                sale = form.save()
                book = _adjust_stock(sale.book_id, -sale.quantity)
                messages.success(request, "Sale recorded.")
                _notify_stock_level(request, book)
                return redirect("sale_list")

    return render(request, "books/sale_form.html", {"form": form})


@login_required
@permission_required("books.change_sale", raise_exception=True)
def sale_update(request, id):
    sale = get_object_or_404(Sale, id=id)
    form = SaleForm(instance=sale)

    if request.method == "POST":
        previous_book_id = sale.book_id
        previous_quantity = sale.quantity

        form = SaleForm(request.POST, instance=sale)

        if form.is_valid():
            new_book = form.cleaned_data["book"]
            new_quantity = form.cleaned_data["quantity"]

            available = new_book.stock_on_hand
            if new_book.id == previous_book_id:
                available += previous_quantity

            if available < new_quantity:
                messages.error(
                    request,
                    f"Cannot update sale: '{new_book.title}' only has "
                    f"{available} available.",
                )
            else:
                sale = form.save()
                _adjust_stock(previous_book_id, previous_quantity)
                book = _adjust_stock(sale.book_id, -sale.quantity)
                messages.success(request, "Sale updated.")
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
    sale = get_object_or_404(Sale, id=id)

    if request.method == "POST":
        _adjust_stock(sale.book_id, sale.quantity)
        sale.delete()
        messages.success(request, "Sale deleted.")
        return redirect("sale_list")

    return render(
        request,
        "books/confirm_delete.html",
        {
            "object_type": "sale",
            "object_name": f"{sale.book.title} ({sale.sale_date})",
            "cancel_url": reverse("sale_list"),
        },
    )


@login_required
@permission_required("books.view_book", raise_exception=True)
def export_books_csv(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="rumi-press-books.csv"'

    writer = csv.writer(response)
    writer.writerow(BOOK_EXPORT_HEADERS)

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
    worksheet.append(BOOK_EXPORT_HEADERS)

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
    elements = [
        Paragraph("Rumi Press Books", styles["Title"]),
        Spacer(1, 12),
    ]

    rows = [BOOK_EXPORT_HEADERS]

    for row in _book_export_rows(_filtered_books_for_export(request)):
        rows.append([str(value) for value in row])

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
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
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
    writer.writerow(SALE_EXPORT_HEADERS)

    sales = Sale.objects.select_related("book", "book__category")

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
    worksheet.append(SALE_EXPORT_HEADERS)

    sales = Sale.objects.select_related("book", "book__category")

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
    elements = [
        Paragraph("Rumi Press Sales", styles["Title"]),
        Spacer(1, 12),
    ]

    sales = Sale.objects.select_related("book", "book__category")

    rows = [SALE_EXPORT_HEADERS]

    for row in _sale_export_rows(sales):
        rows.append([str(value) for value in row])

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
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
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
