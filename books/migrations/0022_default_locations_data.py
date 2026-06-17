from django.db import migrations


def create_default_locations(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    Book = apps.get_model('books', 'Book')
    Location = apps.get_model('books', 'Location')
    StockLevel = apps.get_model('books', 'StockLevel')

    for user in User.objects.all():
        if not Book.objects.filter(owner=user).exists():
            continue

        location = Location.objects.create(
            owner=user,
            name='Main Warehouse',
            is_default=True,
        )

        for book in Book.objects.filter(owner=user, stock_on_hand__gt=0):
            StockLevel.objects.create(
                owner=user,
                book=book,
                location=location,
                quantity=book.stock_on_hand,
            )


def reverse_default_locations(apps, schema_editor):
    Location = apps.get_model('books', 'Location')
    Location.objects.filter(name='Main Warehouse', is_default=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('books', '0021_location_stocklevel'),
    ]

    operations = [
        migrations.RunPython(create_default_locations, reverse_default_locations),
    ]
