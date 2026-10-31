"""POTA LA GASTRO — MVP web app (ordini + food cost)."""
from __future__ import annotations

import os
import secrets
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .auth import (
    audit,
    current_user,
    hash_password,
    require_admin,
    require_user,
    verify_password,
)
from .catalog_import import CSV_PATH, apply_supplier_exclusions, import_catalog, is_excluded_supplier
from .db import DB_PATH, db, init_db
from .units import ORDER_UNITS, ORDER_UNIT_VALUES, infer_order_unit, normalize_order_unit, unit_label
from .foodcost import compute_recipe_cost, scale_ingredients, ingredient_cost

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR") or (BASE / "data"))
TEMPLATES = Jinja2Templates(directory=str(BASE / "templates"))

def _session_secret() -> str:
    env = (os.environ.get("SESSION_SECRET") or "").strip()
    if env:
        return env
    secret_file = DATA_DIR / "session_secret.txt"
    if secret_file.exists():
        return secret_file.read_text().strip()
    val = secrets.token_hex(32)
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    secret_file.write_text(val)
    return val


_https_only = os.environ.get("HTTPS_ONLY", "1").lower() not in ("0", "false", "no")

app = FastAPI(title="POTA LA GASTRO")
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret(),
    session_cookie="pota_session",
    same_site="lax",
    https_only=_https_only,
    max_age=60 * 60 * 24 * 14,
)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


@app.on_event("startup")
def startup():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    # Also bootstrap here so a plain `uvicorn` start (without start.sh) still seeds admin/catalog.
    if str(BASE) not in sys.path:
        sys.path.insert(0, str(BASE))
    try:
        from scripts.bootstrap import main as bootstrap_main
        bootstrap_main()
    except Exception as e:
        print(f"bootstrap warning: {e}", flush=True)
    try:
        excl = apply_supplier_exclusions()
        print(f"supplier exclusions: {excl}", flush=True)
    except Exception as e:
        print(f"supplier exclusion warning: {e}", flush=True)


def ok(data=None, **kw):
    payload = {"ok": True}
    if data is not None:
        payload["data"] = data
    payload.update(kw)
    return JSONResponse(payload)


def err(msg: str, status: int = 400):
    return JSONResponse({"ok": False, "error": msg}, status_code=status)


# ─── Pages ───────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return TEMPLATES.TemplateResponse(
        request,
        "index.html",
        {"user": user, "is_admin": user["role"] == "admin"},
    )


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if current_user(request):
        return RedirectResponse("/", status_code=302)
    return TEMPLATES.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    from .auth import get_user_by_username

    u = get_user_by_username(username.strip())
    if not u or not verify_password(password, u["password_hash"]):
        return TEMPLATES.TemplateResponse(
            request,
            "login.html",
            {"error": "Utente o password non validi"},
            status_code=401,
        )
    request.session["user_id"] = u["id"]
    audit(dict(u), "login", "user", u["id"])
    return RedirectResponse("/", status_code=302)


@app.post("/logout")
@app.get("/logout")
def logout(request: Request):
    user = current_user(request)
    if user:
        audit(user, "logout", "user", user["id"])
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


# ─── API: me / users ─────────────────────────────────────

@app.get("/api/me")
def api_me(request: Request):
    user = require_user(request)
    return ok(
        {
            "id": user["id"],
            "username": user["username"],
            "full_name": user["full_name"],
            "role": user["role"],
        }
    )


@app.get("/api/users")
def api_users(request: Request):
    require_admin(request)
    with db() as conn:
        rows = conn.execute(
            "SELECT id, username, full_name, role, active, created_at FROM users ORDER BY id"
        ).fetchall()
    return ok([dict(r) for r in rows])


@app.post("/api/users")
async def api_create_user(request: Request):
    admin = require_admin(request)
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    full_name = (body.get("full_name") or "").strip()
    role = body.get("role") or "dipendente"
    if role not in ("admin", "dipendente"):
        return err("Ruolo non valido")
    if not username or not password or not full_name:
        return err("Campi obbligatori mancanti")
    if len(password) < 6:
        return err("Password minimo 6 caratteri")
    try:
        with db() as conn:
            cur = conn.execute(
                """INSERT INTO users (username, password_hash, full_name, role, created_by)
                   VALUES (?,?,?,?,?)""",
                (username, hash_password(password), full_name, role, admin["id"]),
            )
            uid = cur.lastrowid
        audit(admin, "create_user", "user", uid, f"{username}/{role}")
        return ok({"id": uid})
    except Exception as e:
        return err(f"Errore: {e}")


@app.post("/api/me/password")
async def api_change_own_password(request: Request):
    """Change the logged-in user's password; requires current password."""
    user = require_user(request)
    body = await request.json()
    current = body.get("current_password") or ""
    new = body.get("new_password") or ""
    if not current or not new:
        return err("Password attuale e nuova obbligatorie")
    if len(new) < 6:
        return err("Password nuova: minimo 6 caratteri")
    if not verify_password(current, user["password_hash"]):
        return err("Password attuale non corretta", 403)
    with db() as conn:
        conn.execute(
            "UPDATE users SET password_hash=? WHERE id=?",
            (hash_password(new), user["id"]),
        )
    audit(user, "change_password", "user", user["id"])
    return ok()


# ─── Catalog / search ────────────────────────────────────

@app.get("/api/suppliers")
def api_suppliers(request: Request):
    require_user(request)
    with db() as conn:
        rows = conn.execute(
            """SELECT s.*,
               (SELECT COUNT(*) FROM products p WHERE p.supplier_id=s.id AND p.visible=1) AS n_visible,
               (SELECT COUNT(*) FROM products p WHERE p.supplier_id=s.id) AS n_total
               FROM suppliers s ORDER BY s.name"""
        ).fetchall()
    return ok([dict(r) for r in rows if not is_excluded_supplier(r["name"])])


@app.get("/api/suppliers/{sid}")
def api_supplier(request: Request, sid: int):
    require_user(request)
    with db() as conn:
        row = conn.execute("SELECT * FROM suppliers WHERE id=?", (sid,)).fetchone()
    if not row or is_excluded_supplier(row["name"]):
        return err("Fornitore non trovato", 404)
    return ok(dict(row))


@app.put("/api/suppliers/{sid}")
async def api_update_supplier(request: Request, sid: int):
    admin = require_admin(request)
    body = await request.json()
    fields = [
        "delivery_days",
        "order_deadline",
        "min_order",
        "rep_name",
        "rep_whatsapp",
        "notes",
    ]
    with db() as conn:
        exists = conn.execute("SELECT id FROM suppliers WHERE id=?", (sid,)).fetchone()
        if not exists:
            return err("Fornitore non trovato", 404)
        sets = []
        vals = []
        for f in fields:
            if f in body:
                sets.append(f"{f}=?")
                vals.append(body[f] or "")
        sets.append("updated_at=datetime('now','localtime')")
        sets.append("updated_by=?")
        vals.append(admin["id"])
        vals.append(sid)
        conn.execute(f"UPDATE suppliers SET {', '.join(sets)} WHERE id=?", vals)
    audit(admin, "update_supplier", "supplier", sid)
    return ok()


@app.get("/api/products/search")
def api_search(request: Request, q: str = "", supplier_id: int | None = None, all: int = 0):
    user = require_user(request)
    is_admin = user["role"] == "admin"
    q = (q or "").strip()
    with db() as conn:
        excluded_ids = [
            r["id"]
            for r in conn.execute("SELECT id, name FROM suppliers").fetchall()
            if is_excluded_supplier(r["name"])
        ]
        if supplier_id and supplier_id in excluded_ids:
            return ok([])
        sql = """SELECT p.id, p.code, p.description, p.unit, p.default_order_unit, p.last_purchase_date,
                        p.purchase_count, p.visible, p.favorited, p.supplier_id,
                        s.name AS supplier_name"""
        if is_admin:
            sql += ", p.last_price, p.price_override, COALESCE(p.price_override, p.last_price) AS effective_price"
        sql += " FROM products p JOIN suppliers s ON s.id=p.supplier_id WHERE 1=1"
        params: list = []
        if excluded_ids:
            sql += f" AND p.supplier_id NOT IN ({','.join('?' * len(excluded_ids))})"
            params.extend(excluded_ids)
        if not all:
            sql += " AND p.visible=1"
        if supplier_id:
            sql += " AND p.supplier_id=?"
            params.append(supplier_id)
        if q:
            sql += " AND (p.description LIKE ? OR p.code LIKE ? OR s.name LIKE ?)"
            like = f"%{q}%"
            params.extend([like, like, like])
        # Wide list when browsing one supplier; keep search results capped otherwise
        limit = 5000 if supplier_id else 80
        sql += f" ORDER BY p.description COLLATE NOCASE LIMIT {limit}"
        rows = conn.execute(sql, params).fetchall()
    return ok([dict(r) for r in rows])


@app.get("/api/suppliers/{sid}/products")
def api_supplier_products(request: Request, sid: int, q: str = "", all: int = 1):
    """Products for a supplier with per-user favorite + recent-order stats.

    Default all=1 returns every catalog row; the mobile UI hides items not bought
    in the last 6 months under "Mostra tutti" (favorites and recently ordered stay visible).
    """
    user = require_user(request)
    is_admin = user["role"] == "admin"
    q = (q or "").strip()
    with db() as conn:
        exists = conn.execute("SELECT id, name FROM suppliers WHERE id=?", (sid,)).fetchone()
        if not exists or is_excluded_supplier(exists["name"]):
            return err("Fornitore non trovato", 404)
        sql = """SELECT p.id, p.code, p.description, p.unit, p.default_order_unit, p.last_purchase_date,
                        p.purchase_count, p.visible, p.supplier_id,
                        CASE WHEN f.product_id IS NOT NULL THEN 1 ELSE 0 END AS is_favorite,
                        COALESCE((
                          SELECT COUNT(*) FROM order_lines ol
                          JOIN orders o ON o.id=ol.order_id
                          WHERE ol.product_id=p.id AND o.status='inviato'
                        ), 0) AS times_ordered,
                        (
                          SELECT MAX(o.sent_at) FROM order_lines ol
                          JOIN orders o ON o.id=ol.order_id
                          WHERE ol.product_id=p.id AND o.status='inviato'
                        ) AS last_ordered_at"""
        if is_admin:
            sql += """, p.last_price, p.price_override,
                       COALESCE(p.price_override, p.last_price) AS effective_price,
                       (SELECT COUNT(*) FROM user_product_favorites uf WHERE uf.product_id=p.id) AS favorite_count"""
        sql += """ FROM products p
                   LEFT JOIN user_product_favorites f
                     ON f.product_id=p.id AND f.user_id=?
                   WHERE p.supplier_id=?"""
        params: list = [user["id"], sid]
        if not all:
            sql += " AND p.visible=1"
        if q:
            sql += " AND (p.description LIKE ? OR p.code LIKE ?)"
            like = f"%{q}%"
            params.extend([like, like])
        sql += " ORDER BY p.description COLLATE NOCASE"
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        # Has this supplier a previous sent (or any) order to repeat?
        last_sent = conn.execute(
            """SELECT id, sent_at FROM orders
               WHERE supplier_id=? AND status='inviato'
               ORDER BY sent_at DESC LIMIT 1""",
            (sid,),
        ).fetchone()
        can_repeat = bool(last_sent)
    return ok({"products": rows, "can_repeat": can_repeat, "last_sent_at": last_sent["sent_at"] if last_sent else None})


@app.get("/api/units")
def api_units(request: Request):
    """Canonical order units for the mobile selector."""
    require_user(request)
    return ok(ORDER_UNITS)


@app.put("/api/products/{pid}/default-unit")
async def api_product_default_unit(request: Request, pid: int):
    """Admin: set the default order unit for a product (Kg/Ct/Cassa/Pezzo)."""
    admin = require_admin(request)
    body = await request.json()
    unit = normalize_order_unit(body.get("unit") or body.get("default_order_unit"))
    if unit not in ORDER_UNIT_VALUES:
        return err("Unità non valida")
    with db() as conn:
        row = conn.execute("SELECT id FROM products WHERE id=?", (pid,)).fetchone()
        if not row:
            return err("Prodotto non trovato", 404)
        conn.execute("UPDATE products SET default_order_unit=? WHERE id=?", (unit, pid))
    audit(admin, "set_default_order_unit", "product", pid, unit)
    return ok({"id": pid, "default_order_unit": unit})


@app.post("/api/products/{pid}/visibility")
async def api_visibility(request: Request, pid: int):
    admin = require_admin(request)
    body = await request.json()
    vis = 1 if body.get("visible") else 0
    with db() as conn:
        conn.execute("UPDATE products SET visible=? WHERE id=?", (vis, pid))
    audit(admin, "set_visibility", "product", pid, f"visible={vis}")
    return ok()


@app.post("/api/products/{pid}/favorite")
async def api_favorite(request: Request, pid: int):
    """Toggle per-user favorite star (not global)."""
    user = require_user(request)
    body = await request.json()
    fav = 1 if body.get("favorited") else 0
    with db() as conn:
        prod = conn.execute("SELECT id FROM products WHERE id=?", (pid,)).fetchone()
        if not prod:
            return err("Prodotto non trovato", 404)
        if fav:
            conn.execute(
                "INSERT OR IGNORE INTO user_product_favorites (user_id, product_id) VALUES (?,?)",
                (user["id"], pid),
            )
        else:
            conn.execute(
                "DELETE FROM user_product_favorites WHERE user_id=? AND product_id=?",
                (user["id"], pid),
            )
    audit(user, "set_favorite", "product", pid, f"favorited={fav}")
    return ok({"id": pid, "favorited": fav})


@app.post("/api/products/{pid}/price_override")
async def api_price_override(request: Request, pid: int):
    admin = require_admin(request)
    body = await request.json()
    val = body.get("price_override")
    with db() as conn:
        conn.execute(
            "UPDATE products SET price_override=? WHERE id=?",
            (None if val in (None, "") else float(val), pid),
        )
    audit(admin, "price_override", "product", pid, str(val))
    return ok()


@app.post("/api/catalog/reimport")
def api_reimport(request: Request):
    admin = require_admin(request)
    try:
        stats = import_catalog(user_id=admin["id"], username=admin["username"])
        return ok(stats)
    except Exception as e:
        return err(str(e), 500)


@app.post("/api/catalog/upload")
async def api_catalog_upload(request: Request, file: UploadFile = File(...)):
    """Admin-only: upload catalog CSV to DATA_DIR and import (prices never stored in git)."""
    admin = require_admin(request)
    if not file.filename or not file.filename.lower().endswith(".csv"):
        return err("Serve un file .csv", 400)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    dest = DATA_DIR / "catalogo_import.csv"
    content = await file.read()
    if not content:
        return err("File vuoto", 400)
    dest.write_bytes(content)
    try:
        stats = import_catalog(csv_path=dest, user_id=admin["id"], username=admin["username"])
        return ok(stats)
    except Exception as e:
        return err(str(e), 500)


@app.get("/api/catalog/stats")
def api_catalog_stats(request: Request):
    require_user(request)
    with db() as conn:
        tot = conn.execute("SELECT COUNT(*) c FROM products").fetchone()["c"]
        vis = conn.execute("SELECT COUNT(*) c FROM products WHERE visible=1").fetchone()["c"]
        nsup = conn.execute("SELECT COUNT(*) c FROM suppliers").fetchone()["c"]
        imported = conn.execute(
            "SELECT value FROM app_meta WHERE key='catalog_imported_at'"
        ).fetchone()
    return ok(
        {
            "products_total": tot,
            "products_visible": vis,
            "suppliers": nsup,
            "imported_at": imported["value"] if imported else None,
        }
    )


# ─── Orders ──────────────────────────────────────────────

@app.get("/api/orders")
def api_orders(request: Request, status: str | None = None, supplier_id: int | None = None):
    require_user(request)
    with db() as conn:
        sql = """SELECT o.*, s.name AS supplier_name,
                 (SELECT COUNT(*) FROM order_lines ol WHERE ol.order_id=o.id) AS n_lines
                 FROM orders o JOIN suppliers s ON s.id=o.supplier_id WHERE 1=1"""
        params = []
        if status:
            sql += " AND o.status=?"
            params.append(status)
        if supplier_id:
            sql += " AND o.supplier_id=?"
            params.append(supplier_id)
        sql += " ORDER BY COALESCE(o.updated_at, o.created_at) DESC LIMIT 100"
        rows = conn.execute(sql, params).fetchall()
    return ok([dict(r) for r in rows])


@app.get("/api/orders/{oid}")
def api_order(request: Request, oid: int):
    require_user(request)
    with db() as conn:
        o = conn.execute(
            """SELECT o.*, s.name AS supplier_name, s.rep_name, s.rep_whatsapp,
                      s.delivery_days, s.order_deadline, s.min_order
               FROM orders o JOIN suppliers s ON s.id=o.supplier_id WHERE o.id=?""",
            (oid,),
        ).fetchone()
        if not o:
            return err("Ordine non trovato", 404)
        lines = conn.execute(
            "SELECT * FROM order_lines WHERE order_id=? ORDER BY id", (oid,)
        ).fetchall()
    data = dict(o)
    data["lines"] = [dict(l) for l in lines]
    return ok(data)


@app.post("/api/orders")
async def api_create_order(request: Request):
    user = require_user(request)
    body = await request.json()
    sid = body.get("supplier_id")
    if not sid:
        return err("supplier_id obbligatorio")
    created_new = False
    with db() as conn:
        sup = conn.execute("SELECT id, name FROM suppliers WHERE id=?", (sid,)).fetchone()
        if not sup or is_excluded_supplier(sup["name"]):
            return err("Fornitore non disponibile per ordini", 404)
        # reuse existing draft for supplier if any
        existing = conn.execute(
            "SELECT id FROM orders WHERE supplier_id=? AND status='bozza' ORDER BY id DESC LIMIT 1",
            (sid,),
        ).fetchone()
        if existing and not body.get("force_new"):
            oid = existing["id"]
        else:
            cur = conn.execute(
                """INSERT INTO orders (supplier_id, status, delivery_date, notes, created_by, updated_at, updated_by)
                   VALUES (?, 'bozza', ?, ?, ?, datetime('now','localtime'), ?)""",
                (sid, body.get("delivery_date"), body.get("notes") or "", user["id"], user["id"]),
            )
            oid = cur.lastrowid
            created_new = True
    if created_new:
        audit(user, "create_order", "order", oid)
    return ok({"id": oid})


@app.put("/api/orders/{oid}")
async def api_update_order(request: Request, oid: int):
    user = require_user(request)
    body = await request.json()
    with db() as conn:
        o = conn.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        if not o:
            return err("Ordine non trovato", 404)
        if o["status"] == "inviato" and user["role"] != "admin":
            return err("Ordine già inviato", 403)
        if "delivery_date" in body:
            conn.execute("UPDATE orders SET delivery_date=? WHERE id=?", (body["delivery_date"], oid))
        if "notes" in body:
            conn.execute("UPDATE orders SET notes=? WHERE id=?", (body["notes"] or "", oid))
        conn.execute(
            "UPDATE orders SET updated_at=datetime('now','localtime'), updated_by=? WHERE id=?",
            (user["id"], oid),
        )
    audit(user, "update_order", "order", oid)
    return ok()


@app.delete("/api/orders/{oid}")
def api_delete_order(request: Request, oid: int):
    """Delete a draft order. Admin: any draft. Employee: only own drafts. Never sent orders."""
    user = require_user(request)
    with db() as conn:
        o = conn.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        if not o:
            return err("Ordine non trovato", 404)
        if o["status"] != "bozza":
            return err("Si possono eliminare solo le bozze (non gli ordini inviati)", 403)
        if user["role"] != "admin" and o["created_by"] != user["id"]:
            return err("Puoi eliminare solo le tue bozze", 403)
        conn.execute("DELETE FROM order_lines WHERE order_id=?", (oid,))
        conn.execute("DELETE FROM orders WHERE id=?", (oid,))
    audit(user, "delete_order", "order", oid)
    return ok()


@app.post("/api/orders/{oid}/lines")
async def api_add_line(request: Request, oid: int):
    user = require_user(request)
    body = await request.json()
    with db() as conn:
        o = conn.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        if not o:
            return err("Ordine non trovato", 404)
        if o["status"] == "inviato" and user["role"] != "admin":
            return err("Ordine già inviato", 403)
        pid = body.get("product_id")
        desc = body.get("description")
        if "quantity" in body and body.get("quantity") is not None and body.get("quantity") != "":
            qty = float(body.get("quantity"))
        else:
            qty = 1.0
        set_abs = bool(body.get("set"))
        unit_in = body.get("unit")
        lid = None
        if pid:
            # upsert quantity if product already in order
            existing = conn.execute(
                "SELECT id, quantity, unit FROM order_lines WHERE order_id=? AND product_id=?",
                (oid, pid),
            ).fetchone()
            prod = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
            if not prod:
                return err("Prodotto non trovato", 404)
            default_u = normalize_order_unit(
                prod["default_order_unit"] if "default_order_unit" in prod.keys() else None,
                fallback=infer_order_unit(prod["unit"], prod["description"]),
            )
            line_unit = normalize_order_unit(unit_in, fallback=default_u) if unit_in not in (None, "") else (
                normalize_order_unit(existing["unit"], fallback=default_u) if existing else default_u
            )
            if existing:
                new_qty = qty if set_abs else (float(existing["quantity"]) + qty)
                if set_abs and new_qty <= 0:
                    conn.execute("DELETE FROM order_lines WHERE id=?", (existing["id"],))
                    lid = existing["id"]
                else:
                    # If client sent unit, update it; else keep existing line unit
                    if unit_in not in (None, ""):
                        line_unit = normalize_order_unit(unit_in, fallback=default_u)
                    else:
                        line_unit = normalize_order_unit(existing["unit"], fallback=default_u)
                    conn.execute(
                        "UPDATE order_lines SET quantity=?, unit=? WHERE id=?",
                        (new_qty, line_unit, existing["id"]),
                    )
                    lid = existing["id"]
            elif set_abs and qty <= 0:
                lid = None
            else:
                cur = conn.execute(
                    """INSERT INTO order_lines (order_id, product_id, description, unit, quantity)
                       VALUES (?,?,?,?,?)""",
                    (oid, pid, prod["description"], line_unit, qty),
                )
                lid = cur.lastrowid
        else:
            if not desc:
                return err("description obbligatoria")
            line_unit = normalize_order_unit(unit_in, fallback="Pezzo")
            cur = conn.execute(
                """INSERT INTO order_lines (order_id, product_id, description, unit, quantity)
                   VALUES (?,?,?,?,?)""",
                (oid, None, desc, line_unit, qty),
            )
            lid = cur.lastrowid
        conn.execute(
            "UPDATE orders SET updated_at=datetime('now','localtime'), updated_by=? WHERE id=?",
            (user["id"], oid),
        )
    if lid is not None:
        audit(user, "add_order_line", "order", oid, f"line={lid}")
    return ok({"id": lid})


@app.put("/api/orders/{oid}/lines/{lid}")
async def api_update_line(request: Request, oid: int, lid: int):
    user = require_user(request)
    body = await request.json()
    with db() as conn:
        o = conn.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        if not o:
            return err("Ordine non trovato", 404)
        if "quantity" in body:
            conn.execute(
                "UPDATE order_lines SET quantity=? WHERE id=? AND order_id=?",
                (float(body["quantity"]), lid, oid),
            )
        if "unit" in body and body.get("unit") not in (None, ""):
            conn.execute(
                "UPDATE order_lines SET unit=? WHERE id=? AND order_id=?",
                (normalize_order_unit(body.get("unit")), lid, oid),
            )
        if "received_status" in body:
            rs = body["received_status"]
            if rs not in ("pending", "si", "no", "parziale"):
                return err("received_status non valido")
            conn.execute(
                "UPDATE order_lines SET received_status=? WHERE id=? AND order_id=?",
                (rs, lid, oid),
            )
        if "received_note" in body:
            conn.execute(
                "UPDATE order_lines SET received_note=? WHERE id=? AND order_id=?",
                (body["received_note"] or "", lid, oid),
            )
        conn.execute(
            "UPDATE orders SET updated_at=datetime('now','localtime'), updated_by=? WHERE id=?",
            (user["id"], oid),
        )
    return ok()


@app.delete("/api/orders/{oid}/lines/{lid}")
def api_delete_line(request: Request, oid: int, lid: int):
    user = require_user(request)
    with db() as conn:
        conn.execute("DELETE FROM order_lines WHERE id=? AND order_id=?", (lid, oid))
        conn.execute(
            "UPDATE orders SET updated_at=datetime('now','localtime'), updated_by=? WHERE id=?",
            (user["id"], oid),
        )
    audit(user, "delete_order_line", "order", oid, f"line={lid}")
    return ok()


@app.post("/api/orders/{oid}/repeat-last")
def api_repeat_last(request: Request, oid: int):
    """Copy lines from last sent order of same supplier into this draft."""
    user = require_user(request)
    with db() as conn:
        o = conn.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        if not o:
            return err("Ordine non trovato", 404)
        if o["status"] != "bozza":
            return err("Si può ripetere solo su una bozza")
        last = conn.execute(
            """SELECT id FROM orders WHERE supplier_id=? AND status='inviato' AND id!=?
               ORDER BY sent_at DESC LIMIT 1""",
            (o["supplier_id"], oid),
        ).fetchone()
        if not last:
            return err("Nessun ordine inviato precedente per questo fornitore")
        lines = conn.execute(
            "SELECT product_id, description, unit, quantity FROM order_lines WHERE order_id=?",
            (last["id"],),
        ).fetchall()
        if not lines:
            return err("L'ultimo ordine inviato non ha righe")
        conn.execute("DELETE FROM order_lines WHERE order_id=?", (oid,))
        for ln in lines:
            conn.execute(
                """INSERT INTO order_lines (order_id, product_id, description, unit, quantity)
                   VALUES (?,?,?,?,?)""",
                (oid, ln["product_id"], ln["description"], ln["unit"] or "Kg", ln["quantity"]),
            )
        conn.execute(
            "UPDATE orders SET updated_at=datetime('now','localtime'), updated_by=? WHERE id=?",
            (user["id"], oid),
        )
    audit(user, "repeat_last_order", "order", oid, f"from={last['id']}")
    return ok({"id": oid, "copied_from": last["id"], "lines": len(lines)})


@app.post("/api/suppliers/{sid}/repeat-last")
def api_supplier_repeat_last(request: Request, sid: int):
    """Create a fresh draft for the supplier and copy the last sent order into it."""
    user = require_user(request)
    with db() as conn:
        sup = conn.execute("SELECT id, name FROM suppliers WHERE id=?", (sid,)).fetchone()
        if not sup or is_excluded_supplier(sup["name"]):
            return err("Fornitore non trovato", 404)
        last = conn.execute(
            """SELECT id FROM orders WHERE supplier_id=? AND status='inviato'
               ORDER BY sent_at DESC LIMIT 1""",
            (sid,),
        ).fetchone()
        if not last:
            return err("Nessun ordine inviato precedente per questo fornitore")
        lines = conn.execute(
            "SELECT product_id, description, unit, quantity FROM order_lines WHERE order_id=?",
            (last["id"],),
        ).fetchall()
        if not lines:
            return err("L'ultimo ordine inviato non ha righe")
        cur = conn.execute(
            """INSERT INTO orders (supplier_id, status, created_by, updated_at, updated_by)
               VALUES (?, 'bozza', ?, datetime('now','localtime'), ?)""",
            (sid, user["id"], user["id"]),
        )
        oid = cur.lastrowid
        for ln in lines:
            conn.execute(
                """INSERT INTO order_lines (order_id, product_id, description, unit, quantity)
                   VALUES (?,?,?,?,?)""",
                (oid, ln["product_id"], ln["description"], ln["unit"] or "Kg", ln["quantity"]),
            )
    audit(user, "repeat_last_order", "order", oid, f"from={last['id']} supplier={sid}")
    return ok({"id": oid, "copied_from": last["id"], "lines": len(lines)})


@app.get("/api/orders/favorites/{sid}")
def api_favorites(request: Request, sid: int):
    """Current user's favorite products for a supplier (admin sees own stars too)."""
    user = require_user(request)
    with db() as conn:
        rows = conn.execute(
            """SELECT p.id, p.description, p.unit, p.default_order_unit, p.code FROM products p
               JOIN user_product_favorites f ON f.product_id=p.id AND f.user_id=?
               WHERE p.supplier_id=?
               ORDER BY p.description""",
            (user["id"], sid),
        ).fetchall()
    return ok([dict(r) for r in rows])


@app.post("/api/orders/{oid}/send")
def api_send_order(request: Request, oid: int):
    """Admin only: mark as sent and return wa.me link."""
    admin = require_admin(request)
    with db() as conn:
        o = conn.execute(
            """SELECT o.*, s.name AS supplier_name, s.rep_name, s.rep_whatsapp
               FROM orders o JOIN suppliers s ON s.id=o.supplier_id WHERE o.id=?""",
            (oid,),
        ).fetchone()
        if not o:
            return err("Ordine non trovato", 404)
        lines = conn.execute(
            "SELECT * FROM order_lines WHERE order_id=? ORDER BY id", (oid,)
        ).fetchall()
        if not lines:
            return err("Ordine vuoto")
        phone = (o["rep_whatsapp"] or "").strip()
        phone_digits = "".join(c for c in phone if c.isdigit())
        if phone_digits.startswith("00"):
            phone_digits = phone_digits[2:]
        if phone_digits.startswith("3") and len(phone_digits) == 10:
            phone_digits = "39" + phone_digits  # IT default
        if not phone_digits:
            return err("Numero WhatsApp del rappresentante non configurato")

        delivery = o["delivery_date"] or "da concordare"
        saluto = f"Buongiorno{(' ' + o['rep_name']) if o['rep_name'] else ''},"
        rows_txt = []
        for ln in lines:
            q = ln["quantity"]
            qstr = str(int(q)) if float(q).is_integer() else str(q)
            unit = normalize_order_unit(ln["unit"], fallback="") or ""
            # e.g. "3 Cassa Zucchine"
            if unit:
                rows_txt.append(f"- {qstr} {unit} {ln['description']}")
            else:
                rows_txt.append(f"- {qstr} {ln['description']}")
        msg = (
            f"{saluto}\n\n"
            f"Ordine POTA LA GASTRO\n"
            f"Consegna: {delivery}\n\n"
            + "\n".join(rows_txt)
            + "\n\nGrazie,\nGianluca"
        )
        wa = f"https://wa.me/{phone_digits}?text={quote(msg)}"
        conn.execute(
            """UPDATE orders SET status='inviato', wa_link=?, sent_at=datetime('now','localtime'),
               sent_by=?, updated_at=datetime('now','localtime'), updated_by=? WHERE id=?""",
            (wa, admin["id"], admin["id"], oid),
        )
    audit(admin, "send_order", "order", oid, phone_digits)
    return ok({"wa_link": wa, "message": msg, "phone": phone_digits})


# ─── Recipes / Food cost ─────────────────────────────────

@app.get("/api/recipes")
def api_recipes(request: Request):
    require_user(request)
    with db() as conn:
        rows = conn.execute(
            "SELECT id, name, category, yield_qty, yield_unit, sell_price, created_at, updated_at FROM recipes ORDER BY name"
        ).fetchall()
        # weekly new recipes
        week = conn.execute(
            """SELECT COUNT(*) c FROM recipes
               WHERE date(created_at) >= date('now','localtime','weekday 1','-7 days')
               AND date(created_at) <= date('now','localtime')"""
        ).fetchone()["c"]
        # ISO week Monday-based: simpler — recipes created in current calendar week
        week = conn.execute(
            """SELECT COUNT(*) c FROM recipes
               WHERE strftime('%Y-%W', created_at) = strftime('%Y-%W', 'now', 'localtime')"""
        ).fetchone()["c"]
    return ok({"recipes": [dict(r) for r in rows], "weekly_new": week, "weekly_goal": 7})


@app.get("/api/recipes/{rid}")
def api_recipe(request: Request, rid: int):
    user = require_user(request)
    is_admin = user["role"] == "admin"
    with db() as conn:
        cost = compute_recipe_cost(conn, rid)
        if cost.get("error"):
            return err(cost["error"], 404)
        ings = conn.execute(
            "SELECT * FROM recipe_ingredients WHERE recipe_id=? ORDER BY sort_order, id",
            (rid,),
        ).fetchall()
        recipe = conn.execute("SELECT * FROM recipes WHERE id=?", (rid,)).fetchone()
    data = dict(recipe)
    data["ingredients_raw"] = [dict(i) for i in ings]
    if is_admin:
        data["cost"] = cost
    else:
        # strip prices
        data["cost"] = {
            "recipe_id": rid,
            "name": cost["name"],
            "category": cost["category"],
            "yield_qty": cost["yield_qty"],
            "yield_unit": cost["yield_unit"],
            "ingredients": [
                {"id": i["id"], "kind": i["kind"], "name": i["name"], "quantity": i["quantity"], "unit": i["unit"]}
                for i in cost["ingredients"]
            ],
            "notes": cost["notes"],
        }
    return ok(data)


@app.post("/api/recipes/preview-cost")
async def api_preview_recipe_cost(request: Request):
    """Compute food cost for a recipe draft without saving. Prices only for admin."""
    user = require_user(request)
    is_admin = user["role"] == "admin"
    body = await request.json()
    yield_qty = float(body.get("yield_qty") or 0)
    yield_unit = (body.get("yield_unit") or "kg").lower()
    if yield_unit in ("g", "gr"):
        yield_kg = yield_qty / 1000.0
    else:
        yield_kg = yield_qty
    ingredients = body.get("ingredients") or []
    with db() as conn:
        line_costs = []
        total = 0.0
        missing = False
        for idx, ing in enumerate(ingredients):
            fake = {
                "id": idx,
                "kind": ing.get("kind") or "product",
                "name": ing.get("name") or "",
                "quantity": float(ing.get("quantity") or 0),
                "unit": ing.get("unit") or "kg",
                "product_id": ing.get("product_id"),
                "sub_recipe_id": ing.get("sub_recipe_id"),
                "unit_cost_override": None if ing.get("unit_cost_override") in (None, "") else float(ing["unit_cost_override"]),
            }
            info = ingredient_cost(conn, fake)
            if info.get("line_cost") is not None:
                total += float(info["line_cost"])
            else:
                missing = True
            line_costs.append(info)
        extras = (
            float(body.get("extra_condiments") or 0)
            + float(body.get("extra_packaging") or 0)
            + float(body.get("extra_energy_labor") or 0)
        )
        total += extras
        cost_per_kg = (total / yield_kg) if yield_kg > 0 else None
    if not is_admin:
        return ok({
            "yield_kg": yield_kg,
            "ingredient_count": len(ingredients),
            "has_missing_prices": missing,
        })
    return ok({
        "total_cost": round(total, 4),
        "cost_per_kg": round(cost_per_kg, 4) if cost_per_kg is not None else None,
        "yield_kg": yield_kg,
        "extras": extras,
        "has_missing_prices": missing,
        "ingredients": [
            {
                "name": i["name"],
                "quantity": i["quantity"],
                "unit": i["unit"],
                "line_cost": i.get("line_cost"),
                "note": i.get("note"),
            }
            for i in line_costs
        ],
    })


@app.post("/api/recipes")
async def api_create_recipe(request: Request):
    user = require_user(request)
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        return err("Nome obbligatorio")
    with db() as conn:
        cur = conn.execute(
            """INSERT INTO recipes (name, category, yield_qty, yield_unit, sell_price,
               extra_condiments, extra_packaging, extra_energy_labor, notes, created_by, updated_at, updated_by)
               VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now','localtime'),?)""",
            (
                name,
                body.get("category") or "",
                float(body.get("yield_qty") or 1),
                body.get("yield_unit") or "kg",
                float(body["sell_price"]) if body.get("sell_price") not in (None, "") else None,
                float(body.get("extra_condiments") or 0),
                float(body.get("extra_packaging") or 0),
                float(body.get("extra_energy_labor") or 0),
                body.get("notes") or "",
                user["id"],
                user["id"],
            ),
        )
        rid = cur.lastrowid
        for i, ing in enumerate(body.get("ingredients") or []):
            _insert_ing(conn, rid, ing, i)
    audit(user, "create_recipe", "recipe", rid, name)
    return ok({"id": rid})


def _insert_ing(conn, rid, ing, sort_order=0):
    kind = ing.get("kind") or "product"
    name = (ing.get("name") or "").strip()
    qty = float(ing.get("quantity") or 0)
    unit = ing.get("unit") or "kg"
    override = ing.get("unit_cost_override")
    override = None if override in (None, "") else float(override)
    if kind == "product":
        conn.execute(
            """INSERT INTO recipe_ingredients
               (recipe_id, kind, product_id, sub_recipe_id, name, quantity, unit, unit_cost_override, sort_order)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (rid, "product", ing.get("product_id"), None, name, qty, unit, override, sort_order),
        )
    else:
        conn.execute(
            """INSERT INTO recipe_ingredients
               (recipe_id, kind, product_id, sub_recipe_id, name, quantity, unit, unit_cost_override, sort_order)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (rid, "recipe", None, ing.get("sub_recipe_id"), name, qty, unit, override, sort_order),
        )


@app.put("/api/recipes/{rid}")
async def api_update_recipe(request: Request, rid: int):
    user = require_user(request)
    body = await request.json()
    with db() as conn:
        r = conn.execute("SELECT * FROM recipes WHERE id=?", (rid,)).fetchone()
        if not r:
            return err("Ricetta non trovata", 404)
        # non-admin cannot change prices/extras/sell
        sell = body.get("sell_price", r["sell_price"])
        extras = (
            body.get("extra_condiments", r["extra_condiments"]),
            body.get("extra_packaging", r["extra_packaging"]),
            body.get("extra_energy_labor", r["extra_energy_labor"]),
        )
        if user["role"] != "admin":
            sell = r["sell_price"]
            extras = (r["extra_condiments"], r["extra_packaging"], r["extra_energy_labor"])
        conn.execute(
            """UPDATE recipes SET name=?, category=?, yield_qty=?, yield_unit=?, sell_price=?,
               extra_condiments=?, extra_packaging=?, extra_energy_labor=?, notes=?,
               updated_at=datetime('now','localtime'), updated_by=? WHERE id=?""",
            (
                (body.get("name") or r["name"]).strip(),
                body.get("category", r["category"]) or "",
                float(body.get("yield_qty", r["yield_qty"])),
                body.get("yield_unit", r["yield_unit"]) or "kg",
                None if sell in (None, "") else float(sell),
                float(extras[0] or 0),
                float(extras[1] or 0),
                float(extras[2] or 0),
                body.get("notes", r["notes"]) or "",
                user["id"],
                rid,
            ),
        )
        if "ingredients" in body:
            conn.execute("DELETE FROM recipe_ingredients WHERE recipe_id=?", (rid,))
            for i, ing in enumerate(body["ingredients"]):
                _insert_ing(conn, rid, ing, i)
    audit(user, "update_recipe", "recipe", rid)
    return ok()


@app.delete("/api/recipes/{rid}")
def api_delete_recipe(request: Request, rid: int):
    admin = require_admin(request)
    with db() as conn:
        conn.execute("DELETE FROM recipe_ingredients WHERE recipe_id=?", (rid,))
        conn.execute("DELETE FROM recipes WHERE id=?", (rid,))
    audit(admin, "delete_recipe", "recipe", rid)
    return ok()


@app.get("/api/recipes/{rid}/scale")
def api_scale(request: Request, rid: int, kg: float = 1.0):
    user = require_user(request)
    is_admin = user["role"] == "admin"
    with db() as conn:
        cost = compute_recipe_cost(conn, rid)
        if cost.get("error"):
            return err(cost["error"], 404)
        scaled = scale_ingredients(cost["ingredients"], cost["yield_kg"] or 1, kg)
    payload = {
        "target_kg": kg,
        "original_yield_kg": cost["yield_kg"],
        "ingredients": [
            {
                "name": i["name"],
                "quantity": i["quantity"],
                "unit": i["unit"],
                **({"line_cost": i.get("line_cost")} if is_admin else {}),
            }
            for i in scaled
        ],
    }
    if is_admin and cost["cost_per_kg"] is not None:
        payload["estimated_total_cost"] = round(cost["cost_per_kg"] * kg, 4)
        payload["cost_per_kg"] = cost["cost_per_kg"]
    return ok(payload)


@app.get("/api/audit")
def api_audit(request: Request, limit: int = 50):
    require_admin(request)
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (min(limit, 200),)
        ).fetchall()
    return ok([dict(r) for r in rows])


@app.get("/health")
def health():
    return {"status": "ok", "app": "POTA LA GASTRO"}
