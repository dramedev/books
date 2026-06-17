from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.utils.translation import gettext_lazy as _

from .models import Author, Book, Category, Invoice, InvoiceItem, PrintRun, Profile, Reorder, Return, RoyaltyRate, Sale, StockAdjustment, Supplier


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
            "currency",
            "tax_rate",
            "sale_date",
            "channel",
        ]

        widgets = {
            "book": forms.Select(attrs={"class": "form-select"}),
            "quantity": forms.NumberInput(attrs={"class": "form-control", "min": "1"}),
            "unit_price": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "currency": forms.Select(attrs={"class": "form-select"}),
            "tax_rate": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.01"}),
            "sale_date": forms.DateInput(format="%Y-%m-%d", attrs={"class": "form-control", "type": "date"}),
            "channel": forms.TextInput(attrs={"class": "form-control", "placeholder": _("Channel (optional)")}),
        }



class ReorderForm(forms.ModelForm):

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)

        if user is not None:
            self.fields["supplier"].queryset = Supplier.objects.filter(owner=user)

        self.fields["supplier"].required = False
        self.fields["supplier"].empty_label = _("No supplier")

    class Meta:
        model = Reorder

        fields = [
            "supplier",
            "quantity",
            "unit_cost",
            "note",
        ]

        widgets = {
            "supplier": forms.Select(
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
            "unit_cost": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": "0",
                    "step": "0.01"
                }
            ),
            "note": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": _("Note (optional)")
                }
            ),
        }



class SupplierForm(forms.ModelForm):

    class Meta:
        model = Supplier

        fields = [
            "name",
            "contact_name",
            "email",
            "phone",
            "notes",
        ]

        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "form-control"
                }
            ),
            "contact_name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": _("Contact name (optional)")
                }
            ),
            "email": forms.EmailInput(
                attrs={
                    "class": "form-control"
                }
            ),
            "phone": forms.TextInput(
                attrs={
                    "class": "form-control"
                }
            ),
            "notes": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": _("Notes (optional)")
                }
            ),
        }



class ReturnForm(forms.ModelForm):

    class Meta:
        model = Return

        fields = [
            "quantity",
            "reason",
            "return_date",
        ]

        widgets = {
            "quantity": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": "1"
                }
            ),
            "reason": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": _("Reason (optional)")
                }
            ),
            "return_date": forms.DateInput(
                format="%Y-%m-%d",
                attrs={
                    "class": "form-control",
                    "type": "date"
                }
            ),
        }



class StockAdjustmentForm(forms.ModelForm):

    class Meta:
        model = StockAdjustment

        fields = [
            "change",
            "reason",
            "note",
        ]

        widgets = {
            "change": forms.NumberInput(
                attrs={
                    "class": "form-control",
                }
            ),
            "reason": forms.Select(
                attrs={
                    "class": "form-select",
                }
            ),
            "note": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": _("Note (optional)")
                }
            ),
        }

        help_texts = {
            "change": _("Use a positive number to add stock, negative to remove."),
        }

    def clean_change(self):
        change = self.cleaned_data["change"]

        if change == 0:
            raise forms.ValidationError(_("Change cannot be zero."))

        return change



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



class InvoiceForm(forms.ModelForm):

    class Meta:
        model = Invoice

        fields = [
            "customer_name",
            "customer_email",
            "customer_address",
            "invoice_date",
            "due_date",
            "currency",
            "note",
        ]

        widgets = {
            "customer_name": forms.TextInput(attrs={"class": "form-control"}),
            "customer_email": forms.EmailInput(attrs={"class": "form-control"}),
            "customer_address": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "invoice_date": forms.DateInput(format="%Y-%m-%d", attrs={"class": "form-control", "type": "date"}),
            "due_date": forms.DateInput(format="%Y-%m-%d", attrs={"class": "form-control", "type": "date"}),
            "currency": forms.Select(attrs={"class": "form-select"}),
            "note": forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": _("Note (optional)")}),
        }



class InvoiceItemForm(forms.ModelForm):

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            self.fields["book"].queryset = Book.objects.filter(owner=user)
        self.fields["book"].required = False
        self.fields["book"].empty_label = _("No book (custom item)")

    class Meta:
        model = InvoiceItem

        fields = ["book", "description", "quantity", "unit_price", "tax_rate"]

        widgets = {
            "book": forms.Select(attrs={"class": "form-select"}),
            "description": forms.TextInput(attrs={"class": "form-control"}),
            "quantity": forms.NumberInput(attrs={"class": "form-control", "min": "1"}),
            "unit_price": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "tax_rate": forms.NumberInput(attrs={"class": "form-control", "min": "0", "step": "0.01"}),
        }



class PrintRunForm(forms.ModelForm):

    class Meta:
        model = PrintRun

        fields = ["quantity", "cost_per_unit", "run_date", "note"]

        widgets = {
            "quantity": forms.NumberInput(attrs={"class": "form-control", "min": "1"}),
            "cost_per_unit": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "run_date": forms.DateInput(format="%Y-%m-%d", attrs={"class": "form-control", "type": "date"}),
            "note": forms.TextInput(attrs={"class": "form-control", "placeholder": _("Note (optional)")}),
        }



class RoyaltyRateForm(forms.ModelForm):

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            self.fields["book"].queryset = Book.objects.filter(owner=user)
            self.fields["author"].queryset = Author.objects.filter(owner=user)

    class Meta:
        model = RoyaltyRate

        fields = ["book", "author", "rate", "effective_from", "note"]

        widgets = {
            "book": forms.Select(attrs={"class": "form-select"}),
            "author": forms.Select(attrs={"class": "form-select"}),
            "rate": forms.NumberInput(attrs={"class": "form-control", "min": "0", "max": "100", "step": "0.01"}),
            "effective_from": forms.DateInput(format="%Y-%m-%d", attrs={"class": "form-control", "type": "date"}),
            "note": forms.TextInput(attrs={"class": "form-control", "placeholder": _("Note (optional)")}),
        }
