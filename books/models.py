from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


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


    class Meta:
        ordering = ["-sale_date"]


    def __str__(self):
        return f"{self.book.title} - {self.sale_date}"


    @property
    def revenue(self):
        return self.quantity * self.unit_price



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
