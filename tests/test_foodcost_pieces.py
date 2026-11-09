"""Pezzi facoltativi nel food cost.

Fondelle: 2 kg / 25 pezzi = 80 g a pezzo.
Usate in un'altra ricetta, 12 pezzi costano come 12 × il peso del singolo pezzo.
Le ricette senza pezzi restano solo a peso. La migrazione non cancella dati.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="gastro-pezzi-")
os.environ["DATA_DIR"] = _TMP
os.environ["HTTPS_ONLY"] = "0"
os.environ["SESSION_SECRET"] = "test-secret-pezzi"
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "secret123"
os.environ["ADMIN_FULL_NAME"] = "Admin Test"
os.environ.pop("CATALOG_CSV", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.db import SCHEMA, _migrate, _table_cols, db  # noqa: E402
from app.foodcost import compute_recipe_cost, parse_yield_pieces  # noqa: E402
from app.main import app  # noqa: E402


def _mem():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


MIN_SCHEMA = """
CREATE TABLE products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  price_override REAL,
  last_price REAL
);
CREATE TABLE recipes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  category TEXT DEFAULT '',
  yield_qty REAL NOT NULL DEFAULT 1,
  yield_unit TEXT NOT NULL DEFAULT 'kg',
  yield_pieces REAL,
  sell_price REAL,
  extra_condiments REAL DEFAULT 0,
  extra_packaging REAL DEFAULT 0,
  extra_energy_labor REAL DEFAULT 0,
  notes TEXT DEFAULT ''
);
CREATE TABLE recipe_ingredients (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  recipe_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  product_id INTEGER,
  sub_recipe_id INTEGER,
  name TEXT NOT NULL,
  quantity REAL NOT NULL,
  unit TEXT NOT NULL DEFAULT 'kg',
  unit_cost_override REAL,
  sort_order INTEGER DEFAULT 0
);
"""


def _seed_fondelle(conn, *, pieces=25, yield_qty=2, yield_unit="kg"):
    """1 kg farina + 1 kg latte a 2 €/kg → totale 4 €, resa richiesta."""
    farina = conn.execute(
        "INSERT INTO products (last_price) VALUES (2.0)"
    ).lastrowid
    latte = conn.execute(
        "INSERT INTO products (last_price) VALUES (2.0)"
    ).lastrowid
    rid = conn.execute(
        """INSERT INTO recipes (name, category, yield_qty, yield_unit, yield_pieces)
           VALUES ('Fondelle per crespelle', 'basi', ?, ?, ?)""",
        (yield_qty, yield_unit, pieces),
    ).lastrowid
    conn.execute(
        """INSERT INTO recipe_ingredients
           (recipe_id, kind, product_id, name, quantity, unit, sort_order)
           VALUES (?, 'product', ?, 'Farina 00', 1, 'kg', 0)""",
        (rid, farina),
    )
    conn.execute(
        """INSERT INTO recipe_ingredients
           (recipe_id, kind, product_id, name, quantity, unit, sort_order)
           VALUES (?, 'product', ?, 'Latte', 1, 'kg', 1)""",
        (rid, latte),
    )
    return rid


class PieceMathTests(unittest.TestCase):
    def test_parse_pieces_optional(self):
        self.assertEqual(parse_yield_pieces(None), (None, None))
        self.assertEqual(parse_yield_pieces(""), (None, None))
        self.assertEqual(parse_yield_pieces("  "), (None, None))
        value, err = parse_yield_pieces("25")
        self.assertIsNone(err)
        self.assertEqual(value, 25)
        value, err = parse_yield_pieces("25,5")
        self.assertIsNone(err)
        self.assertEqual(value, 25.5)
        self.assertIsNotNone(parse_yield_pieces(0)[1])
        self.assertIsNotNone(parse_yield_pieces(-3)[1])
        self.assertIsNotNone(parse_yield_pieces("abc")[1])

    def test_crespelle_2kg_25_pezzi_sono_80g(self):
        conn = _mem()
        conn.executescript(MIN_SCHEMA)
        rid = _seed_fondelle(conn)
        cost = compute_recipe_cost(conn, rid)
        self.assertEqual(cost["yield_kg"], 2)
        self.assertEqual(cost["yield_pieces"], 25)
        self.assertAlmostEqual(cost["piece_weight_g"], 80.0, places=4)
        self.assertAlmostEqual(cost["piece_weight_kg"], 0.08, places=4)
        self.assertAlmostEqual(cost["total_cost"], 4.0, places=4)
        self.assertAlmostEqual(cost["cost_per_kg"], 2.0, places=4)
        self.assertAlmostEqual(cost["cost_per_piece"], 0.16, places=4)
        # 4 € / 25 pezzi = 0,16 €, uguale a 80 g × 2 €/kg
        self.assertAlmostEqual(cost["cost_per_piece"], cost["cost_per_kg"] * 0.08, places=4)

    def test_crespelle_in_grammi(self):
        conn = _mem()
        conn.executescript(MIN_SCHEMA)
        rid = _seed_fondelle(conn, pieces=25, yield_qty=2000, yield_unit="g")
        cost = compute_recipe_cost(conn, rid)
        self.assertAlmostEqual(cost["yield_kg"], 2.0, places=6)
        self.assertAlmostEqual(cost["piece_weight_g"], 80.0, places=4)
        self.assertAlmostEqual(cost["cost_per_kg"], 2.0, places=4)
        self.assertAlmostEqual(cost["cost_per_piece"], 0.16, places=4)

    def test_ricetta_senza_pezzi_invariata(self):
        conn = _mem()
        conn.executescript(MIN_SCHEMA)
        with_pieces = _seed_fondelle(conn, pieces=25)
        without = _seed_fondelle(conn, pieces=None)
        a = compute_recipe_cost(conn, with_pieces)
        b = compute_recipe_cost(conn, without)
        self.assertAlmostEqual(a["cost_per_kg"], b["cost_per_kg"], places=4)
        self.assertAlmostEqual(a["total_cost"], b["total_cost"], places=4)
        self.assertIsNone(b["yield_pieces"])
        self.assertIsNone(b["piece_weight_g"])
        self.assertIsNone(b["cost_per_piece"])

    def test_uso_come_ingrediente_in_pezzi(self):
        conn = _mem()
        conn.executescript(MIN_SCHEMA)
        fondelle = _seed_fondelle(conn)
        prosciutto = conn.execute(
            "INSERT INTO products (last_price) VALUES (10.0)"
        ).lastrowid
        parent = conn.execute(
            """INSERT INTO recipes (name, yield_qty, yield_unit)
               VALUES ('Crespelle al prosciutto', 1.5, 'kg')"""
        ).lastrowid
        conn.execute(
            """INSERT INTO recipe_ingredients
               (recipe_id, kind, sub_recipe_id, name, quantity, unit, sort_order)
               VALUES (?, 'recipe', ?, 'Fondelle per crespelle', 12, 'pz', 0)""",
            (parent, fondelle),
        )
        conn.execute(
            """INSERT INTO recipe_ingredients
               (recipe_id, kind, product_id, name, quantity, unit, sort_order)
               VALUES (?, 'product', ?, 'Prosciutto', 0.2, 'kg', 1)""",
            (parent, prosciutto),
        )
        cost = compute_recipe_cost(conn, parent)
        fondelle_line = cost["ingredients"][0]
        self.assertEqual(fondelle_line["unit"], "pz")
        self.assertEqual(fondelle_line["quantity"], 12)
        self.assertAlmostEqual(fondelle_line["piece_weight_g"], 80.0, places=4)
        self.assertAlmostEqual(fondelle_line["weight_g"], 960.0, places=4)
        # 12 × 0,16 € = 1,92 €, cioè 0,96 kg × 2 €/kg
        self.assertAlmostEqual(fondelle_line["line_cost"], 1.92, places=4)
        self.assertAlmostEqual(fondelle_line["unit_cost"], 0.16, places=4)
        self.assertAlmostEqual(cost["ingredients"][1]["line_cost"], 2.0, places=4)
        self.assertAlmostEqual(cost["total_cost"], 3.92, places=4)

        # Stesso costo se le fondelle entrano a peso (0,96 kg = 12 × 80 g)
        by_weight = conn.execute(
            """INSERT INTO recipes (name, yield_qty, yield_unit)
               VALUES ('Crespelle a peso', 1.5, 'kg')"""
        ).lastrowid
        conn.execute(
            """INSERT INTO recipe_ingredients
               (recipe_id, kind, sub_recipe_id, name, quantity, unit)
               VALUES (?, 'recipe', ?, 'Fondelle per crespelle', 0.96, 'kg')""",
            (by_weight, fondelle),
        )
        weighed = compute_recipe_cost(conn, by_weight)
        self.assertAlmostEqual(weighed["ingredients"][0]["line_cost"], 1.92, places=4)

    def test_pezzi_senza_numero_non_inventa_un_costo(self):
        conn = _mem()
        conn.executescript(MIN_SCHEMA)
        base = _seed_fondelle(conn, pieces=None)
        parent = conn.execute(
            """INSERT INTO recipes (name, yield_qty, yield_unit) VALUES ('Uso errato', 1, 'kg')"""
        ).lastrowid
        conn.execute(
            """INSERT INTO recipe_ingredients
               (recipe_id, kind, sub_recipe_id, name, quantity, unit)
               VALUES (?, 'recipe', ?, 'Fondelle', 12, 'pz')""",
            (parent, base),
        )
        cost = compute_recipe_cost(conn, parent)
        self.assertIsNone(cost["ingredients"][0]["line_cost"])
        self.assertIn("pezzi", cost["ingredients"][0]["note"])
        self.assertIsNone(cost["cost_per_kg"])

    def test_colonna_pezzi_assente_non_rompe_il_calcolo(self):
        conn = _mem()
        conn.executescript(
            MIN_SCHEMA.replace("  yield_pieces REAL,\n", "")
        )
        farina = conn.execute("INSERT INTO products (last_price) VALUES (2)").lastrowid
        rid = conn.execute(
            """INSERT INTO recipes (name, yield_qty, yield_unit)
               VALUES ('Vecchia', 2, 'kg')"""
        ).lastrowid
        conn.execute(
            """INSERT INTO recipe_ingredients
               (recipe_id, kind, product_id, name, quantity, unit)
               VALUES (?, 'product', ?, 'Farina', 2, 'kg')""",
            (rid, farina),
        )
        cost = compute_recipe_cost(conn, rid)
        self.assertAlmostEqual(cost["cost_per_kg"], 2.0, places=4)
        self.assertIsNone(cost["yield_pieces"])
        self.assertIsNone(cost["piece_weight_g"])
        self.assertIsNone(cost["cost_per_piece"])


class MigrationTests(unittest.TestCase):
    def test_migrazione_aggiunge_colonna_senza_perdere_dati(self):
        conn = _mem()
        old = SCHEMA.replace("  yield_pieces REAL,\n", "")
        self.assertNotIn("yield_pieces", old)
        conn.executescript(old)
        conn.execute(
            """INSERT INTO recipes (name, category, yield_qty, yield_unit, notes)
               VALUES ('Lasagne della casa', 'primi', 4.5, 'kg', 'non toccare')"""
        )
        rid = conn.execute("SELECT id FROM recipes").fetchone()["id"]
        conn.execute(
            """INSERT INTO recipe_ingredients
               (recipe_id, kind, name, quantity, unit, sort_order)
               VALUES (?, 'product', 'Pasta', 1.2, 'kg', 0)""",
            (rid,),
        )
        _migrate(conn)
        self.assertIn("yield_pieces", _table_cols(conn, "recipes"))
        row = conn.execute(
            "SELECT name, category, yield_qty, yield_unit, yield_pieces, notes FROM recipes"
        ).fetchone()
        self.assertEqual(row["name"], "Lasagne della casa")
        self.assertEqual(row["category"], "primi")
        self.assertEqual(row["yield_qty"], 4.5)
        self.assertEqual(row["yield_unit"], "kg")
        self.assertIsNone(row["yield_pieces"])
        self.assertEqual(row["notes"], "non toccare")
        ing = conn.execute(
            "SELECT name, quantity, unit FROM recipe_ingredients"
        ).fetchone()
        self.assertEqual(ing["name"], "Pasta")
        self.assertEqual(ing["quantity"], 1.2)
        self.assertEqual(ing["unit"], "kg")
        # Una seconda esecuzione non deve fallire né riscrivere i dati.
        _migrate(conn)
        again = conn.execute("SELECT name, yield_qty, yield_pieces FROM recipes").fetchone()
        self.assertEqual(again["name"], "Lasagne della casa")
        self.assertEqual(again["yield_qty"], 4.5)
        self.assertIsNone(again["yield_pieces"])


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        login = cls.client.post(
            "/login",
            data={"username": "admin", "password": "secret123"},
            follow_redirects=False,
        )
        assert login.status_code == 302, login.text
        with db() as conn:
            sid = conn.execute(
                "INSERT INTO suppliers (name) VALUES ('MOLINO TEST')"
            ).lastrowid
            cls.farina = conn.execute(
                """INSERT INTO products (supplier_id, description, unit, last_price, visible)
                   VALUES (?, 'Farina 00', 'kg', 2, 1)""",
                (sid,),
            ).lastrowid
            cls.latte = conn.execute(
                """INSERT INTO products (supplier_id, description, unit, last_price, visible)
                   VALUES (?, 'Latte intero', 'kg', 2, 1)""",
                (sid,),
            ).lastrowid
        created = cls.client.post(
            "/api/users",
            json={
                "username": "dipendente",
                "full_name": "Dipendente Test",
                "password": "secret123",
                "role": "dipendente",
            },
        )
        assert created.status_code == 200, created.text
        cls.employee = TestClient(app)
        cls.employee.__enter__()
        elogin = cls.employee.post(
            "/login",
            data={"username": "dipendente", "password": "secret123"},
            follow_redirects=False,
        )
        assert elogin.status_code == 302, elogin.text

    @classmethod
    def tearDownClass(cls):
        cls.employee.__exit__(None, None, None)
        cls.client.__exit__(None, None, None)

    def _ingredients(self):
        return [
            {
                "kind": "product",
                "product_id": self.farina,
                "name": "Farina 00",
                "quantity": 1,
                "unit": "kg",
            },
            {
                "kind": "product",
                "product_id": self.latte,
                "name": "Latte intero",
                "quantity": 1,
                "unit": "kg",
            },
        ]

    def test_api_crespelle_e_uso_in_pezzi(self):
        created = self.client.post(
            "/api/recipes",
            json={
                "name": "Fondelle per crespelle",
                "category": "basi",
                "yield_qty": 2,
                "yield_unit": "kg",
                "yield_pieces": 25,
                "ingredients": self._ingredients(),
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        rid = created.json()["data"]["id"]

        got = self.client.get(f"/api/recipes/{rid}")
        self.assertEqual(got.status_code, 200, got.text)
        data = got.json()["data"]
        cost = data["cost"]
        self.assertEqual(data["yield_pieces"], 25)
        self.assertAlmostEqual(cost["piece_weight_g"], 80.0, places=4)
        self.assertAlmostEqual(cost["cost_per_kg"], 2.0, places=4)
        self.assertAlmostEqual(cost["cost_per_piece"], 0.16, places=4)
        self.assertAlmostEqual(cost["total_cost"], 4.0, places=4)

        listed = self.client.get("/api/recipes").json()["data"]["recipes"]
        row = next(r for r in listed if r["id"] == rid)
        self.assertEqual(row["yield_pieces"], 25)

        preview = self.client.post(
            "/api/recipes/preview-cost",
            json={
                "yield_qty": 2,
                "yield_unit": "kg",
                "yield_pieces": 25,
                "ingredients": self._ingredients(),
            },
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        prev = preview.json()["data"]
        self.assertAlmostEqual(prev["piece_weight_g"], 80.0, places=4)
        self.assertAlmostEqual(prev["cost_per_piece"], 0.16, places=4)

        parent = self.client.post(
            "/api/recipes",
            json={
                "name": "Crespelle al prosciutto",
                "yield_qty": 1.5,
                "yield_unit": "kg",
                "ingredients": [
                    {
                        "kind": "recipe",
                        "sub_recipe_id": rid,
                        "name": "Fondelle per crespelle",
                        "quantity": 12,
                        "unit": "Pezzi",
                    }
                ],
            },
        )
        self.assertEqual(parent.status_code, 200, parent.text)
        pid = parent.json()["data"]["id"]
        parent_cost = self.client.get(f"/api/recipes/{pid}").json()["data"]["cost"]
        line = parent_cost["ingredients"][0]
        self.assertEqual(line["unit"], "pz")
        self.assertEqual(line["quantity"], 12)
        self.assertAlmostEqual(line["piece_weight_g"], 80.0, places=4)
        self.assertAlmostEqual(line["weight_g"], 960.0, places=4)
        self.assertAlmostEqual(line["line_cost"], 1.92, places=4)

        # Il dipendente vede i pezzi e il peso, non i prezzi.
        hidden = self.employee.get(f"/api/recipes/{rid}")
        self.assertEqual(hidden.status_code, 200, hidden.text)
        ecost = hidden.json()["data"]["cost"]
        for key in ("cost_per_kg", "cost_per_piece", "total_cost", "sell_price", "margin"):
            self.assertNotIn(key, ecost)
        self.assertAlmostEqual(ecost["piece_weight_g"], 80.0, places=4)
        self.assertEqual(ecost["yield_pieces"], 25)
        eparent = self.employee.get(f"/api/recipes/{pid}").json()["data"]["cost"]
        self.assertNotIn("line_cost", eparent["ingredients"][0])
        self.assertNotIn("unit_cost", eparent["ingredients"][0])
        self.assertAlmostEqual(eparent["ingredients"][0]["weight_g"], 960.0, places=4)

        eprev = self.employee.post(
            "/api/recipes/preview-cost",
            json={"yield_qty": 2, "yield_unit": "kg", "yield_pieces": 25, "ingredients": []},
        )
        self.assertEqual(eprev.status_code, 200, eprev.text)
        ebody = eprev.json()["data"]
        self.assertNotIn("cost_per_kg", ebody)
        self.assertNotIn("cost_per_piece", ebody)
        self.assertNotIn("total_cost", ebody)
        self.assertAlmostEqual(ebody["piece_weight_g"], 80.0, places=4)

        # Non si possono togliere i pezzi se qualcuno le usa a pezzi.
        blocked = self.client.put(
            f"/api/recipes/{rid}",
            json={"yield_pieces": None, "yield_qty": 2, "yield_unit": "kg"},
        )
        self.assertEqual(blocked.status_code, 400, blocked.text)
        self.assertIn("pezzi", blocked.json()["error"])
        still = self.client.get(f"/api/recipes/{rid}").json()["data"]
        self.assertEqual(still["yield_pieces"], 25)

    def test_ricetta_senza_pezzi_e_rifiuto_uso_a_pezzi(self):
        created = self.client.post(
            "/api/recipes",
            json={
                "name": "Ragù della casa",
                "yield_qty": 5,
                "yield_unit": "kg",
                "ingredients": self._ingredients(),
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        rid = created.json()["data"]["id"]
        got = self.client.get(f"/api/recipes/{rid}").json()["data"]
        self.assertIsNone(got["yield_pieces"])
        self.assertIsNone(got["cost"]["piece_weight_g"])
        self.assertIsNone(got["cost"]["cost_per_piece"])
        self.assertAlmostEqual(got["cost"]["cost_per_kg"], 0.8, places=4)

        kept = self.client.put(
            f"/api/recipes/{rid}",
            json={"name": "Ragù della casa", "yield_qty": 5, "yield_unit": "kg"},
        )
        self.assertEqual(kept.status_code, 200, kept.text)
        self.assertIsNone(self.client.get(f"/api/recipes/{rid}").json()["data"]["yield_pieces"])

        bad = self.client.post(
            "/api/recipes",
            json={
                "name": "Piatto con ragù a pezzi",
                "yield_qty": 1,
                "yield_unit": "kg",
                "ingredients": [
                    {
                        "kind": "recipe",
                        "sub_recipe_id": rid,
                        "name": "Ragù della casa",
                        "quantity": 3,
                        "unit": "pz",
                    }
                ],
            },
        )
        self.assertEqual(bad.status_code, 400, bad.text)
        self.assertIn("pezzi", bad.json()["error"])

        product_as_pz = self.client.post(
            "/api/recipes",
            json={
                "name": "Farina a pezzi",
                "yield_qty": 1,
                "yield_unit": "kg",
                "ingredients": [
                    {
                        "kind": "product",
                        "product_id": self.farina,
                        "name": "Farina 00",
                        "quantity": 4,
                        "unit": "pz",
                    }
                ],
            },
        )
        self.assertEqual(product_as_pz.status_code, 400, product_as_pz.text)

    def test_aggiornamento_mantiene_i_pezzi_se_il_campo_non_arriva(self):
        created = self.client.post(
            "/api/recipes",
            json={
                "name": "Biscotti",
                "yield_qty": 1,
                "yield_unit": "kg",
                "yield_pieces": 40,
                "ingredients": self._ingredients(),
            },
        )
        rid = created.json()["data"]["id"]
        renamed = self.client.put(
            f"/api/recipes/{rid}",
            json={"name": "Biscotti della casa"},
        )
        self.assertEqual(renamed.status_code, 200, renamed.text)
        data = self.client.get(f"/api/recipes/{rid}").json()["data"]
        self.assertEqual(data["name"], "Biscotti della casa")
        self.assertEqual(data["yield_pieces"], 40)
        self.assertEqual(data["yield_qty"], 1)


if __name__ == "__main__":
    unittest.main()
