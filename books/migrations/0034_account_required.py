import django.db.models.deletion
from django.db import migrations, models


OWNED_MODELS = [
    "category", "author", "book", "sale", "supplier", "reorder", "return",
    "customer", "invoice", "printrun", "royaltyrate", "royaltypayment",
    "stockadjustment", "location", "stocklevel", "integration",
]


class Migration(migrations.Migration):

    dependencies = [
        ("books", "0033_backfill_accounts"),
    ]

    operations = [
        migrations.AlterField(
            model_name=model_name,
            name="account",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="+", to="books.account"),
        )
        for model_name in OWNED_MODELS
    ] + [
        migrations.AlterUniqueTogether(
            name="author",
            unique_together={("account", "name")},
        ),
        migrations.AlterUniqueTogether(
            name="supplier",
            unique_together={("account", "name")},
        ),
        migrations.AlterUniqueTogether(
            name="customer",
            unique_together={("account", "name")},
        ),
        migrations.AlterUniqueTogether(
            name="location",
            unique_together={("account", "name")},
        ),
    ]
