from django.db import models


class Category(models.Model):
    name = models.CharField(max_length=100)


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


    authors = models.CharField(
        max_length=200
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
