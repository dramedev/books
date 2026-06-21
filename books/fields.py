from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models


def _fernet():
    return Fernet(settings.FIELD_ENCRYPTION_KEY)


def encrypt_value(value):
    if not value:
        return value
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_value(value):
    if not value:
        return value
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken:
        # Not actually ciphertext (e.g. a pre-migration plaintext row that
        # hasn't been through the backfill yet) - return as-is rather than
        # crash, so a half-migrated dataset still degrades gracefully.
        return value


class EncryptedCharField(models.CharField):
    """Transparently encrypts at rest with Fernet (settings.FIELD_ENCRYPTION_KEY).

    Reads/writes look like a plain CharField to the rest of the app - only
    the raw DB column holds ciphertext. Blank values stay blank rather than
    becoming a confusing non-empty ciphertext token for "no secret set".
    """

    def from_db_value(self, value, expression, connection):
        return decrypt_value(value)

    def to_python(self, value):
        if isinstance(value, str) and value:
            return value
        return super().to_python(value)

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        return encrypt_value(value)
