from django.db import migrations


OWNED_MODELS = [
    "Category", "Author", "Book", "Sale", "Supplier", "Reorder", "Return",
    "Customer", "Invoice", "PrintRun", "RoyaltyRate", "RoyaltyPayment",
    "StockAdjustment", "Location", "StockLevel", "Integration",
]


def backfill_accounts(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Account = apps.get_model("books", "Account")
    AccountMembership = apps.get_model("books", "AccountMembership")

    account_id_by_user_id = {}

    for user in User.objects.all():
        account = Account.objects.create(name=user.username)
        AccountMembership.objects.create(account=account, user=user, role="Admin")
        account_id_by_user_id[user.id] = account.id

    for model_name in OWNED_MODELS:
        Model = apps.get_model("books", model_name)
        for user_id, account_id in account_id_by_user_id.items():
            Model.objects.filter(owner_id=user_id).update(account_id=account_id)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("books", "0032_account_author_account_book_account_category_account_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_accounts, noop_reverse),
    ]
