from django.db import migrations

from books.fields import decrypt_value, encrypt_value


SECRET_FIELDS = ("api_key", "api_secret", "webhook_secret")


def encrypt_existing_secrets(apps, schema_editor):
    Integration = apps.get_model("books", "Integration")

    for integration in Integration.objects.all():
        changed = False
        for field_name in SECRET_FIELDS:
            value = getattr(integration, field_name)
            if value:
                setattr(integration, field_name, encrypt_value(value))
                changed = True
        if changed:
            integration.save(update_fields=SECRET_FIELDS)


def decrypt_existing_secrets(apps, schema_editor):
    Integration = apps.get_model("books", "Integration")

    for integration in Integration.objects.all():
        changed = False
        for field_name in SECRET_FIELDS:
            value = getattr(integration, field_name)
            if value:
                setattr(integration, field_name, decrypt_value(value))
                changed = True
        if changed:
            integration.save(update_fields=SECRET_FIELDS)


class Migration(migrations.Migration):

    dependencies = [
        ("books", "0038_alter_integration_api_key_and_more"),
    ]

    operations = [
        migrations.RunPython(encrypt_existing_secrets, decrypt_existing_secrets),
    ]
