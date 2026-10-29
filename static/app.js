/* POTA LA GASTRO frontend */
const IS_ADMIN = !!(window.PLG && window.PLG.isAdmin);

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
    if (btn.dataset.tab === "ordini") loadOrders();
    if (btn.dataset.tab === "foodcost") loadRecipes();
    if (btn.dataset.tab === "admin") loadAdmin();
  });
});

// ─── ORDINI ─────────────────────────────────────────────
let suppliers = [];
let currentOrderId = null;
let searchTimer = null;

async function loadSuppliers() {
  suppliers = await api("/api/suppliers");
  const wrap = $("#supplier-chips");
  wrap.innerHTML = suppliers.map(s =>
    `<button class="chip" data-sid="${s.id}">${esc(s.name)} <span class="muted">(${s.n_visible})</span></button>`
  ).join("");
  wrap.querySelectorAll(".chip").forEach(ch => {
    ch.addEventListener("click", () => openSupplier(Number(ch.dataset.sid)));
  });
}

async function openSupplier(sid) {
  const s = suppliers.find(x => x.id === sid) || await api(`/api/suppliers/${sid}`);
  const panel = $("#supplier-panel");
  panel.classList.remove("hidden");
  $("#order-detail").classList.add("hidden");
  panel.innerHTML = `
    <div class="row-between"><h2 style="margin:0">${esc(s.name)}</h2>
      <button class="btn btn-sm btn-primary" id="btn-draft">Apri / crea bozza</button></div>
    <div class="result-meta" style="margin:8px 0">
      Consegna: ${esc(s.delivery_days || "—")} · Limite: ${esc(s.order_deadline || "—")} · Minimo: ${esc(s.min_order || "—")}<br/>
      Rappresentante: ${esc(s.rep_name || "—")} · WhatsApp: ${esc(s.rep_whatsapp || "—")}
    </div>
    ${IS_ADMIN ? `<details style="margin-bottom:10px"><summary>Configura fornitore (admin)</summary>
      <form id="sup-cfg" class="stack" style="margin-top:8px">
        <label>Giorni consegna <input name="delivery_days" value="${esc(s.delivery_days || "")}"/></label>
        <label>Orario limite <input name="order_deadline" value="${esc(s.order_deadline || "")}"/></label>
        <label>Ordine minimo <input name="min_order" value="${esc(s.min_order || "")}"/></label>
        <label>Nome rappresentante <input name="rep_name" value="${esc(s.rep_name || "")}"/></label>
        <label>WhatsApp (es. 393331234567) <input name="rep_whatsapp" value="${esc(s.rep_whatsapp || "")}"/></label>
        <button class="btn btn-secondary btn-sm" type="submit">Salva config</button>
      </form></details>` : ""}
    <div class="row" style="margin-bottom:8px">
      <button class="btn btn-sm btn-secondary" id="btn-favs">Preferiti</button>
      <button class="btn btn-sm btn-ghost" id="btn-search-sup">Cerca in questo fornitore</button>
    </div>
    <div id="sup-extra"></div>
  `;
  $("#btn-draft").onclick = async () => {
    const o = await api("/api/orders", { method: "POST", body: JSON.stringify({ supplier_id: sid }) });
    openOrder(o.id);
  };
  if (IS_ADMIN && $("#sup-cfg")) {
    $("#sup-cfg").onsubmit = async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      const body = Object.fromEntries(fd.entries());
      await api(`/api/suppliers/${sid}`, { method: "PUT", body: JSON.stringify(body) });
      await loadSuppliers();
      openSupplier(sid);
      alert("Configurazione salvata");
    };
  }
  $("#btn-favs").onclick = async () => {
    const favs = await api(`/api/orders/favorites/${sid}`);
    const box = $("#sup-extra");
    if (!favs.length) { box.innerHTML = `<div class="empty">Nessun preferito</div>`; return; }
    box.innerHTML = favs.map(p => `
      <div class="result-item">
        <div><div class="title">${esc(p.description)}</div>
        <div class="result-meta">${esc(p.unit || "")}</div></div>
        <button class="btn btn-sm btn-primary" data-add="${p.id}">+ Bozza</button>
      </div>`).join("");
    box.querySelectorAll("[data-add]").forEach(b => {
      b.onclick = async () => {
        const o = await api("/api/orders", { method: "POST", body: JSON.stringify({ supplier_id: sid }) });
        await api(`/api/orders/${o.id}/lines`, { method: "POST", body: JSON.stringify({ product_id: Number(b.dataset.add), quantity: 1 }) });
        openOrder(o.id);
      };
    });
  };
  $("#btn-search-sup").onclick = () => {
    $("#search-q").focus();
    $("#search-q").dataset.sid = sid;
    $("#search-q").placeholder = `Cerca in ${s.name}…`;
  };
}

async function doSearch() {
  const q = $("#search-q").value.trim();
  const box = $("#search-results");
  if (q.length < 2) { box.classList.add("hidden"); return; }
  const sid = $("#search-q").dataset.sid;
  const qs = new URLSearchParams({ q });
  if (sid) qs.set("supplier_id", sid);
  const rows = await api(`/api/products/search?${qs}`);
  box.classList.remove("hidden");
  if (!rows.length) { box.innerHTML = `<div class="empty">Nessun risultato per “${esc(q)}”</div>`; return; }
  box.innerHTML = `<div class="muted" style="margin-bottom:8px">${rows.length} risultati</div>` + rows.map(p => `
    <div class="result-item">
      <div>
        <div class="title">${esc(p.description)}</div>
        <div class="result-meta">${esc(p.supplier_name)} · ${esc(p.unit || "—")} · ultimo acq. ${fmtDate(p.last_purchase_date)}
          ${IS_ADMIN ? ` · <strong>${fmtPrice(p.effective_price)}</strong>` : ""}
          ${p.favorited ? ' · ★' : ""}
        </div>
      </div>
      <div class="row">
        ${IS_ADMIN ? `<button class="btn btn-sm btn-ghost" data-vis="${p.id}" data-v="${p.visible}">${p.visible ? "Nascondi" : "Mostra"}</button>` : ""}
        <button class="btn btn-sm btn-ghost" data-fav="${p.id}" data-f="${p.favorited}">${p.favorited ? "★" : "☆"}</button>
        <button class="btn btn-sm btn-primary" data-pid="${p.id}" data-sid="${p.supplier_id}">+</button>
      </div>
    </div>`).join("");
  box.querySelectorAll("[data-pid]").forEach(b => {
    b.onclick = async () => {
      const o = await api("/api/orders", { method: "POST", body: JSON.stringify({ supplier_id: Number(b.dataset.sid) }) });
      await api(`/api/orders/${o.id}/lines`, { method: "POST", body: JSON.stringify({ product_id: Number(b.dataset.pid), quantity: 1 }) });
      openOrder(o.id);
    };
  });
  box.querySelectorAll("[data-fav]").forEach(b => {
    b.onclick = async () => {
      await api(`/api/products/${b.dataset.fav}/favorite`, { method: "POST", body: JSON.stringify({ favorited: b.dataset.f !== "1" }) });
      doSearch();
    };
  });
  box.querySelectorAll("[data-vis]").forEach(b => {
    b.onclick = async () => {
      await api(`/api/products/${b.dataset.vis}/visibility`, { method: "POST", body: JSON.stringify({ visible: b.dataset.v !== "1" }) });
      doSearch();
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
  $("#supplier-panel").classList.add("hidden");
  $("#search-results").classList.add("hidden");
  const box = $("#order-detail");
  box.classList.remove("hidden");
  const canEdit = o.status === "bozza" || IS_ADMIN;
  box.innerHTML = `
    <div class="card">
      <div class="row-between">
        <h2 style="margin:0">${esc(o.supplier_name)}</h2>
        <button class="btn btn-sm btn-ghost" id="btn-back-orders">← Elenco</button>
      </div>
      <div class="result-meta" style="margin:8px 0">Stato: <strong>${o.status}</strong>
        ${o.sent_at ? " · inviato " + esc(o.sent_at) : ""}</div>
      <label>Data consegna
        <input type="date" id="ord-delivery" value="${esc((o.delivery_date || "").slice(0,10))}" ${canEdit ? "" : "disabled"}/>
      </label>
      <div class="row" style="margin:10px 0">
        <button class="btn btn-sm btn-secondary" id="btn-repeat" ${o.status !== "bozza" ? "disabled" : ""}>Ripeti ultimo ordine</button>
        <button class="btn btn-sm btn-ghost" id="btn-save-ord">Salva</button>
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
          </div>`).join("") || '<div class="empty">Nessuna riga — cerca un prodotto e aggiungilo</div>'}
      </div>
      ${IS_ADMIN && o.status === "bozza" ? `
        <button class="btn btn-primary btn-block" style="margin-top:12px" id="btn-send">Invia via WhatsApp</button>
        <div id="wa-box" class="hidden" style="margin-top:10px"></div>` : ""}
      ${o.wa_link ? `<a class="btn btn-secondary btn-block" style="margin-top:10px" href="${esc(o.wa_link)}" target="_blank" rel="noopener">Riapri link WhatsApp</a>` : ""}
    </div>`;
  $("#btn-back-orders").onclick = () => { box.classList.add("hidden"); loadOrders(); };
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
async function loadRecipes() {
  const data = await api("/api/recipes");
  $("#weekly-new").textContent = data.weekly_new;
  const box = $("#recipes-list");
  if (!data.recipes.length) { box.innerHTML = `<div class="empty">Nessuna ricetta — creane una</div>`; return; }
  box.innerHTML = data.recipes.map(r => `
    <div class="list-item" style="cursor:pointer" data-rid="${r.id}">
      <div>
        <div class="title">${esc(r.name)}</div>
        <div class="result-meta">${esc(r.category || "—")} · resa ${r.yield_qty} ${esc(r.yield_unit)}</div>
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
      <div class="result-meta">${esc(r.category || "")} · resa ${r.yield_qty} ${esc(r.yield_unit)}</div>
      ${IS_ADMIN && c.cost_per_kg != null ? `
        <div class="row" style="margin:10px 0;gap:14px">
          <div>Costo/kg: <strong>${fmtPrice(c.cost_per_kg)}</strong></div>
          <div>Vendita: <strong>${fmtPrice(c.sell_price)}</strong></div>
          <div>Food cost: <strong>${c.food_cost_pct != null ? c.food_cost_pct.toFixed(1) + "%" : "—"}</strong>
            ${c.traffic_light ? `<span class="semaforo ${c.traffic_light}"></span>` : ""}</div>
          <div>Margine/kg: <strong>${fmtPrice(c.margin)}</strong></div>
        </div>` : ""}
      <div class="section-title">Ingredienti (crudi)</div>
      ${ings || '<div class="empty">Nessun ingrediente</div>'}
      <div class="section-title">Scala produzione</div>
      <div class="row">
        <label style="flex:1">Voglio produrre (kg)
          <input type="number" id="scale-kg" value="${r.yield_qty}" min="0.01" step="any"/>
        </label>
        <button class="btn btn-secondary" id="btn-scale" style="align-self:flex-end">Ricalcola</button>
        <button class="btn btn-ghost no-print" id="btn-print" style="align-self:flex-end">Stampa scheda</button>
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
  const box = $("#recipe-form");
  box.classList.remove("hidden");
  const ings = existing ? (existing.ingredients_raw || []) : [];
  box.innerHTML = `
    <h2>${existing ? "Modifica ricetta" : "Nuova ricetta"}</h2>
    <form id="rec-form" class="stack">
      <label>Nome <input name="name" required value="${esc(existing?.name || "")}"/></label>
      <label>Categoria <input name="category" value="${esc(existing?.category || "")}"/></label>
      <div class="row">
        <label style="flex:1">Peso totale ottenuto <input name="yield_qty" type="number" step="any" required value="${existing?.yield_qty ?? 1}"/></label>
        <label>Unità
          <select name="yield_unit">
            ${["kg","g","l","pz"].map(u => `<option ${((existing?.yield_unit||"kg")===u)?"selected":""}>${u}</option>`).join("")}
          </select>
        </label>
      </div>
      ${IS_ADMIN ? `
        <label>Prezzo vendita (€/kg) <input name="sell_price" type="number" step="any" value="${existing?.sell_price ?? ""}"/></label>
        <div class="row">
          <label style="flex:1">Extra condimenti € <input name="extra_condiments" type="number" step="any" value="${existing?.extra_condiments ?? 0}"/></label>
          <label style="flex:1">Vaschetta/etichetta € <input name="extra_packaging" type="number" step="any" value="${existing?.extra_packaging ?? 0}"/></label>
          <label style="flex:1">Energia/manodopera € <input name="extra_energy_labor" type="number" step="any" value="${existing?.extra_energy_labor ?? 0}"/></label>
        </div>` : ""}
      <div class="section-title">Ingredienti</div>
      <div id="ing-rows"></div>
      <div class="row">
        <button type="button" class="btn btn-sm btn-secondary" id="add-prod-ing">+ Da catalogo</button>
        <button type="button" class="btn btn-sm btn-ghost" id="add-sub-ing">+ Sottoricetta</button>
      </div>
      <div class="row">
        <button class="btn btn-primary" type="submit">Salva ricetta</button>
        <button class="btn btn-ghost" type="button" id="cancel-rec">Annulla</button>
      </div>
    </form>`;
  const ingBox = $("#ing-rows");
  function addIngRow(ing = {}) {
    const div = document.createElement("div");
    div.className = "card";
    div.style.padding = "10px";
    div.innerHTML = `
      <input type="hidden" name="kind" value="${esc(ing.kind || "product")}"/>
      <input type="hidden" name="product_id" value="${ing.product_id || ""}"/>
      <input type="hidden" name="sub_recipe_id" value="${ing.sub_recipe_id || ""}"/>
      <label>Nome <input name="ing_name" value="${esc(ing.name || "")}" required/></label>
      <div class="row">
        <label style="flex:1">Quantità <input name="ing_qty" type="number" step="any" value="${ing.quantity ?? 0}" required/></label>
        <label>Unità
          <select name="ing_unit">${["kg","g","l","ml","pz"].map(u => `<option ${(ing.unit||"kg")===u?"selected":""}>${u}</option>`).join("")}</select>
        </label>
        ${IS_ADMIN ? `<label style="flex:1">Costo/u override <input name="ing_override" type="number" step="any" value="${ing.unit_cost_override ?? ""}"/></label>` : ""}
        <button type="button" class="btn btn-sm btn-danger rm">✕</button>
      </div>
      <div class="muted" style="font-size:.75rem">${ing.kind === "recipe" ? "Sottoricetta" : "Prodotto catalogo"} ${ing.product_id ? "#"+ing.product_id : ""} ${ing.sub_recipe_id ? "#"+ing.sub_recipe_id : ""}</div>`;
    div.querySelector(".rm").onclick = () => div.remove();
    ingBox.appendChild(div);
  }
  ings.forEach(addIngRow);
  $("#add-prod-ing").onclick = async () => {
    const q = prompt("Cerca prodotto nel catalogo:");
    if (!q) return;
    const rows = await api(`/api/products/search?q=${encodeURIComponent(q)}`);
    if (!rows.length) return alert("Nessun risultato");
    const pick = rows[0];
    // if multiple, simple pick first — MVP; show list if many
    let chosen = pick;
    if (rows.length > 1) {
      const msg = rows.slice(0, 8).map((r, i) => `${i + 1}. ${r.description} (${r.supplier_name})`).join("\n");
      const n = prompt(`Scegli (1-${Math.min(8, rows.length)}):\n${msg}`, "1");
      const idx = Number(n) - 1;
      if (idx >= 0 && idx < rows.length) chosen = rows[idx];
      else return;
    }
    addIngRow({ kind: "product", product_id: chosen.id, name: chosen.description, quantity: 0.1, unit: "kg" });
  };
  $("#add-sub-ing").onclick = async () => {
    const data = await api("/api/recipes");
    const list = data.recipes.filter(r => !existing || r.id !== existing.id);
    if (!list.length) return alert("Nessuna ricetta disponibile");
    const msg = list.slice(0, 12).map((r, i) => `${i + 1}. ${r.name}`).join("\n");
    const n = prompt(`Scegli sottoricetta:\n${msg}`, "1");
    const idx = Number(n) - 1;
    if (idx < 0 || idx >= list.length) return;
    const r = list[idx];
    addIngRow({ kind: "recipe", sub_recipe_id: r.id, name: r.name, quantity: 0.5, unit: "kg" });
  };
  $("#cancel-rec").onclick = () => { box.classList.add("hidden"); loadRecipes(); };
  $("#rec-form").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const ingredients = [...ingBox.children].map(row => {
      const kind = row.querySelector("[name=kind]").value;
      return {
        kind,
        product_id: kind === "product" ? Number(row.querySelector("[name=product_id]").value) || null : null,
        sub_recipe_id: kind === "recipe" ? Number(row.querySelector("[name=sub_recipe_id]").value) || null : null,
        name: row.querySelector("[name=ing_name]").value,
        quantity: Number(row.querySelector("[name=ing_qty]").value),
        unit: row.querySelector("[name=ing_unit]").value,
        unit_cost_override: IS_ADMIN ? (row.querySelector("[name=ing_override]")?.value || null) : null,
      };
    });
    const body = {
      name: fd.get("name"),
      category: fd.get("category"),
      yield_qty: Number(fd.get("yield_qty")),
      yield_unit: fd.get("yield_unit"),
      ingredients,
    };
    if (IS_ADMIN) {
      body.sell_price = fd.get("sell_price") || null;
      body.extra_condiments = Number(fd.get("extra_condiments") || 0);
      body.extra_packaging = Number(fd.get("extra_packaging") || 0);
      body.extra_energy_labor = Number(fd.get("extra_energy_labor") || 0);
    }
    try {
      if (existing) {
        await api(`/api/recipes/${existing.id}`, { method: "PUT", body: JSON.stringify(body) });
        box.classList.add("hidden");
        openRecipe(existing.id);
      } else {
        const r = await api("/api/recipes", { method: "POST", body: JSON.stringify(body) });
        box.classList.add("hidden");
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
