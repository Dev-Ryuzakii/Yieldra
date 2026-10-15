"""What one plot of each crop costs a sponsor, in US dollars.

A sponsor chooses how many plots of a farm to back; the total is plots times
this price and is then split into the crop's milestone tranches. Prices cover
one season of inputs and labour for a plot and are set here, not by the client.
"""

from __future__ import annotations

PLOT_PRICE_USD: dict[str, int] = {
    "cassava": 40,
    "maize": 35,
    "rice": 45,
    "groundnuts": 35,
    "peppers": 50,
    "tomatoes": 60,
}
DEFAULT_PLOT_PRICE_USD = 40


def plot_price_usd(crop: str) -> int:
    return PLOT_PRICE_USD.get(crop.strip().lower(), DEFAULT_PLOT_PRICE_USD)


def plot_price_minor(crop: str) -> int:
    return plot_price_usd(crop) * 100
