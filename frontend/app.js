"use strict";

const $ = (sel) => document.querySelector(sel);
const state = { leads: [], leadId: null, dialog: null, messages: [], edited: false, kb: [], kbEditing: null };

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map((d) => `${d.loc?.slice(1).join(".")}: ${d.msg}`).join("\n")
      : data.detail;
    const err = new Error(detail || `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c != null) node.append(c);
  return node;
}

const fmtPrice = (n) => (n == null ? "—" : `${n.toLocaleString("ru-RU")} ₽`);

/* ---------- вкладки ---------- */
function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.tab === name)));
  for (const p of ["dialogs", "kb", "history"]) $(`#panel-${p}`).hidden = p !== name;
  if (name === "kb") loadKB();
  if (name === "history") loadHistory();
  try { history.replaceState(null, "", `#${name}`); } catch {}
}
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));

/* ---------- статус LLM ---------- */
async function checkStatus() {
  const box = $("#llm-status");
  try {
    const s = await api("/api/status");
    state.llmOk = s.llm_reachable && s.model_available;
    box.className = `status ${state.llmOk ? "ok" : "bad"}`;
    box.title = state.llmOk
      ? "Модель запущена — ответы генерируются вживую"
      : "Модель работает на ноутбуке автора и сейчас выключена. Показываются сохранённые ответы.";
    box.querySelector(".status-text").textContent = state.llmOk
      ? `LLM онлайн: ${s.model}`
      : s.llm_reachable
        ? `модель ${s.model} не скачана`
        : "LLM офлайн — показываю сохранённые ответы";
  } catch {
    state.llmOk = false;
    box.className = "status bad";
    box.querySelector(".status-text").textContent = "API недоступно";
  }
  return state.llmOk;
}

/* Сохранённый ответ модели по сделке — когда модель выключена. */
async function showSaved(reason) {
  if (state.edited) {
    $("#result-error").textContent = `${reason}\nДля отредактированного диалога сохранённого ответа нет — нажмите «Сбросить», чтобы увидеть сохранённый ответ по исходной переписке.`;
    showResult("error");
    return;
  }
  try {
    const r = await api(`/api/leads/${state.leadId}/saved-analysis`);
    renderResult(r);
    const when = new Date(r.created_at).toLocaleString("ru-RU", { dateStyle: "long", timeStyle: "short" });
    $("#r-saved").textContent = `${reason} Ниже — настоящий ответ ${r.model}, сохранённый ${when}.`;
    $("#r-saved").hidden = false;
    showResult("result");
  } catch (e) {
    $("#result-error").textContent = `${reason}\n${e.message}`;
    showResult("error");
  }
}
const OFFLINE = "Модель сейчас выключена: она работает на ноутбуке автора, а не на сервере.";

/* ---------- сделки и диалог ---------- */
async function loadLeads() {
  state.leads = await api("/api/leads");
  const list = $("#lead-list");
  list.replaceChildren(
    ...state.leads.map((l) =>
      el("li", {},
        el("button", { type: "button", "data-id": l.lead_id, onclick: () => selectLead(l.lead_id) },
          el("span", { class: "lead-name" }, l.title),
          el("span", { class: "lead-scn" }, l.scenario)))
    )
  );
  const fromHash = state.leads.find((l) => l.lead_id === location.hash.slice(1));
  selectLead((fromHash || state.leads[0])?.lead_id);
}

async function selectLead(id) {
  if (!id) return;
  state.leadId = id;
  document.querySelectorAll("#lead-list button").forEach((b) => b.setAttribute("aria-current", String(b.dataset.id === id)));
  const { dialog, raw } = await api(`/api/leads/${id}`);
  state.dialog = dialog;
  state.messages = dialog.messages.map((m) => ({ ...m }));
  state.edited = false;
  $("#lead-title").textContent = dialog.lead_title || `Сделка ${id}`;
  $("#lead-meta").textContent = `Сделка #${id} · контакт: ${dialog.contact_name || "—"} · ${dialog.messages.length} сообщ.`;
  $("#raw-json").textContent = JSON.stringify(raw, null, 2);
  renderMessages();
  showResult("empty");
  if (state.llmOk === false) showSaved(OFFLINE);
}

function renderMessages() {
  $("#messages").replaceChildren(
    ...state.messages.map((m) =>
      el("li", { class: `msg ${m.author}${m.added ? " added" : ""}` },
        el("div", { class: "who" }, m.author === "client" ? state.dialog.contact_name || "Клиент" : "Менеджер"),
        el("div", { class: "text" }, m.text)))
  );
}

function addMessage(author) {
  const text = $("#composer-text").value.trim();
  if (!text) return;
  state.messages.push({ author, text, added: true });
  state.edited = true;
  $("#composer-text").value = "";
  renderMessages();
}
$("#composer").addEventListener("submit", (e) => { e.preventDefault(); addMessage("client"); });
$("#add-manager").addEventListener("click", () => addMessage("manager"));
$("#reset-dialog").addEventListener("click", () => selectLead(state.leadId));
$("#toggle-raw").addEventListener("click", () => { $("#raw-json").hidden = !$("#raw-json").hidden; });

/* ---------- анализ ---------- */
let timer = null;
function showResult(which) {
  $("#result-empty").hidden = which !== "empty";
  $("#result-loading").hidden = which !== "loading";
  $("#result-error").hidden = which !== "error";
  $("#result").hidden = which !== "result";
}

$("#analyze").addEventListener("click", async () => {
  const btn = $("#analyze");
  btn.disabled = true;
  showResult("loading");
  const started = Date.now();
  timer = setInterval(() => { $("#elapsed").textContent = `${Math.round((Date.now() - started) / 1000)} с`; }, 500);
  const body = state.edited
    ? {
        lead_id: state.leadId,
        contact_name: state.dialog.contact_name,
        messages: state.messages.map(({ author, text }) => ({ author, text })),
      }
    : { lead_id: state.leadId };
  try {
    renderResult(await api("/api/analyze", { method: "POST", body }));
    showResult("result");
  } catch (e) {
    if (e.status === 503) {
      await checkStatus();
      await showSaved(OFFLINE);
    } else {
      $("#result-error").textContent = e.message;
      showResult("error");
    }
  } finally {
    clearInterval(timer);
    btn.disabled = false;
    checkStatus();
  }
});

function renderResult(r) {
  $("#r-saved").hidden = true;
  $("#r-reply").textContent = r.customer_reply;
  $("#r-hint").textContent = r.upsell_hint || "Допродажу сейчас предлагать не стоит.";
  $("#r-intent").textContent = r.client_intent;
  $("#r-manager").textContent = r.manager_already_did;
  $("#r-kb").replaceChildren(
    ...(r.used_kb.length ? r.used_kb.map((a) => el("span", { class: "chip", title: a.content }, a.title)) : [el("span", { class: "muted" }, "—")])
  );
  $("#r-flags").replaceChildren(
    r.needs_manager
      ? el("span", { class: "pill attention" }, "Нужна проверка менеджером")
      : el("span", { class: "pill" }, "Можно отправлять"),
    el("span", { class: "pill" }, r.upsell_appropriate ? "Допродажа уместна" : "Без допродажи")
  );
  $("#r-warnings").replaceChildren(...r.warnings.map((w) => el("li", {}, w)));
  $("#r-meta").textContent = `${r.model} · ${(r.latency_ms / 1000).toFixed(1)} с · анализ #${r.analysis_id ?? "—"}`;
}

$("#copy-reply").addEventListener("click", async () => {
  const text = $("#r-reply").textContent;
  try {
    await navigator.clipboard.writeText(text);
    $("#copy-reply").textContent = "Скопировано";
  } catch {
    const range = document.createRange();
    range.selectNodeContents($("#r-reply"));
    getSelection().removeAllRanges();
    getSelection().addRange(range);
    $("#copy-reply").textContent = "Выделено — Ctrl+C";
  }
  setTimeout(() => { $("#copy-reply").textContent = "Копировать"; }, 1800);
});

/* ---------- база знаний ---------- */
const KIND = { product: "товар", service: "услуга", policy: "условия", faq: "FAQ", company: "компания" };

async function loadKB() {
  state.kb = await api("/api/kb");
  $("#kb-rows").replaceChildren(
    ...state.kb.map((a) =>
      el("tr", { onclick: () => editArticle(a) },
        el("td", { class: "clip" }, el("strong", {}, a.title), el("div", { class: "muted small-text" }, a.slug)),
        el("td", {}, KIND[a.kind] || a.kind),
        el("td", { class: "num" }, fmtPrice(a.price_rub))))
  );
}

function editArticle(a) {
  state.kbEditing = a?.id ?? null;
  $("#kb-form").hidden = false;
  $("#kb-form-title").textContent = a ? `Статья #${a.id}` : "Новая статья";
  $("#kb-title").value = a?.title ?? "";
  $("#kb-slug").value = a?.slug ?? "";
  $("#kb-kind").value = a?.kind ?? "product";
  $("#kb-price").value = a?.price_rub ?? "";
  $("#kb-related").value = (a?.related_slugs ?? []).join(", ");
  $("#kb-keywords").value = a?.keywords ?? "";
  $("#kb-content").value = a?.content ?? "";
  $("#kb-delete").hidden = !a;
  $("#kb-form-msg").textContent = "";
}
$("#kb-new").addEventListener("click", () => editArticle(null));
$("#kb-cancel").addEventListener("click", () => { $("#kb-form").hidden = true; });

$("#kb-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    title: $("#kb-title").value.trim(),
    slug: $("#kb-slug").value.trim(),
    kind: $("#kb-kind").value,
    price_rub: $("#kb-price").value === "" ? null : Number($("#kb-price").value),
    related_slugs: $("#kb-related").value.split(",").map((s) => s.trim()).filter(Boolean),
    keywords: $("#kb-keywords").value.trim(),
    content: $("#kb-content").value.trim(),
  };
  try {
    const saved = state.kbEditing
      ? await api(`/api/kb/${state.kbEditing}`, { method: "PUT", body })
      : await api("/api/kb", { method: "POST", body });
    await loadKB();
    editArticle(saved);
    $("#kb-form-msg").textContent = "Сохранено";
  } catch (err) {
    $("#kb-form-msg").textContent =
      err.status === 401 ? "Редактирование базы знаний на демо-сайте доступно только автору." : err.message;
  }
});

let deleteArmed = false;
$("#kb-delete").addEventListener("click", async () => {
  if (!deleteArmed) {
    deleteArmed = true;
    $("#kb-delete").textContent = "Точно удалить?";
    setTimeout(() => { deleteArmed = false; $("#kb-delete").textContent = "Удалить"; }, 3000);
    return;
  }
  deleteArmed = false;
  $("#kb-delete").textContent = "Удалить";
  try {
    await api(`/api/kb/${state.kbEditing}`, { method: "DELETE" });
    $("#kb-form").hidden = true;
    loadKB();
  } catch (err) {
    $("#kb-form-msg").textContent =
      err.status === 401 ? "Редактирование базы знаний на демо-сайте доступно только автору." : err.message;
  }
});

let searchTimer = null;
$("#kb-search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(async () => {
    const q = $("#kb-search").value.trim();
    if (!q) { $("#kb-search-result").textContent = ""; return; }
    const found = await api(`/api/kb/search?q=${encodeURIComponent(q)}&limit=5`);
    $("#kb-search-result").textContent = found.length
      ? `Найдётся: ${found.map((a) => a.title).join(" · ")}`
      : "Ничего не найдено — модель ответит «уточню у коллег».";
  }, 300);
});

/* ---------- история ---------- */
async function loadHistory() {
  const rows = await api("/api/analyses?limit=30");
  $("#history-rows").replaceChildren(
    ...(rows.length
      ? rows.map((r) =>
          el("tr", {},
            el("td", { class: "num" }, String(r.id)),
            el("td", {}, r.lead_id),
            el("td", { class: "clip" }, r.customer_reply || ""),
            el("td", { class: "clip" }, r.upsell_hint || "—"),
            el("td", { class: "mono small-text" }, r.model),
            el("td", { class: "num" }, `${(r.latency_ms / 1000).toFixed(1)} с`)))
      : [el("tr", {}, el("td", { colspan: "6", class: "muted" }, "Пока пусто — запустите анализ на вкладке «Диалоги»."))])
  );
}

/* ---------- старт ---------- */
checkStatus()
  .then(loadLeads)
  .catch((e) => { $("#result-error").textContent = e.message; showResult("error"); });
const initialTab = location.hash.slice(1);
if (initialTab === "kb" || initialTab === "history") showTab(initialTab);
