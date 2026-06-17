import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('books', '0018_sale_currency_tax'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Invoice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('invoice_number', models.CharField(blank=True, max_length=20, verbose_name='Invoice number')),
                ('customer_name', models.CharField(max_length=200, verbose_name='Customer name')),
                ('customer_email', models.EmailField(blank=True, verbose_name='Customer email')),
                ('customer_address', models.TextField(blank=True, verbose_name='Customer address')),
                ('invoice_date', models.DateField(verbose_name='Invoice date')),
                ('due_date', models.DateField(blank=True, null=True, verbose_name='Due date')),
                ('currency', models.CharField(
                    choices=[
                        ('USD', 'USD'), ('EUR', 'EUR'), ('GBP', 'GBP'),
                        ('SAR', 'SAR'), ('AED', 'AED'), ('MAD', 'MAD'),
                        ('DZD', 'DZD'), ('TND', 'TND'), ('EGP', 'EGP'), ('TRY', 'TRY'),
                    ],
                    default='USD',
                    max_length=3,
                    verbose_name='Currency',
                )),
                ('status', models.CharField(
                    choices=[('draft', 'Draft'), ('sent', 'Sent'), ('paid', 'Paid')],
                    default='draft',
                    max_length=10,
                    verbose_name='Status',
                )),
                ('note', models.TextField(blank=True, verbose_name='Note')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('owner', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='owned_invoices',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='InvoiceItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('description', models.CharField(max_length=200, verbose_name='Description')),
                ('quantity', models.PositiveIntegerField(default=1, verbose_name='Quantity')),
                ('unit_price', models.DecimalField(decimal_places=2, max_digits=8, verbose_name='Unit price')),
                ('tax_rate', models.DecimalField(decimal_places=2, default=0, max_digits=5, verbose_name='Tax rate (%)')),
                ('book', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='invoice_items',
                    to='books.book',
                    verbose_name='Book',
                )),
                ('invoice', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='items',
                    to='books.invoice',
                    verbose_name='Invoice',
                )),
            ],
        ),
    ]
