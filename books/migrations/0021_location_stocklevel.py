import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('books', '0020_printrun_royaltyrate'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Location',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200, verbose_name='Name')),
                ('address', models.TextField(blank=True, verbose_name='Address')),
                ('is_default', models.BooleanField(default=False, verbose_name='Default location')),
                ('owner', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='owned_locations',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'ordering': ['name'], 'unique_together': {('owner', 'name')}},
        ),
        migrations.CreateModel(
            name='StockLevel',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity', models.PositiveIntegerField(default=0, verbose_name='Quantity')),
                ('book', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='stock_levels',
                    to='books.book',
                    verbose_name='Book',
                )),
                ('location', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='stock_levels',
                    to='books.location',
                    verbose_name='Location',
                )),
                ('owner', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='owned_stock_levels',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'unique_together': {('book', 'location')}},
        ),
    ]
