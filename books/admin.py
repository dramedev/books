import json

from django.contrib import admin
from django.db.models import Count, Sum

from .models import Book, Category



@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):

    list_display = (
        "id",
        "name",
        "book_count",
    )

    search_fields = (
        "name",
    )

    ordering = (
        "name",
    )

    def book_count(self, obj):
        return obj.book_set.count()

    book_count.short_description = "Books"



@admin.register(Book)
class BookAdmin(admin.ModelAdmin):

    list_display = (
        "isbn",
        "title",
        "authors",
        "category",
        "publisher",
        "published_date",
        "distribution_expense",
    )


    search_fields = (
        "isbn",
        "title",
        "authors",
        "publisher",
    )


    list_filter = (
        "category",
        "publisher",
        "published_date",
    )

    autocomplete_fields = (
        "category",
    )

    date_hierarchy = "published_date"

    ordering = (
        "title",
    )

    list_per_page = 25





# Admin title

admin.site.site_header = "Rumi Press Admin"

admin.site.site_title = "Rumi Press"

admin.site.index_title = "Dashboard"





# keep original admin page

original_index = admin.site.index





def custom_index(request, extra_context=None):


    extra_context = extra_context or {}



    # cards

    extra_context["total_books"] = Book.objects.count()


    extra_context["total_categories"] = Category.objects.count()



    total = Book.objects.aggregate(
        total=Sum(
            "distribution_expense"
        )
    )


    extra_context["total_expenses"] = round(
        total["total"] or 0,
        2
    )

    top_category = (
        Book.objects
        .values(
            "category__name"
        )
        .annotate(
            total=Sum(
                "distribution_expense"
            )
        )
        .order_by(
            "-total"
        )
        .first()
    )

    extra_context["top_category"] = top_category

    extra_context["latest_books"] = (
        Book.objects
        .select_related(
            "category"
        )
        .order_by(
            "-id"
        )[:5]
    )

    extra_context["publisher_count"] = (
        Book.objects
        .values(
            "publisher"
        )
        .distinct()
        .count()
    )



    # chart data by category


    report = (
        Book.objects
        .values(
            "category__name"
        )
        .annotate(
            total=Sum(
                "distribution_expense"
            ),
            count=Count(
                "id"
            )
        )
        .order_by(
            "category__name"
        )
    )



    labels = []

    values = []



    for item in report:


        labels.append(
            item["category__name"]
        )


        values.append(
            float(
                item["total"]
            )
        )



    extra_context["labels"] = json.dumps(
        labels
    )

    extra_context["values"] = json.dumps(
        values
    )




    return original_index(
        request,
        extra_context
    )





# replace admin homepage

admin.site.index = custom_index
