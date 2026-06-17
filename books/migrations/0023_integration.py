import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('books', '0022_default_locations_data'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Integration',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('platform', models.CharField(
                    choices=[('shopify', 'Shopify'), ('amazon', 'Amazon')],
                    max_length=20,
                    verbose_name='Platform',
                )),
                ('name', models.CharField(max_length=100, verbose_name='Name')),
                ('store_url', models.CharField(blank=True, max_length=200, verbose_name='Store URL')),
                ('api_key', models.CharField(blank=True, max_length=200, verbose_name='API key')),
                ('api_secret', models.CharField(blank=True, max_length=200, verbose_name='API secret')),
                ('webhook_secret', models.CharField(blank=True, max_length=200, verbose_name='Webhook secret')),
                ('is_active', models.BooleanField(default=True, verbose_name='Active')),
                ('orders_synced', models.PositiveIntegerField(default=0)),
                ('last_synced_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('owner', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='owned_integrations',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
