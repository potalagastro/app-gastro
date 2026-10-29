"""Order units of measure for POTA LA GASTRO.

Canonical values (stored in DB / WhatsApp):
  Kg, Ct, Cassa, Pezzo

Ct is shown in the UI as "Ct (cartone)".
Invoice/catalog `products.unit` is kept as-is; `default_order_unit` is the
order UoM used when adding a product to a draft.
"""
from __future__ import annotations

ORDER_UNIT_VALUES = ("Kg", "Ct", "Cassa", "Pezzo")

ORDER_UNITS = [
    {"value": "Kg", "label": "Kg"},
    {"value": "Ct", "label": "Ct (cartone)"},
    {"value": "Cassa", "label": "Cassa"},
    {"value": "Pezzo", "label": "Pezzo"},
]

# invoice / catalog tokens → canonical
_INVOICE_MAP = {
    "kg": "Kg",
    "kgs": "Kg",
    "kilo": "Kg",
    "kilogrammi": "Kg",
    "lt": "Kg",  # liquids ordered by volume ≈ kg basis in kitchen
    "l": "Kg",
    "litro": "Kg",
    "litri": "Kg",
    "ct": "Ct",
    "crt": "Ct",
    "cartone": "Ct",
    "cartoni": "Ct",
    "cassa": "Cassa",
    "casse": "Cassa",
    "cs": "Cassa",
    "pz": "Pezzo",
    "pezzo": "Pezzo",
    "pezzi": "Pezzo",
    "pc": "Pezzo",
    "ea": "Pezzo",
    "n": "Pezzo",
    "n.": "Pezzo",
    "nr": "Pezzo",
    "numero": "Pezzo",
    "cf": "Pezzo",
    "st": "Pezzo",
}


def normalize_order_unit(value: str | None, fallback: str = "Kg") -> str:
    if value is None:
        return fallback if fallback in ORDER_UNIT_VALUES else "Kg"
    raw = str(value).strip()
    if raw in ORDER_UNIT_VALUES:
        return raw
    mapped = _INVOICE_MAP.get(raw.lower())
    if mapped:
        return mapped
    # case-insensitive match on canonical
    for v in ORDER_UNIT_VALUES:
        if raw.lower() == v.lower():
            return v
    return fallback if fallback in ORDER_UNIT_VALUES else "Kg"


def infer_order_unit(invoice_unit: str | None = "", description: str | None = "") -> str:
    """Pick a sensible default from invoice unit text, else description hints."""
    u = (invoice_unit or "").strip()
    if u:
        mapped = _INVOICE_MAP.get(u.lower())
        if mapped:
            return mapped
        # already canonical?
        n = normalize_order_unit(u, fallback="")
        if n in ORDER_UNIT_VALUES:
            return n

    d = (description or "").lower()
    # packaging sold as carton / cassa (word boundaries-ish)
    if any(x in d for x in (" cartone", "cartone ", " crt.", " crt ", " ct ", "/ct", "ct.")):
        # many lines say "CT 50 PZ" meaning pack size — still a countable pack → Ct
        if " cassa" not in d:
            return "Ct"
    if " cassa" in d or d.startswith("cassa ") or "casse " in d:
        return "Cassa"
    # weight-like
    if any(x in d for x in (" kg", "kg.", "/kg", " al kg", "grammi", " gr ")):
        return "Kg"
    # default: piece (most non-weight grocery / pack items)
    return "Pezzo"


def unit_label(value: str) -> str:
    for u in ORDER_UNITS:
        if u["value"] == value:
            return u["label"]
    return value or "Kg"
