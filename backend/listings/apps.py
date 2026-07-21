from django.apps import AppConfig
from django.db.models import CharField, TextField
from django.db.models.functions import Length


class ListingsConfig(AppConfig):
    name = "listings"

    def ready(self) -> None:
        from dormcycle import checks  # noqa: F401
        from . import signals  # noqa: F401  — listing thumbnail generation

        # Enable `field__length` lookups used by the completeness-score
        # annotation in views/helpers.py.
        CharField.register_lookup(Length)
        TextField.register_lookup(Length)
