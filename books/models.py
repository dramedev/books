from django.db import models


class Category(models.Model):
    name = models.CharField(max_length=100)


    def __str__(self):
        return self.name



class Author(models.Model):

    name = models.CharField(max_length=200, unique=True)


    class Meta:
        ordering = ["name"]


    def __str__(self):
        return self.name



class Book(models.Model):

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


    def __str__(self):
        return self.title



class Sale(models.Model):

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
