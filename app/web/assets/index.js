import { api, capital, h, money, remember, remembered, showMode, splitMinor, strip, SHARES, SHORT_TITLES } from "/assets/common.js";

const dialog = document.getElementById("sponsor");
const form = document.getElementById("sponsor-form");
const amount = document.getElementById("amount");
const errorText = document.getElementById("sponsor-error");
const submit = document.getElementById("sponsor-submit");

const RAILS = {
  paypal: { currency: "USD", label: "Total in US dollars", button: "Continue to PayPal", suggested: 100 },
  tuago: { currency: "NGN", label: "Total in naira", button: "Continue to Tuago", suggested: 100000 },
};
let limits = { paypal: { min: 10, max: 10000 }, tuago: { min: 5000, max: 5000000 } };
let farm = null;

// The example strip in the hero: what a sponsor sees after the second stage.
document.getElementById("example").append(strip([
  { title: "Seed and land", amount: "$20", share: 20, state: "paid", note: "Paid when you start" },
  { title: "Planted", amount: "$30", share: 30, state: "paid", note: "Photo passed, paid" },
  { title: "Established", amount: "$30", share: 30, state: "awaiting_evidence" },
  { title: "Harvest", amount: "$20", share: 20, state: "locked" },
]));

function rail() {
  return form.elements.rail.value;
}

function renderPreview() {
  const { currency, label, button } = RAILS[rail()];
  const bounds = limits[rail()];
  document.getElementById("amount-label").textContent = label;
  document.getElementById("amount-hint").textContent =
    `Between ${money(bounds.min, currency)} and ${money(bounds.max, currency)}.`;
  submit.textContent = button;
  const total = Number(amount.value);
  const preview = document.getElementById("preview");
  preview.replaceChildren();
  if (!(total > 0)) return;
  const parts = splitMinor(Math.round(total * 100));
  preview.append(strip(parts.map((minor, index) => ({
    title: SHORT_TITLES[index],
    amount: money(minor / 100, currency),
    share: SHARES[index],
    state: index === 0 ? "awaiting_payment" : "locked",
    note: index === 0 ? "Paid today" : "After its photo passes",
  }))));
}

function openSponsor(selected) {
  farm = selected;
  document.getElementById("sponsor-title").textContent = `Sponsor ${farm.name}`;
  document.getElementById("sponsor-sub").textContent =
    `${capital(farm.crop_type)} in ${farm.location}, farmed by ${farm.farmer_first_name}.`;
  const tuago = form.querySelector('input[value="tuago"]');
  tuago.disabled = !farm.naira_ready;
  document.getElementById("tuago-note").textContent = farm.naira_ready
    ? "Naira, through Tuago. You pay each part when it is due."
    : "Not open for this farm yet: the farmer has not added a bank account.";
  form.elements.rail.value = "paypal";
  amount.value = RAILS.paypal.suggested;
  errorText.textContent = "";
  renderPreview();
  dialog.showModal();
}

form.addEventListener("change", (event) => {
  if (event.target.name === "rail") amount.value = RAILS[rail()].suggested;
  renderPreview();
});
amount.addEventListener("input", renderPreview);
document.getElementById("sponsor-close").addEventListener("click", () => dialog.close());

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorText.textContent = "";
  const { currency } = RAILS[rail()];
  const bounds = limits[rail()];
  const total = Number(amount.value);
  const name = form.elements.name.value.trim();
  const email = form.elements.email.value.trim();
  if (!(total >= bounds.min && total <= bounds.max)) {
    errorText.textContent = `Enter a total between ${money(bounds.min, currency)} and ${money(bounds.max, currency)}.`;
    amount.focus();
    return;
  }
  if (!name) { errorText.textContent = "Enter your name."; form.elements.name.focus(); return; }
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    errorText.textContent = "Enter an email address, like ada@example.com.";
    form.elements.email.focus();
    return;
  }
  submit.disabled = true;
  try {
    const started = await api("/sponsorships/checkout", {
      method: "POST",
      body: { farm_id: farm.id, amount: total, rail: rail(), name, email },
    });
    remember({
      reference: started.sponsorship.reference,
      farm: started.sponsorship.farm_name,
      total: started.sponsorship.total,
    });
    window.location.assign(started.approve_url);
  } catch (error) {
    errorText.textContent = error.message;
    submit.disabled = false;
  }
});

function farmRow(item) {
  const stage = item.latest_stage
    ? h("p", { className: "progress" }, h("strong", {}, item.latest_stage), "Latest stage checked")
    : h("p", { className: "progress" }, h("strong", {}, "Ready to start"), "No stage checked yet");
  const sponsors = item.sponsors === 0 ? "No sponsors yet" : item.sponsors === 1 ? "1 sponsor" : `${item.sponsors} sponsors`;
  return h("li", { className: "farm" },
    item.latest_photo_url
      ? h("img", { className: "thumb", src: item.latest_photo_url, alt: `Latest photo of ${item.name}` })
      : h("div", { className: "thumb", role: "img", "aria-label": "No photo yet" }),
    h("div", {},
      h("h3", {}, item.name),
      h("p", { className: "where" }, `${capital(item.crop_type)} in ${item.location}, farmed by ${item.farmer_first_name}`),
    ),
    h("div", {}, stage, h("p", { className: "progress" }, sponsors)),
    h("div", { className: "action" },
      h("button", { className: "button", type: "button", onclick: () => openSponsor(item) }, "Sponsor this farm"),
    ),
  );
}

async function loadFarms() {
  const list = document.getElementById("farm-list");
  try {
    const farms = await api("/sponsorships/farms/overview");
    list.replaceChildren(...(farms.length
      ? farms.map(farmRow)
      : [h("li", { className: "empty" }, "No farms are listed yet. Add one from the API, or run the seed script.")]));
  } catch (error) {
    list.replaceChildren(h("li", { className: "empty" }, `Farms could not be loaded. ${error.message}`));
  }
}

function showMine() {
  const mine = remembered();
  if (!mine.length) return;
  document.getElementById("yours").hidden = false;
  document.getElementById("mine").replaceChildren(...mine.map((entry) =>
    h("li", {}, h("a", { className: "button quiet", href: `/s/${entry.reference}` }, `${entry.farm}, ${entry.total}`)),
  ));
}

const meta = await showMode(document.getElementById("mode"));
if (meta) limits = meta.limits;
showMine();
loadFarms();
