from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


AVATAR_MAX_SIZE_BYTES = 2 * 1024 * 1024


def validate_avatar_size(file):
    if file.size > AVATAR_MAX_SIZE_BYTES:
        raise ValidationError(
            _("Image must be smaller than %(max_mb)s MB.") % {"max_mb": AVATAR_MAX_SIZE_BYTES // (1024 * 1024)}
        )


CURRENCY_CHOICES = [
    ("USD", "USD"), ("EUR", "EUR"), ("GBP", "GBP"),
    ("SAR", "SAR"), ("AED", "AED"), ("MAD", "MAD"),
    ("DZD", "DZD"), ("TND", "TND"), ("EGP", "EGP"), ("TRY", "TRY"),
]


class Category(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_categories",
    )

    name = models.CharField(max_length=100, verbose_name=_("Name"))


    def __str__(self):
        return self.name



class Author(models.Model):

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_authors",
    )

    name = models.CharField(max_length=200, verbose_name=_("Name"))


    class Meta:
        ordering = ["name"]
        unique_together = ("owner", "name")


    def __str__(self):
        return self.name



class Book(models.Model):

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_books",
    )

    isbn = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        verbose_name=_("ISBN")
    )


    title = models.CharField(
        max_length=200,
        verbose_name=_("Title")
    )


    subtitle = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_("Subtitle")
    )


    authors = models.ManyToManyField(
        Author,
        related_name="books",
        blank=True,
        verbose_name=_("Authors")
    )


    publisher = models.CharField(
        max_length=200,
        verbose_name=_("Publisher")
    )


    published_date = models.DateField(verbose_name=_("Published date"))


    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        verbose_name=_("Category")
    )


    distribution_expense = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        verbose_name=_("Distribution expense")
    )


    stock_on_hand = models.PositiveIntegerField(
        default=0,
        verbose_name=_("Stock on hand")
    )


    reorder_threshold = models.PositiveIntegerField(
        default=0,
        verbose_name=_("Reorder threshold")
    )


    low_stock_alert_sent = models.BooleanField(default=False, editable=False)


    def __str__(self):
        return self.title


    @property
    def is_low_stock(self):
        return self.stock_on_hand <= self.reorder_threshold



class Sale(models.Model):

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_sales",
    )

    book = models.ForeignKey(
        Book,
        on_delete=models.CASCADE,
        related_name="sales",
        verbose_name=_("Book")
    )


    quantity = models.PositiveIntegerField(verbose_name=_("Quantity"))


    unit_price = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        verbose_name=_("Unit price")
    )


    sale_date = models.DateField(verbose_name=_("Sale date"))


    channel = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("Channel")
    )


    currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        default="USD",
        verbose_name=_("Currency")
    )


    tax_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        verbose_name=_("Tax rate (%)")
    )


    class Meta:
        ordering = ["-sale_date"]


    def __str__(self):
        return f"{self.book.title} - {self.sale_date}"


    @property
    def revenue(self):
        return self.quantity * self.unit_price


    @property
    def tax_amount(self):
        return self.quantity * self.unit_price * self.tax_rate / Decimal(100)


    @property
    def total(self):
        return self.revenue + self.tax_amount



class Supplier(models.Model):

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_suppliers",
    )

    name = models.CharField(max_length=200, verbose_name=_("Name"))

    contact_name = models.CharField(max_length=200, blank=True, verbose_name=_("Contact name"))

    email = models.EmailField(blank=True, verbose_name=_("Email"))

    phone = models.CharField(max_length=50, blank=True, verbose_name=_("Phone"))

    notes = models.CharField(max_length=200, blank=True, verbose_name=_("Notes"))


    class Meta:
        ordering = ["name"]
        unique_together = ("owner", "name")


    def __str__(self):
        return self.name



class Reorder(models.Model):

    STATUS_PENDING = "pending"
    STATUS_ORDERED = "ordered"
    STATUS_RECEIVED = "received"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_PENDING, _("Pending")),
        (STATUS_ORDERED, _("Ordered")),
        (STATUS_RECEIVED, _("Received")),
        (STATUS_CANCELLED, _("Cancelled")),
    ]

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_reorders",
    )

    book = models.ForeignKey(
        Book,
        on_delete=models.CASCADE,
        related_name="reorders",
        verbose_name=_("Book")
    )

    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reorders",
        verbose_name=_("Supplier")
    )


    quantity = models.PositiveIntegerField(verbose_name=_("Quantity"))


    unit_cost = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=0,
        verbose_name=_("Unit cost")
    )


    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        verbose_name=_("Status")
    )


    note = models.CharField(max_length=200, blank=True, verbose_name=_("Note"))


    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Created"))

    received_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Received"))


    class Meta:
        ordering = ["-created_at"]


    def __str__(self):
        return f"{self.book.title} - {self.get_status_display()}"


    @property
    def status_badge_class(self):
        return {
            self.STATUS_PENDING: "bg-warning",
            self.STATUS_ORDERED: "bg-info",
            self.STATUS_RECEIVED: "bg-success",
            self.STATUS_CANCELLED: "bg-secondary",
        }.get(self.status, "bg-secondary")


    @property
    def total_cost(self):
        return self.quantity * self.unit_cost



class Return(models.Model):

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_returns",
    )

    sale = models.ForeignKey(
        Sale,
        on_delete=models.CASCADE,
        related_name="returns",
        verbose_name=_("Sale")
    )


    quantity = models.PositiveIntegerField(verbose_name=_("Quantity"))


    reason = models.CharField(max_length=200, blank=True, verbose_name=_("Reason"))


    return_date = models.DateField(verbose_name=_("Return date"))


    class Meta:
        ordering = ["-return_date"]


    def __str__(self):
        return f"{self.sale.book.title} - {self.return_date}"


    @property
    def refund_amount(self):
        return self.quantity * self.sale.unit_price



class Customer(models.Model):

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_customers",
    )

    name = models.CharField(max_length=200, verbose_name=_("Name"))

    email = models.EmailField(blank=True, verbose_name=_("Email"))

    phone = models.CharField(max_length=50, blank=True, verbose_name=_("Phone"))

    address = models.TextField(blank=True, verbose_name=_("Address"))

    notes = models.CharField(max_length=200, blank=True, verbose_name=_("Notes"))


    class Meta:
        ordering = ["name"]
        unique_together = ("owner", "name")


    def __str__(self):
        return self.name



class CustomerLoginToken(models.Model):

    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="login_tokens",
    )

    token = models.CharField(max_length=64, unique=True)

    created_at = models.DateTimeField(auto_now_add=True)

    expires_at = models.DateTimeField()

    used_at = models.DateTimeField(null=True, blank=True)


    def __str__(self):
        return f"Login token for {self.customer.name}"


    @property
    def is_valid(self):
        return self.used_at is None and timezone.now() <= self.expires_at



class Invoice(models.Model):

    STATUS_DRAFT = "draft"
    STATUS_SENT = "sent"
    STATUS_PAID = "paid"

    STATUS_CHOICES = [
        (STATUS_DRAFT, _("Draft")),
        (STATUS_SENT, _("Sent")),
        (STATUS_PAID, _("Paid")),
    ]

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_invoices",
    )

    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices",
        verbose_name=_("Customer"),
    )

    invoice_number = models.CharField(max_length=20, blank=True, verbose_name=_("Invoice number"))

    customer_name = models.CharField(max_length=200, verbose_name=_("Customer name"))

    customer_email = models.EmailField(blank=True, verbose_name=_("Customer email"))

    customer_address = models.TextField(blank=True, verbose_name=_("Customer address"))

    invoice_date = models.DateField(verbose_name=_("Invoice date"))

    due_date = models.DateField(null=True, blank=True, verbose_name=_("Due date"))

    currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        default="USD",
        verbose_name=_("Currency"),
    )

    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        verbose_name=_("Status"),
    )

    note = models.TextField(blank=True, verbose_name=_("Note"))

    stripe_payment_intent_id = models.CharField(max_length=200, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)


    class Meta:
        ordering = ["-created_at"]


    def __str__(self):
        return f"{self.invoice_number or 'Draft'} – {self.customer_name}"


    @property
    def subtotal(self):
        return sum((item.subtotal for item in self.items.all()), Decimal(0))


    @property
    def tax_total(self):
        return sum((item.tax_amount for item in self.items.all()), Decimal(0))


    @property
    def grand_total(self):
        return sum((item.total for item in self.items.all()), Decimal(0))


    @property
    def is_overdue(self):
        return (
            self.due_date is not None
            and self.status != self.STATUS_PAID
            and self.due_date < timezone.now().date()
        )

    @property
    def status_badge_class(self):
        return {
            self.STATUS_DRAFT: "bg-secondary",
            self.STATUS_SENT: "bg-info",
            self.STATUS_PAID: "bg-success",
        }.get(self.status, "bg-secondary")



class InvoiceItem(models.Model):

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name=_("Invoice"),
    )

    book = models.ForeignKey(
        Book,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoice_items",
        verbose_name=_("Book"),
    )

    description = models.CharField(max_length=200, verbose_name=_("Description"))

    quantity = models.PositiveIntegerField(default=1, verbose_name=_("Quantity"))

    unit_price = models.DecimalField(max_digits=8, decimal_places=2, verbose_name=_("Unit price"))

    tax_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        verbose_name=_("Tax rate (%)"),
    )


    def __str__(self):
        return self.description


    @property
    def subtotal(self):
        return self.quantity * self.unit_price


    @property
    def tax_amount(self):
        amount = self.subtotal * self.tax_rate / Decimal(100)
        return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


    @property
    def total(self):
        return self.subtotal + self.tax_amount



class PrintRun(models.Model):

    STATUS_PENDING = "pending"
    STATUS_COMPLETED = "completed"

    STATUS_CHOICES = [
        (STATUS_PENDING, _("Pending")),
        (STATUS_COMPLETED, _("Completed")),
    ]

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_print_runs",
    )

    book = models.ForeignKey(
        Book,
        on_delete=models.CASCADE,
        related_name="print_runs",
        verbose_name=_("Book"),
    )

    quantity = models.PositiveIntegerField(verbose_name=_("Quantity"))

    cost_per_unit = models.DecimalField(max_digits=8, decimal_places=2, verbose_name=_("Cost per unit"))

    run_date = models.DateField(verbose_name=_("Run date"))

    note = models.CharField(max_length=200, blank=True, verbose_name=_("Note"))

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        verbose_name=_("Status"),
    )

    completed_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Completed at"))


    class Meta:
        ordering = ["-run_date"]


    def __str__(self):
        return f"{self.book.title} – {self.run_date}"


    @property
    def total_cost(self):
        return self.quantity * self.cost_per_unit



class RoyaltyRate(models.Model):

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_royalty_rates",
    )

    book = models.ForeignKey(
        Book,
        on_delete=models.CASCADE,
        related_name="royalty_rates",
        verbose_name=_("Book"),
    )

    author = models.ForeignKey(
        Author,
        on_delete=models.CASCADE,
        related_name="royalty_rates",
        verbose_name=_("Author"),
    )

    rate = models.DecimalField(max_digits=5, decimal_places=2, verbose_name=_("Rate (%)"))

    effective_from = models.DateField(verbose_name=_("Effective from"))

    note = models.CharField(max_length=200, blank=True, verbose_name=_("Note"))


    class Meta:
        ordering = ["-effective_from"]


    def __str__(self):
        return f"{self.book.title} – {self.author.name} – {self.rate}%"



class RoyaltyPayment(models.Model):

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_royalty_payments",
    )

    author = models.ForeignKey(
        Author,
        on_delete=models.CASCADE,
        related_name="royalty_payments",
        verbose_name=_("Author"),
    )

    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name=_("Amount"))

    currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        default="USD",
        verbose_name=_("Currency"),
    )

    payment_date = models.DateField(verbose_name=_("Payment date"))

    note = models.CharField(max_length=200, blank=True, verbose_name=_("Note"))


    class Meta:
        ordering = ["-payment_date"]


    def __str__(self):
        return f"{self.author.name} – {self.amount} {self.currency} ({self.payment_date})"



class StockAdjustment(models.Model):

    REASON_DAMAGED = "damaged"
    REASON_LOST = "lost"
    REASON_FOUND = "found"
    REASON_CORRECTION = "correction"
    REASON_PRODUCTION = "production"
    REASON_OTHER = "other"

    REASON_CHOICES = [
        (REASON_DAMAGED, _("Damaged")),
        (REASON_LOST, _("Lost")),
        (REASON_FOUND, _("Found")),
        (REASON_CORRECTION, _("Correction")),
        (REASON_PRODUCTION, _("Print run")),
        (REASON_OTHER, _("Other")),
    ]

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_stock_adjustments",
    )

    book = models.ForeignKey(
        Book,
        on_delete=models.CASCADE,
        related_name="stock_adjustments",
        verbose_name=_("Book")
    )

    change = models.IntegerField(verbose_name=_("Change"))

    resulting_stock = models.PositiveIntegerField(verbose_name=_("Resulting stock"))

    reason = models.CharField(
        max_length=20,
        choices=REASON_CHOICES,
        default=REASON_CORRECTION,
        verbose_name=_("Reason")
    )

    note = models.CharField(max_length=200, blank=True, verbose_name=_("Note"))

    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Date"))


    class Meta:
        ordering = ["-created_at"]


    def __str__(self):
        return f"{self.book.title} - {self.change:+d}"



class Location(models.Model):

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_locations",
    )

    name = models.CharField(max_length=200, verbose_name=_("Name"))

    address = models.TextField(blank=True, verbose_name=_("Address"))

    is_default = models.BooleanField(default=False, verbose_name=_("Default location"))


    class Meta:
        ordering = ["name"]
        unique_together = ("owner", "name")


    def __str__(self):
        return self.name



class StockLevel(models.Model):

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_stock_levels",
    )

    book = models.ForeignKey(
        Book,
        on_delete=models.CASCADE,
        related_name="stock_levels",
        verbose_name=_("Book"),
    )

    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name="stock_levels",
        verbose_name=_("Location"),
    )

    quantity = models.PositiveIntegerField(default=0, verbose_name=_("Quantity"))


    class Meta:
        unique_together = ("book", "location")


    def __str__(self):
        return f"{self.book.title} @ {self.location.name}: {self.quantity}"



class Integration(models.Model):

    PLATFORM_SHOPIFY = "shopify"
    PLATFORM_AMAZON = "amazon"
    PLATFORM_STRIPE = "stripe"

    PLATFORM_CHOICES = [
        (PLATFORM_SHOPIFY, _("Shopify")),
        (PLATFORM_AMAZON, _("Amazon")),
        (PLATFORM_STRIPE, _("Stripe")),
    ]

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_integrations",
    )

    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES, verbose_name=_("Platform"))

    name = models.CharField(max_length=100, verbose_name=_("Name"))

    store_url = models.CharField(max_length=200, blank=True, verbose_name=_("Store URL"))

    api_key = models.CharField(max_length=200, blank=True, verbose_name=_("API key"))

    api_secret = models.CharField(max_length=200, blank=True, verbose_name=_("API secret"))

    webhook_secret = models.CharField(max_length=200, blank=True, verbose_name=_("Webhook secret"))

    is_active = models.BooleanField(default=True, verbose_name=_("Active"))

    orders_synced = models.PositiveIntegerField(default=0)

    last_synced_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)


    class Meta:
        ordering = ["-created_at"]


    def __str__(self):
        return f"{self.name} ({self.get_platform_display()})"



class Profile(models.Model):

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile"
    )


    avatar = models.ImageField(
        upload_to="avatars/",
        blank=True,
        null=True,
        validators=[validate_avatar_size],
        verbose_name=_("Avatar")
    )


    email_verified = models.BooleanField(default=False)

    verification_code = models.CharField(max_length=6, blank=True)

    verification_code_expires_at = models.DateTimeField(null=True, blank=True)

    access_code_redeemed = models.BooleanField(default=False)


    def __str__(self):
        return f"{self.user.username} profile"


class PendingActivation(Profile):

    class Meta:
        proxy = True
        verbose_name = "Pending activation"
        verbose_name_plural = "Pending activations"


class AccessCode(models.Model):

    code = models.CharField(max_length=12, unique=True)

    label = models.CharField(max_length=100, blank=True)

    recipient_email = models.EmailField(blank=True)

    is_used = models.BooleanField(default=False)

    used_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="redeemed_access_code",
    )

    used_at = models.DateTimeField(null=True, blank=True)

    expires_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)


    def __str__(self):
        return self.code


    @property
    def is_expired(self):
        return bool(self.expires_at and timezone.now() > self.expires_at)


    @property
    def is_valid(self):
        return not self.is_used and not self.is_expired



class Subscription(models.Model):

    STATUS_TRIALING = "trialing"
    STATUS_ACTIVE = "active"
    STATUS_PAST_DUE = "past_due"
    STATUS_CANCELED = "canceled"
    STATUS_INCOMPLETE = "incomplete"
    STATUS_UNPAID = "unpaid"

    STATUS_CHOICES = [
        (STATUS_TRIALING, _("Trialing")),
        (STATUS_ACTIVE, _("Active")),
        (STATUS_PAST_DUE, _("Past due")),
        (STATUS_CANCELED, _("Canceled")),
        (STATUS_INCOMPLETE, _("Incomplete")),
        (STATUS_UNPAID, _("Unpaid")),
    ]

    GOOD_STANDING_STATUSES = (STATUS_TRIALING, STATUS_ACTIVE)

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscription",
    )

    stripe_customer_id = models.CharField(max_length=200, blank=True)

    stripe_subscription_id = models.CharField(max_length=200, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_INCOMPLETE)

    trial_end = models.DateTimeField(null=True, blank=True)

    current_period_end = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    updated_at = models.DateTimeField(auto_now=True)


    def __str__(self):
        return f"{self.user.username} subscription ({self.status})"


    @property
    def is_in_good_standing(self):
        return self.status in self.GOOD_STANDING_STATUSES
