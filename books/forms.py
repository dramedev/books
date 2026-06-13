from django import forms
from .models import Book, Category


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
            "authors": forms.TextInput(
                attrs={
                    "class": "form-control"
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
