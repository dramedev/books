import logging

from django.apps import AppConfig


logger = logging.getLogger(__name__)


class BooksConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'books'

    def ready(self):
        from . import signals  # noqa: F401
        from django.db.models.signals import post_migrate
        from .permissions import ensure_roles

        def sync_roles(sender, **kwargs):
            if sender.name == "books":
                try:
                    ensure_roles()
                except Exception:
                    logger.exception("Failed to sync role permissions after migrate")

        post_migrate.connect(sync_roles, sender=self)
