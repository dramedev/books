from django.conf import settings
from django.db import models
from django.utils import timezone


class Category(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_categories",
    )

    name = models.CharField(max_length=100)


    def __str__(self):
        return self.name



class Author(models.Model):

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_authors",
    )

    name = models.CharField(max_length=200)


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
        null=True
    )


    title = models.CharField(
        max_length=200
    )


    subtitle = models.CharField(
        max_length=200,
        blank=True
    )


    authors = models.ManyToManyField(
        Author,
        related_name="books",
        blank=True
    )


    publisher = models.CharField(
        max_length=200
    )


    published_date = models.DateField()


    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE
    )


    distribution_expense = models.DecimalField(
        max_digits=8,
        decimal_places=2
    )


    stock_on_hand = models.PositiveIntegerField(
        default=0
    )


    reorder_threshold = models.PositiveIntegerField(
        default=0
    )


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
        related_name="sales"
    )


    quantity = models.PositiveIntegerField()


    unit_price = models.DecimalField(
        max_digits=8,
        decimal_places=2
    )


    sale_date = models.DateField()


    channel = models.CharField(
        max_length=100,
        blank=True
    )


    class Meta:
        ordering = ["-sale_date"]


    def __str__(self):
        return f"{self.book.title} - {self.sale_date}"


    @property
    def revenue(self):
        return self.quantity * self.unit_price



class Profile(models.Model):

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile"
    )


    avatar = models.ImageField(
        upload_to="avatars/",
        blank=True,
        null=True
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
