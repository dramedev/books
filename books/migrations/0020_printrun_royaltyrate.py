import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('books', '0019_invoice_invoiceitem'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PrintRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity', models.PositiveIntegerField(verbose_name='Quantity')),
                ('cost_per_unit', models.DecimalField(decimal_places=2, max_digits=8, verbose_name='Cost per unit')),
                ('run_date', models.DateField(verbose_name='Run date')),
                ('note', models.CharField(blank=True, max_length=200, verbose_name='Note')),
                ('book', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='print_runs',
                    to='books.book',
                    verbose_name='Book',
                )),
                ('owner', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='owned_print_runs',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'ordering': ['-run_date']},
        ),
        migrations.CreateModel(
            name='RoyaltyRate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('rate', models.DecimalField(decimal_places=2, max_digits=5, verbose_name='Rate (%)')),
                ('effective_from', models.DateField(verbose_name='Effective from')),
                ('note', models.CharField(blank=True, max_length=200, verbose_name='Note')),
                ('author', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='royalty_rates',
                    to='books.author',
                    verbose_name='Author',
                )),
                ('book', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='royalty_rates',
                    to='books.book',
                    verbose_name='Book',
                )),
                ('owner', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='owned_royalty_rates',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'ordering': ['-effective_from']},
        ),
    ]
