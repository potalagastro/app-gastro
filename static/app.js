/* POTA LA GASTRO frontend */
const IS_ADMIN = !!(window.PLG && window.PLG.isAdmin);
const USER_ID = (window.PLG && window.PLG.userId) || null;

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  const data = await res.json().catch(() => ({ ok: false, error: "Risposta non valida" }));
  if (!res.ok || data.ok === false) throw new Error(data.error || res.statusText);
  return data.data !== undefined ? data.data : data;
}

function $(sel, root = document) { return root.querySelector(sel); }
function $all(sel, root = document) { return [...root.querySelectorAll(sel)]; }
function fmtDate(d) {
  if (!d) return "—";
  const s = String(d).slice(0, 10);
  const [y, m, day] = s.split("-");
  return day && m ? `${day}/${m}/${y}` : s;
}
function fmtPrice(p) {
  if (p == null || p === "") return "—";
  return Number(p).toLocaleString("it-IT", { style: "currency", currency: "EUR" });
}
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Tabs
$all(".tab").forEach(btn => {
  btn.addEventListener("click", () => {
    $all(".tab").forEach(b => b.classList.remove("active"));
    $all(".page").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    $(`#page-${btn.dataset.tab}`).classList.add("active");
    if (btn.dataset.tab === "ordini") { showOrdiniHome(); loadOrders(); }
    if (btn.dataset.tab === "foodcost") loadRecipes();
    if (btn.dataset.tab === "admin") loadAdmin();
  });
});

// ─── ORDINI ─────────────────────────────────────────────
let suppliers = [];
let currentOrderId = null;
let currentSupplierId = null;
let searchTimer = null;
let qtyBusy = false;
let draftQty = {}; // product_id -> qty
let draftLines = []; // full lines for fab count
let catalogProducts = []; // current supplier catalog

function showOrdiniHome() {
  $("#ordini-home").classList.remove("hidden");
  $("#supplier-panel").classList.add("hidden");
  $("#order-detail").classList.add("hidden");
  $("#draft-fab").classList.add("hidden");
  currentSupplierId = null;
  $("#search-q").value = "";
  $("#search-q").placeholder = "Cerca prodotto (es. zuc)…";
  delete $("#search-q").dataset.sid;
  $("#search-results").classList.add("hidden");
}

function updateFab() {
  const fab = $("#draft-fab");
  const n = Object.values(draftQty).reduce((a, b) => a + (Number(b) > 0 ? 1 : 0), 0);
  const totalQty = Object.values(draftQty).reduce((a, b) => a + Number(b || 0), 0);
  const onSupplier = !$("#supplier-panel").classList.contains("hidden");
  if (onSupplier && n > 0) {
    fab.classList.remove("hidden");
    $("#btn-fab-draft").textContent = `Vedi bozza (${n} art. · q.tà ${fmtQty(totalQty)})`;
  } else {
    fab.classList.add("hidden");
  }
}

function fmtQty(q) {
  const n = Number(q);
  if (!Number.isFinite(n)) return "0";
  return Number.isInteger(n) ? String(n) : n.toFixed(2).replace(/\.?0+$/, "");
}

async function loadSuppliers() {
  suppliers = await api("/api/suppliers");
  const wrap = $("#supplier-btns");
  if (!suppliers.length) {
    wrap.innerHTML = `<div class="empty">Nessun fornitore</div>`;
    return;
  }
  wrap.innerHTML = suppliers.map(s => `
    <button type="button" class="sup-btn" data-sid="${s.id}">
      <div>
        <div class="sup-name">${esc(s.name)}</div>
        <div class="sup-meta">${s.n_visible || s.n_products || "?"} prodotti
          ${s.delivery_days ? " · consegna " + esc(s.delivery_days) : ""}
        </div>
      </div>
      <span class="sup-arrow">›</span>
    </button>`).join("");
  wrap.querySelectorAll(".sup-btn").forEach(ch => {
    ch.addEventListener("click", () => openSupplier(Number(ch.dataset.sid)));
  });
}

async function ensureDraft(sid) {
  const o = await api("/api/orders", { method: "POST", body: JSON.stringify({ supplier_id: sid }) });
  currentOrderId = o.id;
  const full = await api(`/api/orders/${o.id}`);
  draftQty = {};
  draftLines = full.lines || [];
  for (const l of draftLines) {
    if (l.product_id) draftQty[l.product_id] = Number(l.quantity) || 0;
  }
  return full;
}

async function openSupplier(sid) {
  const s = suppliers.find(x => x.id === sid) || await api(`/api/suppliers/${sid}`);
  currentSupplierId = sid;
  $("#ordini-home").classList.add("hidden");
  $("#order-detail").classList.add("hidden");
  const panel = $("#supplier-panel");
  panel.classList.remove("hidden");
  panel.innerHTML = `<div class="card"><div class="empty">Caricamento prodotti…</div></div>`;

  await ensureDraft(sid);
  catalogProducts = await api(`/api/suppliers/${sid}/products`);

  panel.innerHTML = `
    <div class="card sup-panel-head">
      <div class="row-between">
        <button class="btn btn-sm btn-ghost" id="btn-back-sup">← Fornitori</button>
        ${IS_ADMIN ? `<button class="btn btn-sm btn-ghost" id="btn-cfg-sup">Config</button>` : ""}
      </div>
      <h2 style="margin-top:10px">${esc(s.name)}</h2>
      <div class="result-meta">
        ${esc(s.delivery_days || "—")} · limite ${esc(s.order_deadline || "—")} · min ${esc(s.min_order || "—")}
      </div>
      <div class="card search-bar" style="margin:12px 0 0;box-shadow:none">
        <span class="ico">🔍</span>
        <input id="sup-filter" type="search" placeholder="Filtra prodotti…" autocomplete="off"/>
      </div>
    </div>
    <div class="card prod-list-card">
      <p class="filter-hint" id="prod-count"></p>
      <div id="prod-list"></div>
    </div>
    ${IS_ADMIN ? `<div id="sup-cfg-box" class="card hidden"></div>` : ""}
  `;

  $("#btn-back-sup").onclick = () => { showOrdiniHome(); loadOrders(); };
  $("#sup-filter").oninput = () => renderProductList($("#sup-filter").value.trim());
  renderProductList("");
  updateFab();

  if (IS_ADMIN && $("#btn-cfg-sup")) {
    $("#btn-cfg-sup").onclick = () => {
      const box = $("#sup-cfg-box");
      box.classList.toggle("hidden");
      if (!box.classList.contains("hidden")) {
        box.innerHTML = `
          <h3>Configura fornitore</h3>
          <form id="sup-cfg" class="stack">
            <label>Giorni consegna <input name="delivery_days" value="${esc(s.delivery_days || "")}"/></label>
            <label>Orario limite <input name="order_deadline" value="${esc(s.order_deadline || "")}"/></label>
            <label>Ordine minimo <input name="min_order" value="${esc(s.min_order || "")}"/></label>
            <label>Nome rappresentante <input name="rep_name" value="${esc(s.rep_name || "")}"/></label>
            <label>WhatsApp (es. 393331234567) <input name="rep_whatsapp" value="${esc(s.rep_whatsapp || "")}"/></label>
            <button class="btn btn-secondary" type="submit">Salva</button>
          </form>`;
        $("#sup-cfg").onsubmit = async (e) => {
          e.preventDefault();
          const fd = new FormData(e.target);
          await api(`/api/suppliers/${sid}`, { method: "PUT", body: JSON.stringify(Object.fromEntries(fd.entries())) });
          await loadSuppliers();
          alert("Configurazione salvata");
          openSupplier(sid);
        };
      }
    };
  }
}

function renderProductList(filter) {
  const q = (filter || "").toLowerCase();
  const rows = !q ? catalogProducts : catalogProducts.filter(p =>
    (p.description || "").toLowerCase().includes(q) || (p.code || "").toLowerCase().includes(q)
  );
  $("#prod-count").textContent = `${rows.length} prodotti (A–Z)` + (q ? ` · filtro “${filter}”` : "");
  const box = $("#prod-list");
  if (!rows.length) {
    box.innerHTML = `<div class="empty">Nessun prodotto</div>`;
    return;
  }
  box.innerHTML = rows.map(p => {
    const qty = Number(draftQty[p.id] || 0);
    return `
      <div class="prod-row" data-pid="${p.id}">
        <div class="prod-info">
          <div class="prod-name">${esc(p.description)}</div>
          <div class="prod-meta">${esc(p.unit || "—")}${IS_ADMIN && p.effective_price != null ? " · " + fmtPrice(p.effective_price) : ""}${p.favorited ? " · ★" : ""}</div>
        </div>
        <div class="stepper">
          <button type="button" class="step-btn" data-act="-" data-pid="${p.id}" ${qty <= 0 ? "disabled" : ""}>−</button>
          <span class="step-qty ${qty <= 0 ? "zero" : ""}" data-qty-label="${p.id}">${fmtQty(qty)}</span>
          <button type="button" class="step-btn plus" data-act="+" data-pid="${p.id}">+</button>
        </div>
      </div>`;
  }).join("");
  box.querySelectorAll(".step-btn").forEach(b => {
    b.onclick = () => bumpQty(Number(b.dataset.pid), b.dataset.act === "+" ? 1 : -1);
  });
}

async function bumpQty(pid, delta) {
  if (qtyBusy || !currentOrderId) return;
  const cur = Number(draftQty[pid] || 0);
  const next = Math.max(0, cur + delta);
  if (next === cur) return;
  qtyBusy = true;
  try {
    await api(`/api/orders/${currentOrderId}/lines`, {
      method: "POST",
      body: JSON.stringify({ product_id: pid, quantity: next, set: true }),
    });
    draftQty[pid] = next;
    const label = document.querySelector(`[data-qty-label="${pid}"]`);
    if (label) {
      label.textContent = fmtQty(next);
      label.classList.toggle("zero", next <= 0);
    }
    const minus = document.querySelector(`.step-btn[data-act="-"][data-pid="${pid}"]`);
    if (minus) minus.disabled = next <= 0;
    updateFab();
  } catch (e) {
    alert(e.message);
  } finally {
    qtyBusy = false;
  }
}

$("#btn-fab-draft").onclick = () => {
  if (currentOrderId) openOrder(currentOrderId);
};

async function doSearch() {
  const q = $("#search-q").value.trim();
  const box = $("#search-results");
  // When inside a supplier, use local filter instead
  if (currentSupplierId && !$("#supplier-panel").classList.contains("hidden")) {
    box.classList.add("hidden");
    return;
  }
  if (q.length < 2) { box.classList.add("hidden"); return; }
  const qs = new URLSearchParams({ q });
  const rows = await api(`/api/products/search?${qs}`);
  box.classList.remove("hidden");
  if (!rows.length) { box.innerHTML = `<div class="empty">Nessun risultato per “${esc(q)}”</div>`; return; }
  box.innerHTML = `<div class="muted" style="margin-bottom:8px">${rows.length} risultati — tocca un fornitore oppure +</div>` + rows.map(p => `
    <div class="result-item">
      <div style="flex:1;min-width:0">
        <div class="title">${esc(p.description)}</div>
        <div class="result-meta">${esc(p.supplier_name)} · ${esc(p.unit || "—")}
          ${IS_ADMIN ? ` · <strong>${fmtPrice(p.effective_price)}</strong>` : ""}
        </div>
      </div>
      <button class="btn btn-sm btn-primary" data-pid="${p.id}" data-sid="${p.supplier_id}">+</button>
    </div>`).join("");
  box.querySelectorAll("[data-pid]").forEach(b => {
    b.onclick = async () => {
      try {
        const o = await api("/api/orders", { method: "POST", body: JSON.stringify({ supplier_id: Number(b.dataset.sid) }) });
        await api(`/api/orders/${o.id}/lines`, { method: "POST", body: JSON.stringify({ product_id: Number(b.dataset.pid), quantity: 1 }) });
        await openSupplier(Number(b.dataset.sid));
      } catch (e) { alert(e.message); }
    };
  });
}

$("#search-q").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(doSearch, 220);
});

async function loadOrders() {
  const list = await api("/api/orders");
  const box = $("#orders-list");
  if (!list.length) { box.innerHTML = `<div class="empty">Nessun ordine ancora</div>`; return; }
  box.innerHTML = list.map(o => `
    <div class="list-item" style="cursor:pointer" data-oid="${o.id}">
      <div>
        <div class="title">${esc(o.supplier_name)}</div>
        <div class="result-meta">${o.status === "inviato" ? "Inviato " + fmtDate(o.sent_at) : "Bozza"} · ${o.n_lines} righe · consegna ${fmtDate(o.delivery_date)}</div>
      </div>
      <span class="badge ${o.status === "inviato" ? "badge-amber" : ""}">${o.status}</span>
    </div>`).join("");
  box.querySelectorAll("[data-oid]").forEach(el => {
    el.onclick = () => openOrder(Number(el.dataset.oid));
  });
}

async function openOrder(oid) {
  currentOrderId = oid;
  const o = await api(`/api/orders/${oid}`);
  $("#ordini-home").classList.add("hidden");
  $("#supplier-panel").classList.add("hidden");
  $("#search-results").classList.add("hidden");
  $("#draft-fab").classList.add("hidden");
  const box = $("#order-detail");
  box.classList.remove("hidden");
  const canEdit = o.status === "bozza" || IS_ADMIN;
  const canDelete = o.status === "bozza" && (IS_ADMIN || o.created_by === USER_ID);

  box.innerHTML = `
    <div class="card">
      <div class="row-between">
        <h2 style="margin:0">${esc(o.supplier_name)}</h2>
        <button class="btn btn-sm btn-ghost" id="btn-back-orders">← Indietro</button>
      </div>
      <div class="result-meta" style="margin:8px 0">Stato: <strong>${o.status}</strong>
        ${o.sent_at ? " · inviato " + esc(o.sent_at) : ""}</div>
      <label>Data consegna
        <input type="date" id="ord-delivery" value="${esc((o.delivery_date || "").slice(0,10))}" ${canEdit && o.status==="bozza" ? "" : "disabled"}/>
      </label>
      <div class="row" style="margin:10px 0">
        <button class="btn btn-sm btn-secondary" id="btn-repeat" ${o.status !== "bozza" ? "disabled" : ""}>Ripeti ultimo ordine</button>
        <button class="btn btn-sm btn-ghost" id="btn-save-ord" ${o.status !== "bozza" ? "disabled" : ""}>Salva</button>
        ${o.status === "bozza" ? `<button class="btn btn-sm btn-secondary" id="btn-add-more">+ Aggiungi prodotti</button>` : ""}
      </div>
      <div class="section-title">Righe</div>
      <div id="ord-lines">
        ${o.lines.map(l => `
          <div class="line-item">
            <div style="flex:1">
              <div class="title">${esc(l.description)}</div>
              <div class="result-meta">${esc(l.unit || "")}
                ${o.status === "inviato" ? ` · arrivo:
                  <select data-recv="${l.id}" class="btn-sm">
                    <option value="pending" ${l.received_status==="pending"?"selected":""}>da controllare</option>
                    <option value="si" ${l.received_status==="si"?"selected":""}>sì</option>
                    <option value="parziale" ${l.received_status==="parziale"?"selected":""}>parziale</option>
                    <option value="no" ${l.received_status==="no"?"selected":""}>no</option>
                  </select>` : ""}
              </div>
            </div>
            <input class="qty-input" type="number" min="0" step="any" value="${l.quantity}" data-qty="${l.id}" ${canEdit && o.status==="bozza" ? "" : "disabled"}/>
            ${o.status==="bozza" ? `<button class="btn btn-sm btn-danger" data-del="${l.id}">✕</button>` : ""}
          </div>`).join("") || '<div class="empty">Nessuna riga — aggiungi prodotti dal fornitore</div>'}
      </div>
      ${IS_ADMIN && o.status === "bozza" ? `
        <button class="btn btn-primary btn-block" style="margin-top:12px" id="btn-send">Invia via WhatsApp</button>
        <div id="wa-box" class="hidden" style="margin-top:10px"></div>` : ""}
      ${o.wa_link ? `<a class="btn btn-secondary btn-block" style="margin-top:10px" href="${esc(o.wa_link)}" target="_blank" rel="noopener">Riapri link WhatsApp</a>` : ""}
      ${canDelete ? `<button class="btn btn-danger btn-block" style="margin-top:14px" id="btn-del-draft">Elimina bozza</button>` : ""}
    </div>`;

  $("#btn-back-orders").onclick = () => {
    if (o.status === "bozza" && o.supplier_id) openSupplier(o.supplier_id);
    else { showOrdiniHome(); loadOrders(); }
  };
  const addMore = $("#btn-add-more");
  if (addMore) addMore.onclick = () => openSupplier(o.supplier_id);

  $("#btn-save-ord").onclick = async () => {
    await api(`/api/orders/${oid}`, { method: "PUT", body: JSON.stringify({ delivery_date: $("#ord-delivery").value }) });
    for (const inp of $all("[data-qty]")) {
      await api(`/api/orders/${oid}/lines/${inp.dataset.qty}`, { method: "PUT", body: JSON.stringify({ quantity: Number(inp.value) }) });
    }
    alert("Salvato");
    openOrder(oid);
  };
  $("#btn-repeat") && ($("#btn-repeat").onclick = async () => {
    try {
      await api(`/api/orders/${oid}/repeat-last`, { method: "POST", body: "{}" });
      openOrder(oid);
    } catch (e) { alert(e.message); }
  });
  $all("[data-del]").forEach(b => {
    b.onclick = async () => {
      await api(`/api/orders/${oid}/lines/${b.dataset.del}`, { method: "DELETE" });
      openOrder(oid);
    };
  });
  $all("[data-recv]").forEach(sel => {
    sel.onchange = async () => {
      await api(`/api/orders/${oid}/lines/${sel.dataset.recv}`, { method: "PUT", body: JSON.stringify({ received_status: sel.value }) });
    };
  });
  const delBtn = $("#btn-del-draft");
  if (delBtn) {
    delBtn.onclick = async () => {
      if (!confirm("Eliminare questa bozza? L'operazione non si può annullare.")) return;
      try {
        await api(`/api/orders/${oid}`, { method: "DELETE" });
        showOrdiniHome();
        await loadOrders();
        alert("Bozza eliminata");
      } catch (e) { alert(e.message); }
    };
  }
  const sendBtn = $("#btn-send");
  if (sendBtn) {
    sendBtn.onclick = async () => {
      try {
        await api(`/api/orders/${oid}`, { method: "PUT", body: JSON.stringify({ delivery_date: $("#ord-delivery").value }) });
        for (const inp of $all("[data-qty]")) {
          await api(`/api/orders/${oid}/lines/${inp.dataset.qty}`, { method: "PUT", body: JSON.stringify({ quantity: Number(inp.value) }) });
        }
        const r = await api(`/api/orders/${oid}/send`, { method: "POST", body: "{}" });
        const wa = $("#wa-box");
        wa.classList.remove("hidden");
        wa.innerHTML = `<div class="alert alert-ok">Ordine segnato come inviato.</div>
          <a class="btn btn-primary btn-block" href="${esc(r.wa_link)}" target="_blank" rel="noopener">Apri WhatsApp</a>
          <pre style="white-space:pre-wrap;font-size:.8rem;background:#f7faf8;padding:10px;border-radius:10px;margin-top:8px">${esc(r.message)}</pre>`;
        loadOrders();
      } catch (e) { alert(e.message); }
    };
  }
}

// ─── FOOD COST ──────────────────────────────────────────
let recipeListCache = [];
let previewTimer = null;

async function loadRecipes() {
  const data = await api("/api/recipes");
  recipeListCache = data.recipes || [];
  $("#weekly-new").textContent = data.weekly_new;
  const box = $("#recipes-list");
  if (!data.recipes.length) { box.innerHTML = `<div class="empty">Nessuna ricetta — creane una</div>`; return; }
  box.innerHTML = data.recipes.map(r => `
    <div class="list-item" style="cursor:pointer" data-rid="${r.id}">
      <div>
        <div class="title">${esc(r.name)}</div>
        <div class="result-meta">${esc(r.category || "—")} · prodotto finito ${r.yield_qty} ${esc(r.yield_unit)}</div>
      </div>
      <span class="badge">Apri</span>
    </div>`).join("");
  box.querySelectorAll("[data-rid]").forEach(el => el.onclick = () => openRecipe(Number(el.dataset.rid)));
}

$("#btn-new-recipe").onclick = () => showRecipeForm(null);

async function openRecipe(rid) {
  const r = await api(`/api/recipes/${rid}`);
  $("#recipe-form").classList.add("hidden");
  const box = $("#recipe-detail");
  box.classList.remove("hidden");
  const c = r.cost || {};
  const ings = (c.ingredients || []).map(i =>
    `<div class="line-item"><div><div class="title">${esc(i.name)}</div>
      <div class="result-meta">${i.kind === "recipe" ? "sottoricetta" : "prodotto"}</div></div>
      <div>${i.quantity} ${esc(i.unit)}${IS_ADMIN && i.line_cost != null ? ` · ${fmtPrice(i.line_cost)}` : ""}</div></div>`
  ).join("");
  box.innerHTML = `
    <div class="card">
      <div class="row-between"><h2 style="margin:0">${esc(r.name)}</h2>
        <button class="btn btn-sm btn-ghost" id="btn-back-rec">← Elenco</button></div>
      <div class="result-meta">${esc(r.category || "")} · prodotto finito ${r.yield_qty} ${esc(r.yield_unit)}</div>
      ${IS_ADMIN && c.cost_per_kg != null ? `
        <div class="cost-summary card" style="background:var(--green-soft);margin:12px 0;box-shadow:none">
          <div class="row-between"><span>Costo totale</span><strong>${fmtPrice(c.total_cost != null ? c.total_cost : (c.cost_per_kg * (c.yield_kg || r.yield_qty)))}</strong></div>
          <div class="row-between" style="margin-top:6px"><span>Costo / kg</span><strong>${fmtPrice(c.cost_per_kg)}</strong></div>
          ${c.sell_price != null ? `<div class="row-between" style="margin-top:6px"><span>Vendita</span><strong>${fmtPrice(c.sell_price)}</strong></div>` : ""}
          ${c.food_cost_pct != null ? `<div class="row-between" style="margin-top:6px"><span>Food cost</span><strong>${c.food_cost_pct.toFixed(1)}% ${c.traffic_light ? `<span class="semaforo ${c.traffic_light}"></span>` : ""}</strong></div>` : ""}
        </div>` : ""}
      <div class="section-title">Ingredienti</div>
      ${ings || '<div class="empty">Nessun ingrediente</div>'}
      <div class="section-title">Scala produzione</div>
      <div class="row">
        <label style="flex:1">Voglio produrre (kg)
          <input type="number" id="scale-kg" value="${r.yield_qty}" min="0.01" step="any"/>
        </label>
        <button class="btn btn-secondary" id="btn-scale" style="align-self:flex-end">Ricalcola</button>
        <button class="btn btn-ghost no-print" id="btn-print" style="align-self:flex-end">Stampa</button>
      </div>
      <div id="scale-out"></div>
      <div class="row" style="margin-top:12px">
        <button class="btn btn-sm btn-secondary" id="btn-edit-rec">Modifica</button>
      </div>
    </div>`;
  $("#btn-back-rec").onclick = () => { box.classList.add("hidden"); loadRecipes(); };
  $("#btn-scale").onclick = async () => {
    const kg = Number($("#scale-kg").value);
    const sc = await api(`/api/recipes/${rid}/scale?kg=${kg}`);
    $("#scale-out").innerHTML = `<div class="card" style="background:#f7faf8"><strong>Scheda per ${kg} kg</strong>
      ${sc.ingredients.map(i => `<div class="line-item"><div>${esc(i.name)}</div><div>${i.quantity} ${esc(i.unit)}</div></div>`).join("")}
      </div>`;
  };
  $("#btn-print").onclick = () => {
    $("#btn-scale").click();
    setTimeout(() => window.print(), 300);
  };
  $("#btn-edit-rec").onclick = () => showRecipeForm(r);
}

function showRecipeForm(existing) {
  $("#recipe-detail").classList.add("hidden");
  $("#recipes-list").classList.add("hidden");
  const box = $("#recipe-form");
  box.classList.remove("hidden");
  const ings = existing ? (existing.ingredients_raw || []) : [];

  box.innerHTML = `
    <div class="row-between" style="margin-bottom:8px">
      <h2 style="margin:0">${existing ? "Modifica ricetta" : "Nuova ricetta"}</h2>
      <button class="btn btn-sm btn-ghost" type="button" id="cancel-rec">← Annulla</button>
    </div>
    <form id="rec-form" class="stack fc-flow">
      <div class="card fc-step">
        <div class="section-title" style="margin-top:0">1 · Nome ricetta</div>
        <label>Nome
          <input name="name" required value="${esc(existing?.name || "")}" placeholder="Es. Ragù della casa" autocomplete="off"/>
        </label>
        <label style="margin-top:8px">Categoria (opzionale)
          <input name="category" value="${esc(existing?.category || "")}" placeholder="Es. sughi"/>
        </label>
      </div>

      <div class="card fc-step">
        <div class="section-title" style="margin-top:0">2 · Ingredienti</div>
        <div id="ing-rows" class="ing-list"></div>
        <button type="button" class="btn btn-secondary btn-block" id="add-ing" style="margin-top:10px">+ Aggiungi ingrediente</button>
      </div>

      <div class="card fc-step">
        <div class="section-title" style="margin-top:0">3 · Prodotto finito</div>
        <div class="row">
          <label style="flex:1">Peso totale finale
            <input name="yield_qty" type="number" step="any" min="0.001" required value="${existing?.yield_qty ?? ""}" placeholder="Es. 5"/>
          </label>
          <label style="width:100px">Unità
            <select name="yield_unit">
              ${["kg","g"].map(u => `<option ${((existing?.yield_unit||"kg")===u)?"selected":""}>${u}</option>`).join("")}
            </select>
          </label>
        </div>
        <p class="muted" style="margin:8px 0 0;font-size:.8rem">Quanto pesa il prodotto finito dopo la preparazione.</p>
      </div>

      ${IS_ADMIN ? `
      <div class="card fc-step" id="cost-preview">
        <div class="section-title" style="margin-top:0">Costo (anteprima)</div>
        <div id="cost-preview-body" class="muted">Aggiungi ingredienti e peso finale per vedere il costo.</div>
      </div>
      <details class="card fc-step">
        <summary style="font-weight:700;cursor:pointer">Opzioni avanzate (admin)</summary>
        <div class="stack" style="margin-top:10px">
          <label>Prezzo vendita (€/kg) <input name="sell_price" type="number" step="any" value="${existing?.sell_price ?? ""}"/></label>
          <label>Extra condimenti € <input name="extra_condiments" type="number" step="any" value="${existing?.extra_condiments ?? 0}"/></label>
          <label>Vaschetta/etichetta € <input name="extra_packaging" type="number" step="any" value="${existing?.extra_packaging ?? 0}"/></label>
          <label>Energia/manodopera € <input name="extra_energy_labor" type="number" step="any" value="${existing?.extra_energy_labor ?? 0}"/></label>
        </div>
      </details>` : `
      <div class="card fc-step">
        <div class="muted" style="font-size:.9rem">I prezzi sono visibili solo all'admin. Tu puoi inserire ricetta e pesi.</div>
      </div>`}

      <button class="btn btn-primary btn-block" type="submit" style="min-height:52px;font-size:1.05rem">Salva ricetta</button>
    </form>`;

  const ingBox = $("#ing-rows");

  function collectIngredients() {
    return [...ingBox.querySelectorAll(".ing-row")].map(row => {
      const kind = row.querySelector("[name=kind]").value;
      return {
        kind,
        product_id: kind === "product" ? Number(row.querySelector("[name=product_id]").value) || null : null,
        sub_recipe_id: kind === "recipe" ? Number(row.querySelector("[name=sub_recipe_id]").value) || null : null,
        name: row.querySelector("[name=ing_name]").value,
        quantity: Number(row.querySelector("[name=ing_qty]").value),
        unit: row.querySelector("[name=ing_unit]").value,
      };
    }).filter(i => i.name && i.quantity > 0);
  }

  function schedulePreview() {
    if (!IS_ADMIN) return;
    clearTimeout(previewTimer);
    previewTimer = setTimeout(runPreview, 350);
  }

  async function runPreview() {
    const bodyEl = $("#cost-preview-body");
    if (!bodyEl) return;
    const form = $("#rec-form");
    const yield_qty = Number(form.yield_qty.value);
    const yield_unit = form.yield_unit.value;
    const ingredients = collectIngredients();
    if (!ingredients.length || !yield_qty) {
      bodyEl.innerHTML = `<span class="muted">Aggiungi ingredienti e peso finale per vedere il costo.</span>`;
      return;
    }
    bodyEl.innerHTML = `<span class="muted">Calcolo…</span>`;
    try {
      const extras = {
        extra_condiments: Number(form.extra_condiments?.value || 0),
        extra_packaging: Number(form.extra_packaging?.value || 0),
        extra_energy_labor: Number(form.extra_energy_labor?.value || 0),
      };
      const r = await api("/api/recipes/preview-cost", {
        method: "POST",
        body: JSON.stringify({ yield_qty, yield_unit, ingredients, ...extras }),
      });
      bodyEl.innerHTML = `
        <div class="row-between"><span>Costo totale</span><strong style="font-size:1.15rem">${fmtPrice(r.total_cost)}</strong></div>
        <div class="row-between" style="margin-top:8px"><span>Costo / kg</span><strong style="font-size:1.15rem;color:var(--green)">${fmtPrice(r.cost_per_kg)}</strong></div>
        ${r.has_missing_prices ? `<p class="muted" style="margin:8px 0 0;font-size:.8rem">⚠ Alcuni ingredienti senza prezzo</p>` : ""}
      `;
    } catch (e) {
      bodyEl.innerHTML = `<span class="alert alert-error">${esc(e.message)}</span>`;
    }
  }

  function addIngRow(ing = {}) {
    const div = document.createElement("div");
    div.className = "ing-row card";
    div.style.cssText = "padding:12px;margin-bottom:8px;box-shadow:none;border:1px solid var(--border)";
    const kind = ing.kind || "product";
    div.innerHTML = `
      <input type="hidden" name="kind" value="${esc(kind)}"/>
      <input type="hidden" name="product_id" value="${ing.product_id || ""}"/>
      <input type="hidden" name="sub_recipe_id" value="${ing.sub_recipe_id || ""}"/>
      <label>Ingrediente
        <input name="ing_search" type="search" placeholder="Cerca nel catalogo o sottoricetta…" value="${esc(ing.name || "")}" autocomplete="off"/>
      </label>
      <input type="hidden" name="ing_name" value="${esc(ing.name || "")}"/>
      <div class="pick-results hidden"></div>
      <div class="row" style="margin-top:8px;align-items:flex-end">
        <label style="flex:1">Peso
          <input name="ing_qty" type="number" step="any" min="0" value="${ing.quantity ?? ""}" placeholder="0" required/>
        </label>
        <label style="width:90px">Unità
          <select name="ing_unit">${["kg","g"].map(u => `<option ${(ing.unit||"kg")===u?"selected":""}>${u}</option>`).join("")}</select>
        </label>
        <button type="button" class="btn btn-sm btn-danger rm" title="Rimuovi">✕</button>
      </div>
      <div class="ing-picked muted" style="font-size:.75rem;margin-top:6px">
        ${ing.name ? (kind === "recipe" ? "Sottoricetta: " : "Catalogo: ") + esc(ing.name) : "Cerca e scegli un ingrediente"}
      </div>`;
    div.querySelector(".rm").onclick = () => { div.remove(); schedulePreview(); };

    const searchInp = div.querySelector("[name=ing_search]");
    const pickBox = div.querySelector(".pick-results");
    let t = null;
    searchInp.addEventListener("input", () => {
      clearTimeout(t);
      t = setTimeout(async () => {
        const q = searchInp.value.trim();
        if (q.length < 1) { pickBox.classList.add("hidden"); return; }
        const [prods, recipes] = await Promise.all([
          api(`/api/products/search?q=${encodeURIComponent(q)}&all=1`).catch(() => []),
          recipeListCache.length ? Promise.resolve(recipeListCache) : api("/api/recipes").then(d => (recipeListCache = d.recipes || [])),
        ]);
        const subHits = recipes.filter(r =>
          (!existing || r.id !== existing.id) &&
          (r.name || "").toLowerCase().includes(q.toLowerCase())
        ).slice(0, 6);
        const prodHits = (prods || []).slice(0, 8);
        if (!prodHits.length && !subHits.length) {
          pickBox.classList.remove("hidden");
          pickBox.innerHTML = `<div class="empty" style="padding:8px">Nessun risultato</div>`;
          return;
        }
        pickBox.classList.remove("hidden");
        pickBox.innerHTML =
          (subHits.length ? `<div class="pick-group">Sottoricette</div>` + subHits.map(r =>
            `<button type="button" class="pick-item" data-kind="recipe" data-id="${r.id}" data-name="${esc(r.name)}">${esc(r.name)} <span class="muted">ricetta</span></button>`
          ).join("") : "") +
          (prodHits.length ? `<div class="pick-group">Catalogo</div>` + prodHits.map(p =>
            `<button type="button" class="pick-item" data-kind="product" data-id="${p.id}" data-name="${esc(p.description)}">${esc(p.description)} <span class="muted">${esc(p.supplier_name || "")}</span></button>`
          ).join("") : "");
        pickBox.querySelectorAll(".pick-item").forEach(btn => {
          btn.onclick = () => {
            div.querySelector("[name=kind]").value = btn.dataset.kind;
            div.querySelector("[name=product_id]").value = btn.dataset.kind === "product" ? btn.dataset.id : "";
            div.querySelector("[name=sub_recipe_id]").value = btn.dataset.kind === "recipe" ? btn.dataset.id : "";
            div.querySelector("[name=ing_name]").value = btn.dataset.name;
            searchInp.value = btn.dataset.name;
            div.querySelector(".ing-picked").textContent =
              (btn.dataset.kind === "recipe" ? "Sottoricetta: " : "Catalogo: ") + btn.dataset.name;
            pickBox.classList.add("hidden");
            schedulePreview();
          };
        });
      }, 200);
    });

    div.querySelector("[name=ing_qty]").addEventListener("input", schedulePreview);
    div.querySelector("[name=ing_unit]").addEventListener("change", schedulePreview);
    ingBox.appendChild(div);
    if (!ing.name) searchInp.focus();
  }

  if (ings.length) ings.forEach(addIngRow);
  else addIngRow({});

  $("#add-ing").onclick = () => addIngRow({});
  $("#cancel-rec").onclick = () => {
    box.classList.add("hidden");
    $("#recipes-list").classList.remove("hidden");
    loadRecipes();
  };

  const form = $("#rec-form");
  form.yield_qty.addEventListener("input", schedulePreview);
  form.yield_unit.addEventListener("change", schedulePreview);
  ["extra_condiments","extra_packaging","extra_energy_labor"].forEach(n => {
    form[n]?.addEventListener("input", schedulePreview);
  });
  schedulePreview();

  form.onsubmit = async (e) => {
    e.preventDefault();
    const ingredients = [...ingBox.querySelectorAll(".ing-row")].map(row => {
      const kind = row.querySelector("[name=kind]").value;
      const name = row.querySelector("[name=ing_name]").value.trim() || row.querySelector("[name=ing_search]").value.trim();
      return {
        kind,
        product_id: kind === "product" ? Number(row.querySelector("[name=product_id]").value) || null : null,
        sub_recipe_id: kind === "recipe" ? Number(row.querySelector("[name=sub_recipe_id]").value) || null : null,
        name,
        quantity: Number(row.querySelector("[name=ing_qty]").value),
        unit: row.querySelector("[name=ing_unit]").value,
      };
    }).filter(i => i.name);

    for (const i of ingredients) {
      if (!i.product_id && !i.sub_recipe_id) {
        alert(`Scegli “${i.name}” dai risultati di ricerca (catalogo o sottoricetta).`);
        return;
      }
      if (!(i.quantity > 0)) {
        alert(`Indica il peso per “${i.name}”.`);
        return;
      }
    }
    if (!ingredients.length) {
      alert("Aggiungi almeno un ingrediente.");
      return;
    }

    const body = {
      name: form.name.value.trim(),
      category: form.category.value.trim(),
      yield_qty: Number(form.yield_qty.value),
      yield_unit: form.yield_unit.value,
      ingredients,
    };
    if (IS_ADMIN) {
      body.sell_price = form.sell_price?.value || null;
      body.extra_condiments = Number(form.extra_condiments?.value || 0);
      body.extra_packaging = Number(form.extra_packaging?.value || 0);
      body.extra_energy_labor = Number(form.extra_energy_labor?.value || 0);
    }
    try {
      if (existing) {
        await api(`/api/recipes/${existing.id}`, { method: "PUT", body: JSON.stringify(body) });
        box.classList.add("hidden");
        $("#recipes-list").classList.remove("hidden");
        openRecipe(existing.id);
      } else {
        const r = await api("/api/recipes", { method: "POST", body: JSON.stringify(body) });
        box.classList.add("hidden");
        $("#recipes-list").classList.remove("hidden");
        openRecipe(r.id);
      }
      loadRecipes();
    } catch (err) { alert(err.message); }
  };
}

// ─── ADMIN ──────────────────────────────────────────────
async function loadAdmin() {
  if (!IS_ADMIN) return;
  const st = await api("/api/catalog/stats");
  $("#catalog-stats").textContent = `${st.products_total} prodotti · ${st.products_visible} visibili (ultimi 12 mesi) · ${st.suppliers} fornitori · import ${st.imported_at || "—"}`;
  const users = await api("/api/users");
  $("#users-list").innerHTML = users.map(u =>
    `<div class="list-item"><div><strong>${esc(u.username)}</strong> — ${esc(u.full_name)}</div><span class="badge">${esc(u.role)}</span></div>`
  ).join("");
  const audit = await api("/api/audit?limit=30");
  $("#audit-list").innerHTML = audit.map(a =>
    `<div>${esc(a.created_at)} · ${esc(a.username)} · ${esc(a.action)} ${esc(a.entity || "")} ${esc(a.entity_id || "")} ${esc(a.detail || "")}</div>`
  ).join("");
}

if ($("#btn-reimport")) {
  $("#btn-reimport").onclick = async () => {
    if (!confirm("Reimportare il catalogo dal CSV?")) return;
    try {
      const st = await api("/api/catalog/reimport", { method: "POST", body: "{}" });
      alert(`Import ok: ${st.upserted} prodotti, ${st.suppliers} fornitori, ${st.products_visible} visibili`);
      loadAdmin();
      loadSuppliers();
    } catch (e) { alert(e.message); }
  };
}
if ($("#form-user")) {
  $("#form-user").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      await api("/api/users", { method: "POST", body: JSON.stringify(Object.fromEntries(fd.entries())) });
      e.target.reset();
      loadAdmin();
      alert("Utente creato");
    } catch (err) { alert(err.message); }
  };
}

// boot
loadSuppliers().then(loadOrders).catch(e => console.error(e));
