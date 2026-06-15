import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def assign_existing_data_to_drame(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Category = apps.get_model("books", "Category")
    Author = apps.get_model("books", "Author")
    Book = apps.get_model("books", "Book")
    Sale = apps.get_model("books", "Sale")
    Profile = apps.get_model("books", "Profile")

    try:
        owner = User.objects.get(username="Drame")
    except User.DoesNotExist:
        owner = User.objects.order_by("id").first()

    if owner is None:
        return

    Category.objects.filter(owner__isnull=True).update(owner=owner)
    Author.objects.filter(owner__isnull=True).update(owner=owner)
    Book.objects.filter(owner__isnull=True).update(owner=owner)
    Sale.objects.filter(owner__isnull=True).update(owner=owner)

    Profile.objects.update_or_create(
        user=owner,
        defaults={"email_verified": True, "access_code_redeemed": True},
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('books', '0008_profile'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AccessCode',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=12, unique=True)),
                ('label', models.CharField(blank=True, max_length=100)),
                ('is_used', models.BooleanField(default=False)),
                ('used_at', models.DateTimeField(blank=True, null=True)),
                ('expires_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('used_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='redeemed_access_code', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddField(
            model_name='profile',
            name='email_verified',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='profile',
            name='verification_code',
            field=models.CharField(blank=True, max_length=6),
        ),
        migrations.AddField(
            model_name='profile',
            name='verification_code_expires_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='profile',
            name='access_code_redeemed',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='category',
            name='owner',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='owned_categories', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='author',
            name='owner',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='owned_authors', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='book',
            name='owner',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='owned_books', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='sale',
            name='owner',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='owned_sales', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='author',
            name='name',
            field=models.CharField(max_length=200),
        ),
        migrations.RunPython(assign_existing_data_to_drame, noop),
        migrations.AlterField(
            model_name='category',
            name='owner',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='owned_categories', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='author',
            name='owner',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='owned_authors', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='book',
            name='owner',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='owned_books', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='sale',
            name='owner',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='owned_sales', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterUniqueTogether(
            name='author',
            unique_together={('owner', 'name')},
        ),
    ]
