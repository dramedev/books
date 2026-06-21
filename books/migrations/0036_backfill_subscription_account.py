from django.db import migrations


def backfill_subscription_account(apps, schema_editor):
    Subscription = apps.get_model("books", "Subscription")
    AccountMembership = apps.get_model("books", "AccountMembership")

    membership_by_user_id = dict(
        AccountMembership.objects.values_list("user_id", "account_id")
    )

    for subscription in Subscription.objects.all():
        account_id = membership_by_user_id.get(subscription.user_id)
        if account_id is not None:
            subscription.account_id = account_id
            subscription.save(update_fields=["account"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("books", "0035_subscription_account"),
    ]

    operations = [
        migrations.RunPython(backfill_subscription_account, noop_reverse),
    ]
