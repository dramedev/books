from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand, CommandError

from books.permissions import ROLE_PERMISSIONS, ensure_roles


class Command(BaseCommand):
    help = "Create Rumi Press role groups and optionally assign a user."

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            help="Username to assign to a role."
        )
        parser.add_argument(
            "--role",
            choices=sorted(
                ROLE_PERMISSIONS
            ),
            help="Role to assign to the selected user."
        )

    def handle(self, *args, **options):
        for role, codenames in ROLE_PERMISSIONS.items():
            permissions = Permission.objects.filter(
                content_type__app_label="books",
                codename__in=codenames,
            )

            missing = sorted(
                set(codenames)
                - set(permissions.values_list("codename", flat=True))
            )

            if missing:
                raise CommandError(
                    f"Missing permissions for {role}: {', '.join(missing)}"
                )

        groups = ensure_roles()

        for role in groups:
            self.stdout.write(
                self.style.SUCCESS(
                    f"{role} role ready."
                )
            )

        username = options.get(
            "username"
        )
        role = options.get(
            "role"
        )

        if username or role:
            if not username or not role:
                raise CommandError(
                    "Use --username and --role together."
                )

            User = get_user_model()

            try:
                user = User.objects.get(
                    username=username
                )
            except User.DoesNotExist as exc:
                raise CommandError(
                    f"User not found: {username}"
                ) from exc

            user.groups.add(
                groups[role]
            )

            self.stdout.write(
                self.style.SUCCESS(
                    f"Assigned {username} to {role}."
                )
            )
