import { api, h, money, showMode, STATE_TEXT } from "/assets/common.js";

const KEY_STORE = "yieldra.operatorKey";
const keyInput = document.getElementById("key");
const errorText = document.getElementById("ledger-error");
const detail = document.getElementById("detail");
let rows = [];
let grid = null;
let testMode = false;

keyInput.value = sessionStorage.getItem(KEY_STORE) || "";
const call = (path, options = {}) => api(path, { ...options, adminKey: keyInput.value || undefined });

const PAYOUT_TEXT = {
  needs_bank_details: "Needs farmer's bank details",
  awaiting_funding: "Transfer to fund",
  paid: "Paid to farmer",
  failed: "Failed",
};

function pill(state, text) {
  return h("span", { className: "pill", dataset: { state } }, text);
}

const columns = [
  { headerName: "Farm", field: "farm", pinned: "left", width: 190 },
  { headerName: "Stage", field: "stage", width: 170, valueGetter: (p) => p.data.stage && `${p.data.sequence}. ${p.data.stage}` },
  {
    headerName: "Status", field: "status", width: 290,
    cellRenderer: (p) => (p.value ? pill(p.value, STATE_TEXT[p.value] || p.value) : ""),
    filterValueGetter: (p) => STATE_TEXT[p.data.status],
  },
  {
    headerName: "Amount", field: "amount", width: 130, type: "rightAligned",
    valueFormatter: (p) => (p.value === null || p.value === undefined ? "" : money(p.value, p.data.currency)),
  },
  { headerName: "Rail", field: "rail", width: 100, valueFormatter: (p) => ({ paypal: "PayPal", tuago: "Tuago" }[p.value] || "") },
  {
    headerName: "Check confidence", field: "confidence", width: 160, type: "rightAligned",
    valueFormatter: (p) => (p.value === null || p.value === undefined ? "" : `${Math.round(p.value * 100)}%`),
    cellStyle: (p) => (p.value !== null && p.value < 0.75 ? { color: "#a8610c", fontWeight: 700 } : null),
  },
  { headerName: "Sponsor", field: "sponsor", width: 160 },
  { headerName: "Farmer", field: "farmer", width: 160 },
  {
    headerName: "Farmer payout", field: "payout_status", width: 220,
    cellRenderer: (p) => (p.value ? pill(p.value, PAYOUT_TEXT[p.value] || p.value) : ""),
  },
  {
    headerName: "Payout (naira)", field: "payout_naira", width: 150, type: "rightAligned",
    valueFormatter: (p) => (p.value === null || p.value === undefined ? "" : money(p.value, "NGN")),
  },
  { headerName: "Pay into", field: "payout_account", width: 200, valueGetter: (p) => (p.data.payout_account ? `${p.data.payout_bank} ${p.data.payout_account}` : "") },
  { headerName: "Paid", field: "paid_at", width: 170, valueFormatter: (p) => (p.value ? new Date(p.value).toLocaleString() : "") },
  { headerName: "Reference", field: "payment_reference", width: 220 },
];

function totals(list) {
  const sum = (currency) => list.filter((r) => r.currency === currency && r.status === "paid").reduce((a, r) => a + r.amount, 0);
  const owed = list.filter((r) => r.payout_status === "awaiting_funding").reduce((a, r) => a + r.payout_naira, 0);
  const parts = [`${list.length} tranches`];
  if (sum("USD")) parts.push(`${money(sum("USD"), "USD")} collected by PayPal`);
  if (sum("NGN")) parts.push(`${money(sum("NGN"), "NGN")} collected by Tuago`);
  if (owed) parts.push(`${money(owed, "NGN")} of farmer payouts to fund`);
  const review = list.filter((r) => r.status === "needs_review").length;
  if (review) parts.push(`${review} waiting for review`);
  return parts.join(". ") + ".";
}

function visibleRows() {
  const filter = document.getElementById("filter").value;
  if (!filter) return rows;
  if (filter === "payout") return rows.filter((r) => ["awaiting_funding", "needs_bank_details", "failed"].includes(r.payout_status));
  return rows.filter((r) => r.status === filter);
}

function refreshGrid() {
  const list = visibleRows();
  grid.setGridOption("rowData", list);
  document.getElementById("count").textContent = totals(rows);
}

async function loadLedger(keepId) {
  errorText.textContent = "";
  try {
    rows = (await call("/console/ledger")).rows;
    refreshGrid();
    if (keepId) {
      const row = rows.find((r) => r.milestone_id === keepId);
      if (row) showDetail(row);
    }
  } catch (error) {
    errorText.textContent = error.status === 401
      ? "Enter the operator key to see the ledger."
      : `The ledger could not be loaded. ${error.message}`;
    if (error.status === 401) keyInput.focus();
  }
}

function row(term, value) {
  return value === null || value === undefined || value === "" ? [] : [h("dt", {}, term), h("dd", {}, value)];
}

function showDetail(r) {
  const result = h("p", { className: "result", role: "status" });
  const act = (label, path, body, className = "button small") =>
    h("button", {
      className, type: "button",
      onclick: async (event) => {
        event.target.disabled = true;
        try {
          const out = await call(path, { method: "POST", body });
          result.textContent = `Done: ${out.status || "updated"}.`;
          await loadLedger(r.milestone_id);
        } catch (error) {
          result.textContent = error.message;
          event.target.disabled = false;
        }
      },
    }, label);

  const actions = [];
  if (r.status === "needs_review") {
    actions.push(act("Approve and collect", `/sponsorships/milestones/${r.milestone_id}/review`, { approve: true }, "button go small"));
    actions.push(act("Reject photo", `/sponsorships/milestones/${r.milestone_id}/review`, { approve: false }, "button stop small"));
  }
  if (r.status === "payment_failed") actions.push(act("Try payment again", `/sponsorships/milestones/${r.milestone_id}/retry-payment`));
  if (r.status === "awaiting_payment" && testMode) actions.push(act("Mark sponsor payment received (test)", `/sponsorships/milestones/${r.milestone_id}/simulate-payment`));
  if (r.payout_id && r.payout_status !== "paid") {
    actions.push(act(r.payout_account ? "Issue a new account to pay into" : "Issue account to pay into", `/disbursements/${r.payout_id}/funding`));
    if (r.payout_account) actions.push(act("Check with Tuago", `/disbursements/${r.payout_id}/check`));
    if (r.payout_account && testMode) actions.push(act("Mark transfer received (test)", `/disbursements/${r.payout_id}/simulate`));
  }
  if (["active", "pending_approval"].includes(r.sponsorship_status)) {
    actions.push(act("Stop sponsorship", `/sponsorships/${r.sponsorship_id}/cancel`, undefined, "button stop small"));
  }

  detail.replaceChildren(
    h("h2", {}, `${r.farm}: ${r.sequence}. ${r.stage}`),
    r.photo_url ? h("img", { src: r.photo_url, alt: `Farm photo for ${r.stage}` }) : null,
    h("dl", {},
      row("Status", STATE_TEXT[r.status]),
      row("Amount", money(r.amount, r.currency)),
      row("Sponsor", r.sponsor),
      row("Farmer", r.farmer && `${r.farmer}${r.farmer_has_bank ? "" : " (no bank account yet)"}`),
      row("What the check saw", r.verdict),
      row("Confidence", r.confidence === null ? null : `${Math.round(r.confidence * 100)}%`),
      row("Problem", r.failure_reason),
      row("Farmer payout", r.payout_status && `${money(r.payout_naira, "NGN")}, ${PAYOUT_TEXT[r.payout_status].toLowerCase()}`),
      row("Transfer to", r.payout_account && `${r.payout_bank} ${r.payout_account} (${r.payout_account_name})`),
      row("PayPal capture", r.paypal_capture_id),
      row("Sponsor page", h("a", { href: `/s/${r.reference}` }, `/s/${r.reference}`)),
    ),
    h("div", { className: "actions" }, actions.length ? actions : h("p", { className: "hint" }, "Nothing to do for this tranche.")),
    result,
  );
}

grid = agGrid.createGrid(document.getElementById("grid"), {
  theme: agGrid.themeQuartz.withParams({
    fontFamily: "inherit",
    accentColor: "#1f2a5c",
    foregroundColor: "#151b36",
    backgroundColor: "#fbfcfe",
    headerBackgroundColor: "#e2e8f5",
    borderColor: "#bcc6de",
    borderRadius: 4,
    wrapperBorderRadius: 4,
  }),
  columnDefs: columns,
  rowData: [],
  defaultColDef: { sortable: true, filter: true, resizable: true },
  getRowId: (params) => String(params.data.milestone_id),
  rowSelection: { mode: "singleRow", checkboxes: false, enableClickSelection: true },
  onSelectionChanged: (event) => {
    const [selected] = event.api.getSelectedRows();
    if (selected) showDetail(selected);
  },
  overlayNoRowsTemplate: "No tranches yet. They appear here when a sponsorship starts.",
});

document.getElementById("search").addEventListener("input", (event) => grid.setGridOption("quickFilterText", event.target.value));
document.getElementById("filter").addEventListener("change", refreshGrid);
document.getElementById("reload").addEventListener("click", () => loadLedger());
document.getElementById("export").addEventListener("click", () => grid.exportDataAsCsv({ fileName: "yieldra-ledger.csv" }));
document.getElementById("key-form").addEventListener("submit", (event) => { event.preventDefault(); });
keyInput.addEventListener("change", () => {
  sessionStorage.setItem(KEY_STORE, keyInput.value);
  loadLedger();
  loadPeople();
});

// -- add a farm photo --------------------------------------------------------
function readAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error("That file could not be read."));
    reader.readAsDataURL(file);
  });
}

const EVIDENCE_TEXT = {
  released: "Photo passed. The tranche was collected.",
  payment_requested: "Photo passed. The sponsor has been asked to pay.",
  payment_failed: "Photo passed, but the payment did not go through. See the ledger.",
  needs_review: "The check was unsure. The tranche is waiting for review.",
  rejected: "Photo did not pass. Nothing was collected.",
  duplicate_photo: "This photo has been used before. Nothing was collected.",
  nothing_pending: "No stage on this farm is waiting for a photo.",
  verification_unavailable: "The photo could not be checked. Is a model key set?",
  photo_unreadable: "The photo could not be opened.",
};

document.getElementById("evidence-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const output = document.getElementById("evidence-result");
  const button = document.getElementById("evidence-submit");
  const file = document.getElementById("evidence-file").files[0];
  if (!file) return;
  if (file.size > 5 * 1024 * 1024) { output.textContent = "Choose a photo smaller than 5 MB."; return; }
  button.disabled = true;
  output.textContent = "Checking the photo…";
  try {
    const farmId = document.getElementById("evidence-farm").value;
    const out = await call(`/sponsorships/farms/${farmId}/evidence`, {
      method: "POST", body: { photo_url: await readAsDataUrl(file) },
    });
    const seen = out.verdict ? `\nWhat the check saw: ${out.verdict.observations} Confidence ${Math.round(out.verdict.confidence * 100)}%.` : "";
    output.textContent = (EVIDENCE_TEXT[out.status] || out.status) + seen;
    await loadLedger();
  } catch (error) {
    output.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

document.getElementById("bank-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const output = document.getElementById("bank-result");
  try {
    const out = await call(`/farmers/${document.getElementById("bank-farmer").value}/payout-account`, {
      method: "POST",
      body: { bank_code: document.getElementById("bank-code").value, account_number: document.getElementById("bank-number").value.trim() },
    });
    output.textContent = `Saved: ${out.account_name}, ${out.bank_name || out.bank_code} ${out.account_number_masked}.`;
    await loadLedger();
  } catch (error) {
    output.textContent = error.message;
  }
});

async function loadPeople() {
  try {
    const [farms, farmers, banks] = await Promise.all([api("/farms"), api("/users?role=farmer"), api("/banks")]);
    document.getElementById("evidence-farm").replaceChildren(...farms.map((f) => h("option", { value: f.id }, f.name)));
    document.getElementById("bank-farmer").replaceChildren(...farmers.map((u) => h("option", { value: u.id }, u.name)));
    document.getElementById("bank-code").replaceChildren(...banks.map((b) => h("option", { value: b.code }, b.name)));
  } catch { /* the forms stay empty; the ledger error explains */ }
}

const meta = await showMode(document.getElementById("mode"));
if (meta) {
  testMode = meta.tuago !== "live";
  document.getElementById("key-form").hidden = !meta.operator_key_required;
  document.getElementById("modes").textContent =
    `PayPal: ${meta.paypal}. Tuago: ${meta.tuago}. Farmer payouts convert at ₦${meta.usd_ngn_rate.toLocaleString("en-US")} to the dollar.`;
}
loadPeople();
loadLedger();
