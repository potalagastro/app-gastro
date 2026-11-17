"""Ricevitore webhook Ordinia -> avviso email a Gianluca per ogni ordine nuovo.

Flusso:
  Ordinia POST /webhooks/ordinia  ->  risposta 200 immediata (Ordinia vuole 2xx entro 10 s,
  non segue redirect, spegne l'endpoint dopo 15 errori di fila)
  -> in background: dedup sull'id evento (e sull'id ordine per order.created),
     rilettura dell'ordine da GET /orders/{id} (ORDINIA_API_KEY),
     invio dell'avviso tramite l'Apps Script del foglio «Ordini WhatsApp Pota»
     (POST ?notifica=ordinia con segreto condiviso, redirect seguito lato server; MailApp).

Variabili d'ambiente:
  ORDINIA_API_KEY         chiave API Ordinia (orders:read)
  ORDINIA_NOTIFY_URL      URL /exec dell'Apps Script
  ORDINIA_NOTIFY_SECRET   segreto condiviso con lo script (Script Property ORDINIA_NOTIFY_SECRET)
  ORDINIA_WEBHOOK_SECRET  opzionale, whsec_... : se presente si verifica Ordinia-Signature
  ORDINIA_API_BASE        opzionale, default https://api.ordinia.net/api/public/v1

Nomi, note e righe dell'ordine sono testo non fidato: finiscono solo come testo semplice nella mail.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse

from .db import db

log = logging.getLogger("ordinia_webhook")
router = APIRouter()
ROME = ZoneInfo("Europe/Rome")
GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
# Tentativi di invio (minuti dal primo errore); dopo ~24 h la notifica resta "failed".
RETRY_MINUTES = [1, 5, 15, 30, 60, 120, 240, 480, 720, 1440]

_TABLE = """CREATE TABLE IF NOT EXISTS ordinia_events (
  event_id TEXT PRIMARY KEY,
  type TEXT,
  order_id TEXT,
  is_test INTEGER NOT NULL DEFAULT 0,
  received_at TEXT NOT NULL,
  status TEXT NOT NULL,          -- received | sent | pending | failed | ignored
  attempts INTEGER NOT NULL DEFAULT 0,
  next_try_at REAL,
  subject TEXT,
  body TEXT,
  detail TEXT
)"""
_table_ready = False


def _ensure_table():
    global _table_ready
    if _table_ready:
        return
    with db() as conn:
        conn.execute(_TABLE)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ordinia_events_order ON ordinia_events(order_id)")
    _table_ready = True


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _verify_signature(secret: str, header: str | None, raw: bytes) -> bool:
    if not header:
        return False
    try:
        parts = dict(kv.split("=", 1) for kv in header.split(","))
        t = int(parts.get("t", ""))
        v1 = parts.get("v1", "")
    except Exception:
        return False
    if len(v1) != 64 or abs(time.time() - t) > 300:
        return False
    expected = hmac.new(secret.encode(), f"{t}.".encode() + raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1.lower())


def _clean(value, limit: int = 500) -> str:
    """Testo non fidato -> una riga di testo semplice."""
    s = "" if value is None else str(value)
    s = " ".join(s.split())
    return s[:limit]


def _fmt_time(iso: str | None) -> tuple[str, str]:
    """(lungo, breve) in ora di Roma, es. ('domenica 15/11/2026 alle 11:30', '15/11 11:30')."""
    if not iso:
        return ("non indicato", "orario n.d.")
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ROME)
    except Exception:
        return (_clean(iso, 40), _clean(iso, 40))
    return (f"{GIORNI[dt.weekday()]} {dt:%d/%m/%Y} alle {dt:%H:%M}", f"{dt:%d/%m %H:%M}")


def _euro(cents) -> str:
    try:
        return f"{int(cents) / 100:.2f}".replace(".", ",") + " €"
    except Exception:
        return ""


def build_message(order: dict, is_test: bool = False) -> tuple[str, str]:
    name = _clean(order.get("customerName"), 120) or "cliente senza nome"
    phone = _clean(order.get("customerPhone"), 40) or "non indicato"
    long_t, short_t = _fmt_time(order.get("fulfillmentTime"))
    otype = order.get("orderType")
    tipo = {"pickup": "Ritiro in negozio", "delivery": "Consegna"}.get(otype, _clean(otype, 40) or "non indicato")
    verbo = "consegna" if otype == "delivery" else "ritiro"
    subject = f"Nuovo ordine Ordinia – {name} – {verbo} {short_t}"
    if is_test:
        subject = "[TEST] " + subject

    righe = []
    if is_test:
        righe += ["*** PROVA DEL COLLEGAMENTO: questo non è un ordine nuovo. ***", ""]
    righe += [
        f"Nuovo ordine su Ordinia (rif. {_clean(order.get('ref'), 20)})",
        "",
        f"Cliente: {name}",
        f"Telefono: {phone}",
        f"Tipo: {tipo}",
        f"Giorno e ora di {verbo}: {long_t}",
    ]
    if order.get("kind") == "prenotazione":
        righe.append("Tipo ordine: prenotazione (pre-ordine)")
    addr = order.get("deliveryAddress")
    if otype == "delivery" and addr:
        if isinstance(addr, dict):
            addr = ", ".join(_clean(v, 120) for v in addr.values() if v)
        righe.append(f"Indirizzo: {_clean(addr, 300)}")
    righe += ["", "Piatti:"]
    for ln in order.get("lines") or []:
        q = ln.get("quantity")
        q = int(q) if isinstance(q, (int, float)) and float(q).is_integer() else q
        nome = _clean(ln.get("name"), 150)
        var = _clean(ln.get("variantName"), 80)
        if var and var.lower() != "standard":
            nome += f" ({var})"
        r = f"- {q} x {nome}"
        if ln.get("freeText"):
            r += f" — nota: {_clean(ln.get('freeText'), 300)}"
        righe.append(r)
    if not order.get("lines"):
        righe.append("- (nessun piatto nell'ordine)")
    righe += ["", f"Nota del cliente: {_clean(order.get('note'), 1000) or 'nessuna'}"]
    if order.get("totalCents"):
        righe.append(f"Totale: {_euro(order.get('totalCents'))}")
    righe += ["", "Avviso automatico: l'ordine non è stato modificato e al cliente non è stato scritto nulla."]
    return subject, "\n".join(righe)


async def _fetch_order(order_id: str) -> dict:
    key = _env("ORDINIA_API_KEY")
    if not key:
        raise RuntimeError("ORDINIA_API_KEY mancante")
    base = _env("ORDINIA_API_BASE", "https://api.ordinia.net/api/public/v1").rstrip("/")
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{base}/orders/{order_id}", headers={"Authorization": f"Bearer {key}"})
    if r.status_code != 200:
        raise RuntimeError(f"GET order {r.status_code}")
    return r.json()


async def _deliver(subject: str, body: str) -> tuple[bool, str]:
    url = _env("ORDINIA_NOTIFY_URL")
    secret = _env("ORDINIA_NOTIFY_SECRET")
    if not url or not secret:
        return False, "relay non configurato"
    sep = "&" if "?" in url else "?"
    try:
        async with httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
            r = await client.post(
                f"{url}{sep}notifica=ordinia",
                content=json.dumps({"secret": secret, "subject": subject, "text": body}),
                headers={"Content-Type": "application/json"},
            )
    except Exception as exc:  # rete / timeout
        return False, f"relay errore {type(exc).__name__}"
    text = (r.text or "").strip()
    if r.status_code == 200 and text.startswith("ORDINIA_OK"):
        return True, "inviata"
    return False, f"relay risposta {r.status_code}: {text[:120]}"


def _update(event_id: str, **fields):
    cols = ", ".join(f"{k}=?" for k in fields)
    with db() as conn:
        conn.execute(f"UPDATE ordinia_events SET {cols} WHERE event_id=?", (*fields.values(), event_id))


async def _send_or_schedule(event_id: str, subject: str, body: str, attempts: int):
    ok, detail = await _deliver(subject, body)
    attempts += 1
    if ok:
        _update(event_id, status="sent", attempts=attempts, next_try_at=None, detail=detail)
    elif attempts > len(RETRY_MINUTES):
        _update(event_id, status="failed", attempts=attempts, next_try_at=None, detail=detail)
        log.error("Ordinia notice %s failed: %s", event_id, detail)
    else:
        nxt = time.time() + 60 * RETRY_MINUTES[attempts - 1]
        _update(event_id, status="pending", attempts=attempts, next_try_at=nxt, detail=detail)
        log.warning("Ordinia notice %s pending: %s", event_id, detail)


async def _process(event_id: str, order_id: str, is_test: bool):
    try:
        order = await _fetch_order(order_id)
        subject, body = build_message(order, is_test=is_test)
    except Exception as exc:
        # Ordine non leggibile ora: si riprova più tardi con lo stesso flusso
        _update(event_id, status="pending", attempts=1, next_try_at=time.time() + 300,
                detail=f"lettura ordine: {exc}"[:300])
        return
    _update(event_id, subject=subject, body=body)
    await _send_or_schedule(event_id, subject, body, attempts=0)


async def retry_pending(force: bool = False) -> int:
    _ensure_table()
    now = time.time()
    with db() as conn:
        rows = conn.execute(
            "SELECT event_id, order_id, is_test, subject, body, attempts, next_try_at FROM ordinia_events "
            "WHERE status IN ('pending','received')"
        ).fetchall()
    n = 0
    for r in rows:
        if not force and r["next_try_at"] and r["next_try_at"] > now:
            continue
        n += 1
        if r["subject"] and r["body"]:
            await _send_or_schedule(r["event_id"], r["subject"], r["body"], r["attempts"] or 0)
        else:
            await _process(r["event_id"], r["order_id"], bool(r["is_test"]))
    return n


async def _retry_loop():
    while True:
        await asyncio.sleep(60)
        try:
            await retry_pending()
        except Exception:
            log.exception("retry loop")


def start_background():
    _ensure_table()
    asyncio.get_event_loop().create_task(_retry_loop())


def _is_test(request: Request) -> bool:
    secret = _env("ORDINIA_NOTIFY_SECRET")
    given = request.headers.get("x-pota-test") or ""
    return bool(secret) and hmac.compare_digest(given, secret)


@router.post("/webhooks/ordinia")
async def ordinia_webhook(request: Request, background: BackgroundTasks):
    raw = await request.body()
    whsec = _env("ORDINIA_WEBHOOK_SECRET")
    if whsec and not _verify_signature(whsec, request.headers.get("ordinia-signature"), raw):
        return JSONResponse({"ok": False, "error": "firma non valida"}, status_code=401)
    try:
        evt = json.loads(raw or b"{}")
        event_id = str(evt.get("id") or request.headers.get("ordinia-event-id") or "")[:100]
        etype = str(evt.get("type") or "")[:60]
        data = evt.get("data") or {}
    except Exception:
        # Corpo illeggibile: 200 lo stesso, così Ordinia non spegne l'endpoint
        return {"ok": True, "ignored": "corpo non valido"}
    if not event_id:
        return {"ok": True, "ignored": "id evento mancante"}

    _ensure_table()
    is_test = _is_test(request)
    order_id = str(data.get("orderId") or "")[:100]
    received = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db() as conn:
        if conn.execute("SELECT 1 FROM ordinia_events WHERE event_id=?", (event_id,)).fetchone():
            return {"ok": True, "duplicate": True}
        status = "received"
        detail = None
        if etype != "order.created" or not order_id:
            status, detail = "ignored", "evento non gestito"
        elif not is_test and conn.execute(
            "SELECT 1 FROM ordinia_events WHERE order_id=? AND type='order.created' AND is_test=0 "
            "AND status!='ignored'", (order_id,)
        ).fetchone():
            status, detail = "ignored", "ordine già avvisato"
        cur = conn.execute(
            "INSERT OR IGNORE INTO ordinia_events (event_id, type, order_id, is_test, received_at, status, detail) "
            "VALUES (?,?,?,?,?,?,?)",
            (event_id, etype, order_id, int(is_test), received, status, detail),
        )
        if cur.rowcount == 0:
            return {"ok": True, "duplicate": True}
    if status == "received":
        background.add_task(_process, event_id, order_id, is_test)
        return {"ok": True, "accepted": True}
    return {"ok": True, "ignored": detail}


@router.post("/webhooks/ordinia/retry")
async def ordinia_retry(request: Request):
    """Rinvia subito le notifiche in sospeso (solo con l'header X-Pota-Test = segreto)."""
    if not _is_test(request):
        return JSONResponse({"ok": False}, status_code=403)
    n = await retry_pending(force=True)
    return {"ok": True, "retried": n}


@router.get("/webhooks/ordinia/status")
async def ordinia_status(request: Request, limit: int = 10):
    """Ultimi eventi (senza testo dei clienti), solo con l'header X-Pota-Test = segreto."""
    if not _is_test(request):
        return JSONResponse({"ok": False}, status_code=403)
    _ensure_table()
    with db() as conn:
        rows = conn.execute(
            "SELECT event_id, type, order_id, is_test, received_at, status, attempts, detail "
            "FROM ordinia_events ORDER BY received_at DESC LIMIT ?", (min(limit, 50),)
        ).fetchall()
    return {"ok": True, "events": [dict(r) for r in rows]}
