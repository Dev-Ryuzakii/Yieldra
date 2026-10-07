import { api, capital, h, remember, showMode, strip, STATE_TEXT } from "/assets/common.js";

const reference = decodeURIComponent(window.location.pathname.split("/").filter(Boolean).pop());
const head = document.getElementById("head");
const stages = document.getElementById("stages");
const foot = document.getElementById("foot");
const confirmDialog = document.getElementById("confirm");
let current = null;

const SPONSORSHIP_TEXT = {
  pending_approval: "Not started yet: the first payment has not been made.",
  cancelled: "This sponsorship has been stopped. Nothing more will be collected.",
  completed: "Complete. Every stage was checked and paid. Thank you.",
};

function date(value) {
  if (!value) return "";
  return new Date(value).toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" });
}

function summary(s) {
  if (SPONSORSHIP_TEXT[s.status]) return SPONSORSHIP_TEXT[s.status];
  const next = s.next_milestone;
  const tail = next ? ` Next: ${next.title.toLowerCase()}. ${STATE_TEXT[next.status]}.` : "";
  return `${s.paid} of ${s.total} has been released to the farm.${tail}`;
}

function payoutLine(payout) {
  if (!payout) return null;
  const text = {
    paid: `The farmer has been paid ${payout.amount}.`,
    awaiting_funding: `${payout.amount} is being paid to the farmer's bank account.`,
    needs_bank_details: `${payout.amount} is held for the farmer until they add a bank account.`,
    failed: `The farmer's payment of ${payout.amount} did not go through and is being retried.`,
  }[payout.status];
  return text ? h("p", { className: "detail small" }, text) : null;
}

function stageRow(s, m) {
  const body = h("div", {},
    h("h3", {}, m.title),
    h("p", { className: "amount" }, m.amount),
    h("p", { className: "status", dataset: { state: m.status } },
      m.status === "paid" && m.paid_at ? `Paid on ${date(m.paid_at)}` : STATE_TEXT[m.status]),
  );
  if (m.status === "awaiting_evidence" || m.status === "locked") {
    if (m.evidence_required) {
      body.append(h("p", { className: "detail small" }, `The photo must show: ${m.evidence_required}`));
    }
  }
  // Updates are also sent by chat, where they start with the sender's name.
  if (m.sponsor_update) body.append(h("p", { className: "quote" }, m.sponsor_update.replace(/^Yieldra:\s*/, "")));
  if (m.verdict_summary && m.status !== "awaiting_evidence") {
    const sure = m.verdict_confidence !== null ? ` Confidence ${Math.round(m.verdict_confidence * 100)}%.` : "";
    body.append(h("p", { className: "detail small" }, `What the check saw: ${m.verdict_summary}${sure}`));
  }
  const payout = payoutLine(m.farmer_payout);
  if (payout) body.append(payout);
  if (s.rail === "tuago" && m.status === "paid") {
    body.append(h("p", { className: "detail small" }, "Paid through Tuago and settled to the farmer's bank account."));
  }
  if (m.payment_url && s.status !== "cancelled") {
    body.append(h("a", { className: "button go", href: m.payment_url }, `Pay ${m.amount}`));
  }
  if (m.status === "payment_failed") {
    body.append(h("p", { className: "detail small" },
      s.rail === "paypal"
        ? "PayPal did not accept the charge. Check your PayPal account; we will try again."
        : "The payment request could not be created. We will try again."));
  }
  const photo = m.evidence_photo_url && m.status !== "awaiting_evidence"
    ? h("figure", {},
        h("img", { src: m.evidence_photo_url, alt: `Farm photo for the stage: ${m.title}`, loading: "lazy" }),
        h("figcaption", {}, m.verified_at ? `Sent by the farmer, checked ${date(m.verified_at)}` : "Sent by the farmer"))
    : null;
  return h("li", { className: "stage-row" }, h("span", { className: "n", "aria-hidden": "true" }, m.sequence), body, photo);
}

function render(s) {
  current = s;
  document.title = `${s.farm_name} · Yieldra`;
  remember({ reference: s.reference, farm: s.farm_name, total: s.total });
  head.replaceChildren(
    h("h1", {}, s.farm_name),
    h("p", { className: "where" }, `${capital(s.crop_type)} in ${s.location}. Sponsored for ${s.total} by ${s.rail === "paypal" ? "PayPal" : "bank transfer"}.`),
    h("p", { className: "summary" }, summary(s)),
    strip(s.milestones.map((m) => ({ title: m.title, amount: m.amount, share: m.amount_minor, state: m.status }))),
  );
  stages.replaceChildren(...s.milestones.map((m) => stageRow(s, m)));
  const canStop = s.status === "active" || s.status === "pending_approval";
  foot.replaceChildren(
    canStop ? h("button", { className: "button stop", type: "button", onclick: askStop }, "Stop this sponsorship") : null,
    h("a", { className: "button quiet", href: "/#farms" }, "Sponsor another farm"),
  );
}

function askStop() {
  document.getElementById("confirm-text").textContent = current.rail === "paypal"
    ? `${current.paid} already paid stays with the farmer. Your saved PayPal account is deleted and you are not charged again.`
    : `${current.paid} already paid stays with the farmer. No further payments are requested.`;
  document.getElementById("confirm-error").textContent = "";
  confirmDialog.showModal();
}

document.getElementById("confirm-no").addEventListener("click", () => confirmDialog.close());
document.getElementById("confirm-yes").addEventListener("click", async () => {
  try {
    render(await api(`/sponsorships/ref/${encodeURIComponent(reference)}/cancel`, { method: "POST" }));
    confirmDialog.close();
  } catch (error) {
    document.getElementById("confirm-error").textContent = error.message;
  }
});

async function load({ recheck = false } = {}) {
  try {
    const path = `/sponsorships/ref/${encodeURIComponent(reference)}`;
    render(recheck ? await api(`${path}/refresh`, { method: "POST" }) : await api(path));
    return true;
  } catch (error) {
    if (!current) {
      head.replaceChildren(
        h("h1", {}, error.status === 404 ? "Sponsorship not found" : "Could not load this page"),
        h("p", { className: "summary" }, error.status === 404
          ? "Check the link you were given. Each sponsorship has its own private address."
          : error.message),
      );
    }
    return false;
  }
}

showMode(document.getElementById("mode"));
// On arrival, ask the payment provider for the latest word; then keep the page current.
await load({ recheck: true });
setInterval(() => {
  if (document.visibilityState !== "visible" || !current) return;
  const waitingOnPayment = current.milestones.some((m) => m.status === "awaiting_payment");
  load({ recheck: waitingOnPayment });
}, 15000);
