from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('books', '0017_stockadjustment'),
    ]

    operations = [
        migrations.AddField(
            model_name='sale',
            name='currency',
            field=models.CharField(
                choices=[
                    ('USD', 'USD'), ('EUR', 'EUR'), ('GBP', 'GBP'),
                    ('SAR', 'SAR'), ('AED', 'AED'), ('MAD', 'MAD'),
                    ('DZD', 'DZD'), ('TND', 'TND'), ('EGP', 'EGP'), ('TRY', 'TRY'),
                ],
                default='USD',
                max_length=3,
                verbose_name='Currency',
            ),
        ),
        migrations.AddField(
            model_name='sale',
            name='tax_rate',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=5, verbose_name='Tax rate (%)'),
        ),
    ]
