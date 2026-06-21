import json
import secrets
import string

from django import forms
from django.conf import settings
from django.contrib import admin
from django.core.mail import send_mail
from django.db.models import Count, F, Sum

from .models import (
    AccessCode, Account, AccountInvitation, AccountMembership,
    Author, Book, Category, PendingActivation, Sale, StockAdjustment,
)


def _safe_json(value):
    """json.dumps for embedding in a <script> block via the |safe filter.

    Escapes "</" so user-entered strings (category names, etc.) can't contain
    a literal "</script>" that would terminate the tag early and inject HTML.
    """
    return json.dumps(value).replace("</", "<\\/")



@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):

    list_display = ("id", "name", "created_at")
    search_fields = ("name",)


@admin.register(AccountMembership)
class AccountMembershipAdmin(admin.ModelAdmin):

    list_display = ("id", "account", "user", "role", "created_at")
    list_filter = ("role",)
    search_fields = ("account__name", "user__username", "user__email")


@admin.register(AccountInvitation)
class AccountInvitationAdmin(admin.ModelAdmin):

    list_display = ("id", "account", "email", "role", "accepted_at", "expires_at")
    list_filter = ("role",)
    search_fields = ("account__name", "email")


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



@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):

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
        return obj.books.count()

    book_count.short_description = "Books"



@admin.register(Book)
class BookAdmin(admin.ModelAdmin):

    list_display = (
        "isbn",
        "title",
        "author_names",
        "category",
        "publisher",
        "published_date",
        "distribution_expense",
        "stock_on_hand",
        "reorder_threshold",
    )


    search_fields = (
        "isbn",
        "title",
        "authors__name",
        "publisher",
    )


    list_filter = (
        "category",
        "publisher",
        "published_date",
    )

    list_editable = (
        "stock_on_hand",
        "reorder_threshold",
    )

    autocomplete_fields = (
        "category",
        "authors",
    )

    date_hierarchy = "published_date"

    ordering = (
        "title",
    )

    list_per_page = 25

    def author_names(self, obj):
        return ", ".join(obj.authors.values_list("name", flat=True))

    author_names.short_description = "Authors"



@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):

    list_display = (
        "id",
        "book",
        "quantity",
        "unit_price",
        "revenue",
        "sale_date",
        "channel",
    )

    search_fields = (
        "book__title",
        "channel",
    )

    list_filter = (
        "channel",
        "sale_date",
    )

    autocomplete_fields = (
        "book",
    )

    date_hierarchy = "sale_date"

    ordering = (
        "-sale_date",
    )

    list_per_page = 25

    def revenue(self, obj):
        return obj.revenue

    revenue.short_description = "Revenue"



@admin.register(StockAdjustment)
class StockAdjustmentAdmin(admin.ModelAdmin):

    list_display = (
        "id",
        "book",
        "change",
        "resulting_stock",
        "reason",
        "created_at",
    )

    list_filter = (
        "reason",
        "created_at",
    )

    search_fields = (
        "book__title",
        "note",
    )

    autocomplete_fields = (
        "book",
    )

    date_hierarchy = "created_at"

    ordering = (
        "-created_at",
    )

    list_per_page = 25



class AccessCodeAdminForm(forms.ModelForm):

    class Meta:
        model = AccessCode
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["code"].required = False

    def clean_code(self):
        code = self.cleaned_data.get("code")
        if not code:
            code = "".join(
                secrets.choice(string.ascii_uppercase + string.digits)
                for _ in range(10)
            )
        return code


@admin.register(AccessCode)
class AccessCodeAdmin(admin.ModelAdmin):

    form = AccessCodeAdminForm

    list_display = (
        "code",
        "label",
        "recipient_email",
        "is_used",
        "used_by",
        "used_at",
        "expires_at",
        "created_at",
    )

    list_filter = (
        "is_used",
    )

    search_fields = (
        "code",
        "label",
        "recipient_email",
        "used_by__username",
    )

    readonly_fields = (
        "is_used",
        "used_by",
        "used_at",
        "created_at",
    )

    def save_model(self, request, obj, form, change):
        is_new = obj.pk is None

        super().save_model(request, obj, form, change)

        if is_new and obj.recipient_email:
            send_mail(
                subject="Your RumiPress access code",
                message=(
                    "Welcome to RumiPress!\n\n"
                    f"Your access code is: {obj.code}\n\n"
                    "Enter this code on the activation page after verifying "
                    "your email to activate your account."
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[obj.recipient_email],
                fail_silently=True,
            )



@admin.register(PendingActivation)
class PendingActivationAdmin(admin.ModelAdmin):

    list_display = (
        "user",
        "user_email",
    )

    search_fields = (
        "user__username",
        "user__email",
    )

    readonly_fields = (
        "user",
        "email_verified",
        "access_code_redeemed",
    )

    def user_email(self, obj):
        return obj.user.email

    user_email.short_description = "Email"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .filter(email_verified=True, access_code_redeemed=False)
        )



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


    extra_context["low_stock_books"] = (
        Book.objects
        .filter(stock_on_hand__lte=F("reorder_threshold"))
        .select_related("category")
        .order_by("stock_on_hand")[:5]
    )


    extra_context["low_stock_count"] = (
        Book.objects
        .filter(stock_on_hand__lte=F("reorder_threshold"))
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



    extra_context["labels"] = _safe_json(
        labels
    )

    extra_context["values"] = _safe_json(
        values
    )




    return original_index(
        request,
        extra_context
    )





# replace admin homepage

admin.site.index = custom_index
