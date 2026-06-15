from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.utils.translation import gettext_lazy as _

from .models import Author, Book, Category, Profile, Reorder, Sale


class BookForm(forms.ModelForm):

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)

        if user is not None:
            self.fields["category"].queryset = Category.objects.filter(owner=user)
            self.fields["authors"].queryset = Author.objects.filter(owner=user)

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
                    "placeholder": _("ISBN")
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
                    "placeholder": _("Category name")
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
                    "placeholder": _("Author name")
                }
            ),
        }


class SaleForm(forms.ModelForm):

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)

        if user is not None:
            self.fields["book"].queryset = Book.objects.filter(owner=user)

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
                    "placeholder": _("Channel (optional)")
                }
            ),
        }



class ReorderForm(forms.ModelForm):

    class Meta:
        model = Reorder

        fields = [
            "quantity",
            "note",
        ]

        widgets = {
            "quantity": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": "1"
                }
            ),
            "note": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": _("Note (optional)")
                }
            ),
        }



class ProfileForm(forms.ModelForm):

    class Meta:
        model = Profile

        fields = [
            "avatar",
        ]

        widgets = {
            "avatar": forms.FileInput(
                attrs={
                    "class": "form-control"
                }
            ),
        }



class SignupForm(forms.Form):

    username = forms.CharField(
        max_length=150,
        label=_("Username"),
        widget=forms.TextInput(attrs={"class": "form-control", "autofocus": True}),
    )

    email = forms.EmailField(
        label=_("Email"),
        widget=forms.EmailInput(attrs={"class": "form-control"}),
    )

    password1 = forms.CharField(
        label=_("Password"),
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )

    password2 = forms.CharField(
        label=_("Confirm password"),
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )

    def clean_username(self):
        username = self.cleaned_data["username"]

        if get_user_model().objects.filter(username__iexact=username).exists():
            raise forms.ValidationError(_("That username is already taken."))

        return username

    def clean_email(self):
        email = self.cleaned_data["email"]

        if get_user_model().objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(_("An account with that email already exists."))

        return email

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")

        if password1 and password2 and password1 != password2:
            raise forms.ValidationError(_("The two password fields didn't match."))

        if password1:
            validate_password(password1)

        return cleaned_data



class VerifyEmailForm(forms.Form):

    code = forms.CharField(
        max_length=6,
        label=_("Verification code"),
        widget=forms.TextInput(attrs={"class": "form-control", "autofocus": True}),
    )



class RedeemAccessCodeForm(forms.Form):

    code = forms.CharField(
        max_length=12,
        label=_("Access code"),
        widget=forms.TextInput(attrs={"class": "form-control", "autofocus": True}),
    )
