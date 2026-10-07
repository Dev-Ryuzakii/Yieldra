// Shared helpers for the Yieldra pages. Everything from the API is inserted as text,
// never as HTML, because names and updates are written by people and by a model.

export function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "dataset") Object.assign(node.dataset, value);
    else if (key === "style") node.setAttribute("style", value);
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "className") node.className = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === "string" ? detail : "Something went wrong. Please try again.");
    this.status = status;
  }
}

export async function api(path, { method = "GET", body, adminKey } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (adminKey) headers["X-Admin-Key"] = adminKey;
  let response;
  try {
    response = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "Could not reach Yieldra. Check your connection and try again.");
  }
  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { /* not JSON */ }
  if (!response.ok) {
    let detail = data && data.detail;
    if (Array.isArray(detail)) detail = detail.map((d) => d.msg).join(". ");
    throw new ApiError(response.status, detail);
  }
  return data;
}

const SYMBOL = { USD: "$", NGN: "₦" };

export function money(major, currency) {
  const digits = Number.isInteger(major) ? 0 : 2;
  return SYMBOL[currency] + major.toLocaleString("en-US", {
    minimumFractionDigits: digits, maximumFractionDigits: 2,
  });
}

export const STATE_TEXT = {
  locked: "Not open yet",
  awaiting_evidence: "Waiting for the farmer's photo",
  needs_review: "Photo is being checked by a person",
  awaiting_payment: "Photo passed. Payment due",
  payment_failed: "Photo passed. Payment did not go through",
  paid: "Paid",
};

// The stage strip: one segment per tranche, as wide as its share of the money.
export function strip(stages) {
  return h("ol", { className: "strip" }, stages.map((stage, index) =>
    h("li", { style: `--share:${stage.share}`, dataset: { state: stage.state } },
      h("span", { className: "n" }, index + 1),
      h("span", { className: "stage" }, stage.title),
      h("span", { className: "money" }, stage.amount),
      h("span", { className: "state" }, stage.note || STATE_TEXT[stage.state]),
    ),
  ));
}

export function capital(text) {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
}

export const SHARES = [20, 30, 30, 20];
export const SHORT_TITLES = ["Start", "Planted", "Established", "Harvest"];

// Split a total the way the server does: whole minor units, remainder to the last part.
export function splitMinor(totalMinor) {
  const parts = SHARES.map((share) => Math.floor((totalMinor * share) / 100));
  parts[parts.length - 1] += totalMinor - parts.reduce((a, b) => a + b, 0);
  return parts;
}

export async function showMode(element) {
  try {
    const meta = await api("/meta");
    const mock = [];
    if (meta.paypal === "mock") mock.push("PayPal");
    if (meta.tuago === "mock") mock.push("Tuago");
    const notes = [];
    if (mock.length) notes.push(`Demo mode: ${mock.join(" and ")} payments are simulated here, so no money moves.`);
    else if (meta.paypal === "sandbox" || meta.tuago === "test") notes.push("Test mode: payments run in the PayPal sandbox and Tuago test mode, so no real money moves.");
    if (!meta.model_ready) notes.push("No model key is set, so farm photos cannot be checked yet.");
    if (notes.length) { element.textContent = notes.join(" "); element.hidden = false; }
    return meta;
  } catch {
    return null;
  }
}

// Sponsorships started on this device, so the sponsor can find their pages again.
const STORE = "yieldra.sponsorships";

export function remembered() {
  try { return JSON.parse(localStorage.getItem(STORE) || "[]"); } catch { return []; }
}

export function remember(entry) {
  try {
    const all = remembered().filter((e) => e.reference !== entry.reference);
    all.unshift(entry);
    localStorage.setItem(STORE, JSON.stringify(all.slice(0, 20)));
  } catch { /* storage unavailable: the page still works */ }
}
