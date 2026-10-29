#!/usr/bin/env python3
"""Init DB, optionally import catalog, create admin (+ optional employee) from env."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.auth import hash_password
from app.catalog_import import CSV_PATH, import_catalog
from app.db import db, init_db


def main():
    init_db()

    admin_user = (os.environ.get("ADMIN_USERNAME") or "gianluca").strip()
    admin_pass = os.environ.get("ADMIN_PASSWORD") or ""
    admin_name = os.environ.get("ADMIN_FULL_NAME") or "Gianluca Lonati"
    emp_user = (os.environ.get("EMP_USERNAME") or "dipendente").strip()
    emp_pass = os.environ.get("EMP_PASSWORD") or ""
    emp_name = os.environ.get("EMP_FULL_NAME") or "Dipendente Demo"

    with db() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE username=?", (admin_user,)
        ).fetchone()
        if existing:
            print(f"Admin '{admin_user}' già presente, salto creazione utenti")
        else:
            if not admin_pass:
                print(
                    "ADMIN_PASSWORD non impostata e admin assente: "
                    "nessun utente creato (impostala e riavvia)",
                    file=sys.stderr,
                )
            else:
                conn.execute(
                    """INSERT INTO users (username, password_hash, full_name, role)
                       VALUES (?,?,?,?)""",
                    (admin_user, hash_password(admin_pass), admin_name, "admin"),
                )
                if emp_pass:
                    conn.execute(
                        """INSERT INTO users (username, password_hash, full_name, role, created_by)
                           VALUES (?,?,?,?,1)""",
                        (emp_user, hash_password(emp_pass), emp_name, "dipendente"),
                    )
                print(f"Utenti creati (admin={admin_user})")

    # Import catalog once when CSV is available and catalog empty
    with db() as conn:
        nprod = conn.execute("SELECT COUNT(*) c FROM products").fetchone()["c"]
    if nprod == 0 and CSV_PATH.exists():
        stats = import_catalog(csv_path=CSV_PATH, username="bootstrap")
        print("Import catalog:", stats)
        # Optional demo WhatsApp seeds (harmless if suppliers missing)
        with db() as conn:
            conn.execute(
                """UPDATE suppliers SET rep_name=?, rep_whatsapp=?, delivery_days=?, order_deadline=?, min_order=?
                   WHERE name=?""",
                ("Marco (demo)", "393331234567", "Mar, Gio", "11:00", "€50", "EBRANATI AUGUSTO E CLAUDIO SNC"),
            )
            conn.execute(
                """UPDATE suppliers SET rep_name=?, rep_whatsapp=?, delivery_days=?, order_deadline=?
                   WHERE name=?""",
                ("Rapp. MARR", "393339998877", "Lun-Ven", "10:30", "MARR SPA"),
            )
        print("Demo WhatsApp configurato (se fornitori presenti)")
    elif nprod == 0:
        print(f"Catalogo non importato: CSV assente ({CSV_PATH})")
    else:
        print(f"Catalogo già presente ({nprod} prodotti), skip import")


if __name__ == "__main__":
    main()
