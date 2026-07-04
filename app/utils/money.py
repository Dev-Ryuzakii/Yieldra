"""Naira <-> kobo helpers. DB stores kobo; responses show Naira."""


def naira_to_kobo(naira: float | int) -> int:
    return int(round(float(naira) * 100))


def kobo_to_naira(kobo: int) -> float:
    return round(kobo / 100, 2)


def format_naira(kobo: int) -> str:
    """Human string, e.g. 1500000 kobo -> '₦15,000.00'."""
    return f"₦{kobo_to_naira(kobo):,.2f}"
