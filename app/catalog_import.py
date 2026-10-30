"""Import catalog from CSV (exported from Google Sheet)."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path

import os

from .units import infer_order_unit
from .db import db

_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = Path(os.environ.get("DATA_DIR") or (_ROOT / "data"))
CSV_PATH = Path(os.environ["CATALOG_CSV"]) if os.environ.get("CATALOG_CSV") else (_DATA_DIR / "catalogo_import.csv")

MERGE_MAP = {
    "PORTO S.R.L.": "PORTO SRL",
    "PORTO SRL": "PORTO SRL",
    "Prevalcart S.n.c. di Baldi & Mazzacani": "PREVALCART S.R.L.",
    "PREVALCART S.R.L.": "PREVALCART S.R.L.",
}

# Substrings matched case-insensitively against supplier name (like SANTOMARCO).
# Kept out of Ordini forever, including future catalog imports.
EXCLUDE_SUBSTR = (
    "SANTOMARCO",
    "AUSONIA",
    "NANUTTI",
    "CLPA",
    "DALLA BONA",
    "FALENA",
    "EPICASA",
    "METRO",
    "RISTODOM",
    "SAN CARLO",
    "SMERALDO",
    "SPESA INTELLIGENTE",
    "ZA.BE",
    "ZABE",
    "ZEELANDIA",
    "ZELANDIA",
    "ZELANDA",
)


def is_excluded_supplier(name: str) -> bool:
    """True if supplier name matches the permanent Ordini exclusion list."""
    u = (name or "").upper()
    if not u:
        return False
    # Normalize common punctuation so "ZA.BE" / "ZA BE" / "CLPA" still match
    compact = u.replace(".", "").replace(" ", "")
    for x in EXCLUDE_SUBSTR:
        xu = x.upper()
        if xu in u or xu.replace(".", "").replace(" ", "") in compact:
            return True
    return False


def apply_supplier_exclusions() -> dict:
    """Remove or hide excluded suppliers already in the DB.

    - If a supplier has sent/draft orders or is referenced by recipes/order_lines:
      keep rows, hide products from catalog (visible=0). API still filters them out of Ordini.
    - Otherwise delete products then the supplier.
    Does not modify recipes or historical order rows.
    """
    with db() as conn:
        rows = conn.execute("SELECT id, name FROM suppliers").fetchall()
        hidden, deleted_suppliers, deleted_products = [], [], 0
        for row in rows:
            if not is_excluded_supplier(row["name"]):
                continue
            sid = row["id"]
            n_orders = conn.execute(
                "SELECT COUNT(*) c FROM orders WHERE supplier_id=?", (sid,)
            ).fetchone()["c"]
            n_lines = conn.execute(
                """SELECT COUNT(*) c FROM order_lines ol
                   JOIN products p ON p.id=ol.product_id
                   WHERE p.supplier_id=?""",
                (sid,),
            ).fetchone()["c"]
            n_recipes = conn.execute(
                """SELECT COUNT(*) c FROM recipe_ingredients ri
                   JOIN products p ON p.id=ri.product_id
                   WHERE p.supplier_id=?""",
                (sid,),
            ).fetchone()["c"]
            if n_orders or n_lines or n_recipes:
                conn.execute(
                    "UPDATE products SET visible=0 WHERE supplier_id=?", (sid,)
                )
                hidden.append(row["name"])
            else:
                cur = conn.execute("DELETE FROM products WHERE supplier_id=?", (sid,))
                deleted_products += cur.rowcount
                conn.execute("DELETE FROM suppliers WHERE id=?", (sid,))
                deleted_suppliers.append(row["name"])
        return {
            "hidden": hidden,
            "deleted_suppliers": deleted_suppliers,
            "deleted_products": deleted_products,
        }


def canon_supplier(name: str) -> str:
    n = (name or "").strip()
    if n in MERGE_MAP:
        return MERGE_MAP[n]
    nu = n.upper().replace(".", "").replace(" ", "")
    if nu.startswith("PORTO"):
        return "PORTO SRL"
    if "PREVALCART" in nu:
        return "PREVALCART S.R.L."
    return n


def parse_price(v):
    if v is None or str(v).strip() == "":
        return None
    s = str(v).strip().replace("€", "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_date(v):
    if not v:
        return None
    s = str(v).strip()
    if len(s) >= 10 and s[4] == "-":
        return s[:10]
    return s[:10] if s else None


def within_12_months(date_str: str | None, today: datetime | None = None) -> bool:
    if not date_str:
        return False
    today = today or datetime.now()
    try:
        d = datetime.strptime(date_str[:10], "%Y-%m-%d")
    except ValueError:
        return False
    return d >= today - timedelta(days=365)


def import_catalog(csv_path: Path | None = None, user_id: int | None = None, username: str = "system") -> dict:
    path = csv_path or CSV_PATH
    if not path.exists():
        raise FileNotFoundError(f"CSV non trovato: {path}")

    today = datetime.now()
    created_suppliers = 0
    upserted = 0
    skipped = 0
    visible_count = 0

    with db() as conn, path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        supplier_cache: dict[str, int] = {}
        for row in conn.execute("SELECT id, name FROM suppliers"):
            supplier_cache[row["name"]] = row["id"]

        for row in reader:
            cat = (row.get("Categoria") or "").strip().lower()
            if cat != "merce":
                skipped += 1
                continue
            raw_name = (row.get("Fornitore") or "").strip()
            if is_excluded_supplier(raw_name):
                skipped += 1
                continue
            name = canon_supplier(raw_name)
            if name not in supplier_cache:
                cur = conn.execute(
                    "INSERT INTO suppliers (name) VALUES (?)",
                    (name,),
                )
                supplier_cache[name] = cur.lastrowid
                created_suppliers += 1
            sid = supplier_cache[name]
            desc = (row.get("Descrizione") or "").strip()
            if not desc:
                skipped += 1
                continue
            code = (row.get("Codice") or "").strip()
            unit = (row.get("Unità") or "").strip()
            last_price = parse_price(row.get("Ultimo prezzo netto"))
            last_date = parse_date(row.get("Data ultimo"))
            last_inv = (row.get("N. fattura ultimo") or "").strip()
            pmin = parse_price(row.get("Prezzo min"))
            pmax = parse_price(row.get("Prezzo max"))
            try:
                n_acq = int(float(str(row.get("N. acquisti") or "0").replace(",", ".")))
            except ValueError:
                n_acq = 0

            # Preserve admin visibility/favorite/override if product exists
            existing = conn.execute(
                """SELECT id, visible, favorited, price_override FROM products
                   WHERE supplier_id=? AND code=? AND description=?""",
                (sid, code, desc),
            ).fetchone()
            default_visible = 1 if within_12_months(last_date, today) else 0
            order_unit = infer_order_unit(unit, desc)
            if existing:
                conn.execute(
                    """UPDATE products SET unit=?, last_price=?, last_purchase_date=?,
                       last_invoice=?, price_min=?, price_max=?, purchase_count=?
                       WHERE id=?""",
                    (unit, last_price, last_date, last_inv, pmin, pmax, n_acq, existing["id"]),
                )
                # Do not overwrite admin-chosen default_order_unit on reimport
            else:
                conn.execute(
                    """INSERT INTO products
                       (supplier_id, code, description, unit, last_price, last_purchase_date,
                        last_invoice, price_min, price_max, purchase_count, visible, default_order_unit)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (sid, code, desc, unit, last_price, last_date, last_inv, pmin, pmax, n_acq, default_visible, order_unit),
                )
                if default_visible:
                    visible_count += 1
            upserted += 1

        conn.execute(
            "INSERT OR REPLACE INTO app_meta (key, value) VALUES ('catalog_imported_at', datetime('now','localtime'))"
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_meta (key, value) VALUES ('catalog_rows', ?)",
            (str(upserted),),
        )
        conn.execute(
            """INSERT INTO audit_log (user_id, username, action, entity, detail)
               VALUES (?,?,?,?,?)""",
            (user_id, username, "import_catalog", "products", f"upserted={upserted} new_suppliers={created_suppliers}"),
        )

    exclusion = apply_supplier_exclusions()

    with db() as conn:
        tot = conn.execute("SELECT COUNT(*) c FROM products").fetchone()["c"]
        vis = conn.execute("SELECT COUNT(*) c FROM products WHERE visible=1").fetchone()["c"]
        nsup = conn.execute("SELECT COUNT(*) c FROM suppliers").fetchone()["c"]

    return {
        "upserted": upserted,
        "skipped": skipped,
        "suppliers": nsup,
        "products_total": tot,
        "products_visible": vis,
        "created_suppliers": created_suppliers,
        "exclusion": exclusion,
    }
