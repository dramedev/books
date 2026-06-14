from django import forms
from .models import Author, Book, Category, Sale


class BookForm(forms.ModelForm):

    class Meta:
        model = Book

        fields = [
            "isbn",
            "title",
            "subtitle",
            "authors",
            "publisher",
            "published_date",
            "category",
            "distribution_expense",
            "stock_on_hand",
            "reorder_threshold",
        ]

        widgets = {
            "isbn": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "ISBN"
                }
            ),
            "title": forms.TextInput(
                attrs={
                    "class": "form-control"
                }
            ),
            "subtitle": forms.TextInput(
                attrs={
                    "class": "form-control"
                }
            ),
            "authors": forms.SelectMultiple(
                attrs={
                    "class": "form-select",
                    "size": 6
                }
            ),
            "publisher": forms.TextInput(
                attrs={
                    "class": "form-control"
                }
            ),
            "published_date": forms.DateInput(
                format="%Y-%m-%d",
                attrs={
                    "class": "form-control",
                    "type": "date"
                }
            ),
            "category": forms.Select(
                attrs={
                    "class": "form-select"
                }
            ),
            "distribution_expense": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "step": "0.01"
                }
            ),
            "stock_on_hand": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": "0"
                }
            ),
            "reorder_threshold": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": "0"
                }
            ),
        }


class CategoryForm(forms.ModelForm):

    class Meta:
        model = Category

        fields = [
            "name",
        ]

        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Category name"
                }
            ),
        }


class AuthorForm(forms.ModelForm):

    class Meta:
        model = Author

        fields = [
            "name",
        ]

        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Author name"
                }
            ),
        }


class SaleForm(forms.ModelForm):

    class Meta:
        model = Sale

        fields = [
            "book",
            "quantity",
            "unit_price",
            "sale_date",
            "channel",
        ]

        widgets = {
            "book": forms.Select(
                attrs={
                    "class": "form-select"
                }
            ),
            "quantity": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": "1"
                }
            ),
            "unit_price": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "step": "0.01"
                }
            ),
            "sale_date": forms.DateInput(
                format="%Y-%m-%d",
                attrs={
                    "class": "form-control",
                    "type": "date"
                }
            ),
            "channel": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Channel (optional)"
                }
            ),
        }
