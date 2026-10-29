"""SQLite helpers for POTA LA GASTRO."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = Path(os.environ.get("DATA_DIR") or (_ROOT / "data"))
DB_PATH = _DATA_DIR / "app.db"

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  full_name TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin','dipendente')),
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  created_by INTEGER
);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER,
  username TEXT,
  action TEXT NOT NULL,
  entity TEXT,
  entity_id TEXT,
  detail TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS suppliers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  delivery_days TEXT DEFAULT '',
  order_deadline TEXT DEFAULT '',
  min_order TEXT DEFAULT '',
  rep_name TEXT DEFAULT '',
  rep_whatsapp TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  updated_at TEXT,
  updated_by INTEGER
);

CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
  code TEXT DEFAULT '',
  description TEXT NOT NULL,
  unit TEXT DEFAULT '',
  last_price REAL,
  last_purchase_date TEXT,
  last_invoice TEXT,
  price_min REAL,
  price_max REAL,
  purchase_count INTEGER DEFAULT 0,
  visible INTEGER NOT NULL DEFAULT 1,
  favorited INTEGER NOT NULL DEFAULT 0,
  price_override REAL,
  UNIQUE(supplier_id, code, description)
);

CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
  status TEXT NOT NULL DEFAULT 'bozza' CHECK(status IN ('bozza','inviato')),
  delivery_date TEXT,
  notes TEXT DEFAULT '',
  wa_link TEXT,
  sent_at TEXT,
  sent_by INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  created_by INTEGER,
  updated_at TEXT,
  updated_by INTEGER
);

CREATE TABLE IF NOT EXISTS order_lines (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  product_id INTEGER REFERENCES products(id),
  description TEXT NOT NULL,
  unit TEXT DEFAULT '',
  quantity REAL NOT NULL DEFAULT 1,
  received_status TEXT DEFAULT 'pending' CHECK(received_status IN ('pending','si','no','parziale')),
  received_note TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS recipes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  category TEXT DEFAULT '',
  yield_qty REAL NOT NULL DEFAULT 1,
  yield_unit TEXT NOT NULL DEFAULT 'kg',
  sell_price REAL,
  extra_condiments REAL DEFAULT 0,
  extra_packaging REAL DEFAULT 0,
  extra_energy_labor REAL DEFAULT 0,
  notes TEXT DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  created_by INTEGER,
  updated_at TEXT,
  updated_by INTEGER
);

CREATE TABLE IF NOT EXISTS recipe_ingredients (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK(kind IN ('product','recipe')),
  product_id INTEGER REFERENCES products(id),
  sub_recipe_id INTEGER REFERENCES recipes(id),
  name TEXT NOT NULL,
  quantity REAL NOT NULL,
  unit TEXT NOT NULL DEFAULT 'kg',
  unit_cost_override REAL,
  sort_order INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS app_meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
"""


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextmanager
def db():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db() as conn:
        conn.executescript(SCHEMA)
