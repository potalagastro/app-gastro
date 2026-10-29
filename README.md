# POTA LA GASTRO — Ordini & Food Cost (MVP)

Web app mobile-first in italiano per Gianluca Lonati (Prevalle BS).  
Un'unica app, stesso login, due sezioni: **Ordini** e **Food Cost**.

## Avvio sul box

```bash
cd /workspace/app_gastro
# prima volta:
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
PYTHONPATH=. .venv/bin/python scripts/bootstrap.py   # crea DB, admin, importa catalogo
./run.sh   # http://127.0.0.1:8080
```

Credenziali iniziali: file `CREDENZIALI_ADMIN.txt` (non versionare / non condividere).

## Cosa fa (MVP)

### Accesso
- Login utente + password (hash bcrypt)
- Ruoli: **admin** (Gianluca) e **dipendente** (creati dall'admin)
- Log attività (audit) di chi crea/modifica

### Ordini
- Catalogo da Google Sheet (CSV in `data/catalogo_import.csv`), solo fornitori **merce**
- Unione anagrafiche: PORTO S.R.L./PORTO SRL → `PORTO SRL`; Prevalcart Snc/Srl → `PREVALCART S.R.L.`
- Inclusi: Prevalcart, Porto, Le Carni di Biondo, Ovocheese — **escluso Santomarco**
- Di default solo articoli acquistati negli ultimi 12 mesi; admin può mostrare/nascondere
- Pulsante admin **Reimporta catalogo** (+ script `scripts/reimport_catalog.sh`)
- Ricerca unica parziale; ultimo prezzo **solo admin**
- Bozze per fornitore, preferiti, ripeti ultimo ordine
- Config fornitore (giorni, orario limite, minimo, WhatsApp rappresentante)
- **Solo admin** invia: genera link `wa.me` con messaggio pronto; marca inviato
- Storico + controllo arrivo (sì/no/parziale per riga)

### Food Cost
- Ricette con ingredienti crudi + peso totale → costo/kg automatico (niente scarto)
- Ingredienti da catalogo o da sottoricette (cascata)
- Extra: condimenti, vaschetta, energia/manodopera
- Prezzo vendita, margine, % food cost con semaforo (&lt;30 verde, 30–35 giallo, &gt;35 rosso)
- Scala “voglio produrre X kg” + stampa scheda **senza prezzi**
- Contatore ricette nuove settimana vs obiettivo 7
- Prezzi/costi/margini **solo admin**

## Stack
Python 3 + FastAPI + SQLite + HTML/CSS/JS mobile-first.

## Aggiornare il catalogo
1. Esporta/aggiorna `data/catalogo_import.csv` dal foglio Google (tab Catalogo)
2. Admin → “Reimporta catalogo” oppure `./scripts/reimport_catalog.sh`

## Screenshot
Cartella `screens/` (login, ricerca zuc, bozza ordine, food cost lasagna).
