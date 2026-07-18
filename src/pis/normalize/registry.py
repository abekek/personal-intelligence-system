from collections.abc import Callable

from sqlalchemy.orm import Session

from pis.schemas.events import CanonicalEvent

Normalizer = Callable[[Session, CanonicalEvent], None]

_NORMALIZERS: dict[str, Normalizer] = {}


def register(event_type: str, fn: Normalizer) -> None:
    _NORMALIZERS[event_type] = fn


def get_normalizer(event_type: str) -> Normalizer | None:
    return _NORMALIZERS.get(event_type)
