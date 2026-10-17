"""Money formatting shared by every surface. Money is always signed integer paise."""

from __future__ import annotations


def format_paise(paise: int, minus: str = "-") -> str:
    """Format signed integer paise without ever going through a float.

    ``paise / 100`` is exact for every amount this system will see, but money
    formatting is not the place to rely on that. Integer division has no
    magnitude at which it starts rounding.
    """
    sign = minus if paise < 0 else ""
    whole, fraction = divmod(abs(int(paise)), 100)
    return f"{sign}₹{whole:,}.{fraction:02d}"
