"""Food cost calculations with cascading sub-recipes."""
from __future__ import annotations

from typing import Any


def product_unit_cost(product_row) -> float | None:
    if product_row is None:
        return None
    if product_row["price_override"] is not None:
        return float(product_row["price_override"])
    if product_row["last_price"] is not None:
        return float(product_row["last_price"])
    return None


PIECE_UNITS = frozenset({"pz", "pezzo", "pezzi", "pc", "pcs"})


def normalize_recipe_unit(unit: str | None) -> str:
    """Canonical unit stored on a recipe ingredient line."""
    u = (unit or "kg").strip().lower()
    if u in ("kg", "kgs"):
        return "kg"
    if u in ("g", "gr", "grammi"):
        return "g"
    if u in ("l", "lt"):
        return u
    if u == "ml":
        return "ml"
    if u in PIECE_UNITS:
        return "pz"
    return u or "kg"


def is_piece_unit(unit: str | None) -> bool:
    return normalize_recipe_unit(unit) == "pz"


def yield_to_kg(qty: float, unit: str | None) -> float:
    u = (unit or "kg").strip().lower()
    if u in ("g", "gr", "ml"):
        return float(qty) / 1000.0
    return float(qty)


def parse_yield_pieces(value) -> tuple[float | None, str | None]:
    """Empty means 'no pieces'. Anything else must be a number greater than zero."""
    if value is None:
        return None, None
    if isinstance(value, str):
        value = value.strip().replace(",", ".")
        if value == "":
            return None, None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None, "Numero di pezzi non valido"
    if n != n or n == float("inf") or n == float("-inf"):
        return None, "Numero di pezzi non valido"
    if n <= 0:
        return None, "Il numero di pezzi deve essere maggiore di zero"
    return n, None


def pieces_from_row(recipe) -> float | None:
    """Read optional yield_pieces. Missing column or NULL → recipe stays weight-only."""
    if recipe is None:
        return None
    try:
        keys = recipe.keys()
    except AttributeError:
        return None
    if "yield_pieces" not in keys:
        return None
    raw = recipe["yield_pieces"]
    if raw is None or raw == "":
        return None
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return n


def piece_metrics(yield_kg: float, pieces: float | None, cost_per_kg: float | None) -> dict[str, Any]:
    """Weight and cost of one piece. Empty when the recipe has no piece count."""
    out: dict[str, Any] = {
        "yield_pieces": None,
        "piece_weight_kg": None,
        "piece_weight_g": None,
        "cost_per_piece": None,
    }
    if pieces is None or pieces <= 0:
        return out
    out["yield_pieces"] = float(pieces)
    if not yield_kg or yield_kg <= 0:
        return out
    piece_kg = float(yield_kg) / float(pieces)
    out["piece_weight_kg"] = round(piece_kg, 6)
    out["piece_weight_g"] = round(piece_kg * 1000.0, 4)
    if cost_per_kg is not None:
        out["cost_per_piece"] = round(float(cost_per_kg) * piece_kg, 4)
    return out


def convert_qty_to_kg(qty: float, unit: str) -> float:
    u = (unit or "kg").strip().lower()
    if u in ("kg", "l", "lt"):
        return qty
    if u in ("g", "gr", "ml"):
        return qty / 1000.0
    # pezzi: treat as units, cost is per piece already — return qty as-is for "pz"
    return qty


def ingredient_cost(conn, ing: dict, visiting: set[int] | None = None) -> dict[str, Any]:
    """Return cost info for one ingredient line."""
    visiting = visiting or set()
    qty = float(ing["quantity"] or 0)
    unit = ing["unit"] or "kg"
    override = ing["unit_cost_override"]
    result = {
        "id": ing["id"],
        "kind": ing["kind"],
        "name": ing["name"],
        "quantity": qty,
        "unit": unit,
        "unit_cost": None,
        "line_cost": None,
        "note": None,
    }
    if override is not None:
        uc = float(override)
        # override is always cost per the ingredient's unit as entered (per kg/g/l/pz as stored)
        # Convention: unit_cost_override and product prices are "per catalog unit" which is typically per kg or per pz.
        # For g/ml we convert qty to kg-equivalent of the price base.
        if unit.lower() in ("g", "gr", "ml"):
            line = uc * (qty / 1000.0)
        else:
            line = uc * qty
        result["unit_cost"] = uc
        result["line_cost"] = round(line, 4)
        return result

    if ing["kind"] == "product":
        prod = conn.execute("SELECT * FROM products WHERE id=?", (ing["product_id"],)).fetchone()
        uc = product_unit_cost(prod)
        if uc is None:
            result["note"] = "prezzo mancante"
            return result
        if unit.lower() in ("g", "gr", "ml"):
            line = uc * (qty / 1000.0)
        else:
            line = uc * qty
        result["unit_cost"] = uc
        result["line_cost"] = round(line, 4)
        return result

    # sub-recipe
    sub_id = ing["sub_recipe_id"]
    if sub_id in visiting:
        result["note"] = "ciclo ricette"
        return result
    sub = compute_recipe_cost(conn, sub_id, visiting | {sub_id})
    if sub.get("error"):
        result["note"] = "costo sottoricetta non calcolabile"
        return result
    # Pieces: cost follows the single piece (its weight and its cost), not the whole batch.
    if is_piece_unit(unit):
        pieces = sub.get("yield_pieces")
        cost_per_piece = sub.get("cost_per_piece")
        if not pieces:
            result["note"] = "la sottoricetta non indica i pezzi"
            return result
        if cost_per_piece is None:
            result["note"] = "costo sottoricetta non calcolabile"
            return result
        result["unit_cost"] = cost_per_piece
        result["line_cost"] = round(float(cost_per_piece) * qty, 4)
        result["piece_weight_g"] = sub.get("piece_weight_g")
        if sub.get("piece_weight_g") is not None:
            result["weight_g"] = round(float(sub["piece_weight_g"]) * qty, 4)
        return result
    cost_per_kg = sub.get("cost_per_kg")
    if cost_per_kg is None:
        result["note"] = "costo sottoricetta non calcolabile"
        return result
    # sub-recipe cost is always per kg of yield
    if unit.lower() in ("g", "gr"):
        line = cost_per_kg * (qty / 1000.0)
    elif unit.lower() in ("kg", "l", "lt"):
        line = cost_per_kg * qty
    else:
        result["note"] = "unità non riconosciuta"
        return result
    result["unit_cost"] = cost_per_kg
    result["line_cost"] = round(line, 4)
    return result


def compute_recipe_cost(conn, recipe_id: int, visiting: set[int] | None = None) -> dict[str, Any]:
    visiting = visiting or set()
    recipe = conn.execute("SELECT * FROM recipes WHERE id=?", (recipe_id,)).fetchone()
    if not recipe:
        return {"error": "ricetta non trovata"}
    ings = conn.execute(
        "SELECT * FROM recipe_ingredients WHERE recipe_id=? ORDER BY sort_order, id",
        (recipe_id,),
    ).fetchall()
    lines = []
    ingredients_cost = 0.0
    missing = False
    for ing in ings:
        info = ingredient_cost(conn, dict(ing), visiting)
        lines.append(info)
        if info["line_cost"] is None:
            missing = True
        else:
            ingredients_cost += info["line_cost"]

    extras = (
        float(recipe["extra_condiments"] or 0)
        + float(recipe["extra_packaging"] or 0)
        + float(recipe["extra_energy_labor"] or 0)
    )
    total_cost = ingredients_cost + extras
    yield_qty = float(recipe["yield_qty"] or 0) or 0
    yield_unit = recipe["yield_unit"] or "kg"
    # normalize yield to kg for cost_per_kg
    if yield_unit.lower() in ("g", "gr", "ml"):
        yield_kg = yield_qty / 1000.0
    else:
        yield_kg = yield_qty
    cost_per_kg = (total_cost / yield_kg) if yield_kg > 0 and not missing else None
    if missing and ingredients_cost == 0 and not any(l["line_cost"] for l in lines):
        cost_per_kg = None
    elif missing:
        # partial: still compute with known costs
        cost_per_kg = (total_cost / yield_kg) if yield_kg > 0 else None

    pieces = pieces_from_row(recipe)
    metrics = piece_metrics(yield_kg, pieces, cost_per_kg)

    sell = recipe["sell_price"]
    margin = None
    food_cost_pct = None
    traffic = None
    if sell is not None and float(sell) > 0 and cost_per_kg is not None:
        # sell_price is per yield unit sold (typically per kg or per portion of yield)
        # Convention: sell_price is for the whole yield batch? Spec says prezzo di vendita inserted manually.
        # We'll treat sell_price as price per kg (same basis as cost_per_kg) for food cost %.
        sell_f = float(sell)
        food_cost_pct = (cost_per_kg / sell_f) * 100.0
        margin = sell_f - cost_per_kg
        if food_cost_pct < 30:
            traffic = "verde"
        elif food_cost_pct <= 35:
            traffic = "giallo"
        else:
            traffic = "rosso"

    return {
        "recipe_id": recipe_id,
        "name": recipe["name"],
        "category": recipe["category"],
        "yield_qty": yield_qty,
        "yield_unit": yield_unit,
        "yield_kg": yield_kg,
        "yield_pieces": metrics["yield_pieces"],
        "piece_weight_kg": metrics["piece_weight_kg"],
        "piece_weight_g": metrics["piece_weight_g"],
        "cost_per_piece": metrics["cost_per_piece"],
        "ingredients": lines,
        "ingredients_cost": round(ingredients_cost, 4),
        "extras": round(extras, 4),
        "extra_condiments": float(recipe["extra_condiments"] or 0),
        "extra_packaging": float(recipe["extra_packaging"] or 0),
        "extra_energy_labor": float(recipe["extra_energy_labor"] or 0),
        "total_cost": round(total_cost, 4),
        "cost_per_kg": round(cost_per_kg, 4) if cost_per_kg is not None else None,
        "sell_price": float(sell) if sell is not None else None,
        "margin": round(margin, 4) if margin is not None else None,
        "food_cost_pct": round(food_cost_pct, 2) if food_cost_pct is not None else None,
        "traffic_light": traffic,
        "notes": recipe["notes"],
    }


def scale_ingredients(ings: list[dict], from_yield_kg: float, to_kg: float) -> list[dict]:
    if not from_yield_kg:
        factor = 1.0
    else:
        factor = to_kg / from_yield_kg
    out = []
    for ing in ings:
        o = dict(ing)
        o["quantity"] = round(float(ing["quantity"]) * factor, 4)
        if o.get("line_cost") is not None:
            o["line_cost"] = round(float(ing["line_cost"]) * factor, 4)
        if o.get("weight_g") is not None:
            o["weight_g"] = round(float(ing["weight_g"]) * factor, 4)
        out.append(o)
    return out
