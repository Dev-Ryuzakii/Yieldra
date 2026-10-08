import { AllCommunityModule, ModuleRegistry, themeQuartz, type ICellRendererParams, type ColDef } from 'ag-grid-community';
import { AgGridReact } from 'ag-grid-react';
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { api, ApiError, Header, message, ModeNotice, money, STATE_TEXT, useMeta, type Currency } from '../shared';
ModuleRegistry.registerModules([AllCommunityModule]);

interface LedgerRow {
  milestone_id: number; sponsorship_id: number; reference: string; sponsorship_status: string; rail: string; currency: Currency;
  farm: string; farmer: string | null; farmer_has_bank: boolean; sponsor: string | null; sequence: number; stage: string; status: string;
  amount: number; confidence: number | null; verdict: string | null; photo_url: string | null; payment_reference: string | null;
  paypal_capture_id: string | null; failure_reason: string | null; paid_at: string | null; payout_id: number | null; payout_status: string | null;
  payout_naira: number | null; payout_bank: string | null; payout_account: string | null; payout_account_name: string | null;
}
interface Option { id: number; name: string }
interface Bank { code: string; name: string }
const KEY_STORE = 'yieldra.operatorKey';
const PAYOUT_TEXT: Record<string, string> = { needs_bank_details: "Needs farmer's bank details", awaiting_funding: 'Transfer to fund', paid: 'Paid to farmer', failed: 'Failed' };
const EVIDENCE_TEXT: Record<string, string> = {
  released: 'Photo passed. The tranche was collected.', payment_requested: 'Photo passed. The sponsor has been asked to pay.',
  payment_failed: 'Photo passed, but the payment did not go through. See the ledger.', needs_review: 'The check was unsure. The tranche is waiting for review.',
  rejected: 'Photo did not pass. Nothing was collected.', duplicate_photo: 'This photo has been used before. Nothing was collected.',
  nothing_pending: 'No stage on this farm is waiting for a photo.', verification_unavailable: 'The photo could not be checked. Is a model key set?', photo_unreadable: 'The photo could not be opened.',
};
const theme = themeQuartz.withParams({ fontFamily: 'inherit', accentColor: '#1f2a5c', foregroundColor: '#151b36', backgroundColor: '#fbfcfe', headerBackgroundColor: '#e2e8f5', borderColor: '#bcc6de', borderRadius: 4, wrapperBorderRadius: 4 });
const pill = (state: string, text: string) => <span className="pill" data-state={state}>{text}</span>;
function totals(list: LedgerRow[]) {
  const sum = (currency: Currency) => list.filter(r => r.currency === currency && r.status === 'paid').reduce((a, r) => a + r.amount, 0);
  const owed = list.filter(r => r.payout_status === 'awaiting_funding').reduce((a, r) => a + (r.payout_naira || 0), 0);
  const parts = [`${list.length} tranches`];
  if (sum('USD')) parts.push(`${money(sum('USD'), 'USD')} collected by PayPal`);
  if (sum('NGN')) parts.push(`${money(sum('NGN'), 'NGN')} collected by Tuago`);
  if (owed) parts.push(`${money(owed, 'NGN')} of farmer payouts to fund`);
  const review = list.filter(r => r.status === 'needs_review').length;
  if (review) parts.push(`${review} waiting for review`);
  return parts.join('. ') + '.';
}
function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error('That file could not be read.'));
    reader.readAsDataURL(file);
  });
}
export function Console() {
  const meta = useMeta();
  const grid = useRef<AgGridReact<LedgerRow>>(null);
  const [key, setKey] = useState(() => sessionStorage.getItem(KEY_STORE) || '');
  const [operatorSession, setOperatorSession] = useState(false);
  const [rows, setRows] = useState<LedgerRow[]>([]);
  const [selected, setSelected] = useState<LedgerRow | null>(null);
  const [filter, setFilter] = useState('');
  const [search, setSearch] = useState('');
  const [ledgerError, setLedgerError] = useState('');
  const [actionResult, setActionResult] = useState('');
  const [actionBusy, setActionBusy] = useState(false);
  const [farms, setFarms] = useState<Option[]>([]);
  const [farmers, setFarmers] = useState<Option[]>([]);
  const [banks, setBanks] = useState<Bank[]>([]);
  const [farmId, setFarmId] = useState('');
  const [farmerId, setFarmerId] = useState('');
  const [bankCode, setBankCode] = useState('');
  const [bankNumber, setBankNumber] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [evidenceResult, setEvidenceResult] = useState('');
  const [bankResult, setBankResult] = useState('');
  const [evidenceBusy, setEvidenceBusy] = useState(false);
  const call = useCallback(<T,>(path: string, options: { method?: string; body?: unknown } = {}) => api<T>(path, { ...options, adminKey: key || undefined }), [key]);
  useEffect(() => { api<{ role: string }>('/auth/me').then(profile => setOperatorSession(profile.role === 'operator')).catch(() => {}); }, []);
  const loadLedger = useCallback(async (keepId?: number) => {
    setLedgerError('');
    try {
      const result = await call<{ rows: LedgerRow[] }>('/console/ledger');
      setRows(result.rows);
      if (keepId) setSelected(result.rows.find(r => r.milestone_id === keepId) || null);
    } catch (err) {
      setLedgerError(err instanceof ApiError && err.status === 401 ? 'Enter the operator key to see the ledger.' : `The ledger could not be loaded. ${message(err)}`);
    }
  }, [call]);
  useEffect(() => { void loadLedger(); }, [loadLedger]);
  useEffect(() => {
    Promise.all([api<Option[]>('/farms'), api<Option[]>('/users?role=farmer'), api<Bank[]>('/banks')]).then(([fs, us, bs]) => {
      setFarms(fs); setFarmers(us); setBanks(bs); setFarmId(String(fs[0]?.id || '')); setFarmerId(String(us[0]?.id || '')); setBankCode(bs[0]?.code || '');
    }).catch(() => {});
  }, []);
  const visible = useMemo(() => !filter ? rows : filter === 'payout' ? rows.filter(r => ['awaiting_funding', 'needs_bank_details', 'failed'].includes(r.payout_status || '')) : rows.filter(r => r.status === filter), [rows, filter]);
  const columns = useMemo<ColDef<LedgerRow>[]>(() => [
    { headerName: 'Farm', field: 'farm', pinned: 'left', width: 190 },
    { headerName: 'Stage', field: 'stage', width: 170, valueGetter: p => p.data && `${p.data.sequence}. ${p.data.stage}` },
    { headerName: 'Status', field: 'status', width: 290, cellRenderer: (p: ICellRendererParams<LedgerRow>) => p.value ? pill(p.value, STATE_TEXT[p.value] || p.value) : '', filterValueGetter: p => STATE_TEXT[p.data?.status || ''] },
    { headerName: 'Amount', field: 'amount', width: 130, type: 'rightAligned', valueFormatter: p => p.value == null ? '' : money(p.value, p.data!.currency) },
    { headerName: 'Rail', field: 'rail', width: 100, valueFormatter: p => p.value === 'paypal' ? 'PayPal' : p.value === 'tuago' ? 'Tuago' : '' },
    { headerName: 'Check confidence', field: 'confidence', width: 160, type: 'rightAligned', valueFormatter: p => p.value == null ? '' : `${Math.round(p.value * 100)}%`, cellStyle: p => p.value != null && p.value < 0.75 ? { color: '#a8610c', fontWeight: 700 } : undefined },
    { headerName: 'Sponsor', field: 'sponsor', width: 160 }, { headerName: 'Farmer', field: 'farmer', width: 160 },
    { headerName: 'Farmer payout', field: 'payout_status', width: 220, cellRenderer: (p: ICellRendererParams<LedgerRow>) => p.value ? pill(p.value, PAYOUT_TEXT[p.value] || p.value) : '' },
    { headerName: 'Payout (naira)', field: 'payout_naira', width: 150, type: 'rightAligned', valueFormatter: p => p.value == null ? '' : money(p.value, 'NGN') },
    { headerName: 'Pay into', field: 'payout_account', width: 200, valueGetter: p => p.data?.payout_account ? `${p.data.payout_bank} ${p.data.payout_account}` : '' },
    { headerName: 'Paid', field: 'paid_at', width: 170, valueFormatter: p => p.value ? new Date(p.value).toLocaleString() : '' },
    { headerName: 'Reference', field: 'payment_reference', width: 220 },
  ], []);
  async function action(label: string, path: string, body?: unknown) {
    setActionBusy(true); setActionResult('');
    try { const out = await call<{ status?: string }>(path, { method: 'POST', body }); setActionResult(`Done: ${out?.status || 'updated'}.`); await loadLedger(selected?.milestone_id); }
    catch (err) { setActionResult(message(err)); }
    finally { setActionBusy(false); }
  }
  async function submitEvidence(event: FormEvent) {
    event.preventDefault(); if (!file || !farmId) return;
    if (file.size > 5 * 1024 * 1024) { setEvidenceResult('Choose a photo smaller than 5 MB.'); return; }
    setEvidenceBusy(true); setEvidenceResult('Checking the photo…');
    try {
      const out = await call<{ status: string; verdict?: { observations: string; confidence: number } }>(`/sponsorships/farms/${farmId}/evidence`, { method: 'POST', body: { photo_url: await readAsDataUrl(file) } });
      setEvidenceResult((EVIDENCE_TEXT[out.status] || out.status) + (out.verdict ? `\nWhat the check saw: ${out.verdict.observations} Confidence ${Math.round(out.verdict.confidence * 100)}%.` : ''));
      await loadLedger();
    } catch (err) { setEvidenceResult(message(err)); } finally { setEvidenceBusy(false); }
  }
  async function submitBank(event: FormEvent) {
    event.preventDefault();
    try {
      const out = await call<{ account_name: string; bank_name?: string; bank_code: string; account_number_masked: string }>(`/farmers/${farmerId}/payout-account`, { method: 'POST', body: { bank_code: bankCode, account_number: bankNumber.trim() } });
      setBankResult(`Saved: ${out.account_name}, ${out.bank_name || out.bank_code} ${out.account_number_masked}.`); await loadLedger();
    } catch (err) { setBankResult(message(err)); }
  }
  const actions: { label: string; path: string; body?: unknown; className?: string }[] = [];
  if (selected?.status === 'needs_review') { actions.push({ label: 'Approve and collect', path: `/sponsorships/milestones/${selected.milestone_id}/review`, body: { approve: true }, className: 'go' }, { label: 'Reject photo', path: `/sponsorships/milestones/${selected.milestone_id}/review`, body: { approve: false }, className: 'stop' }); }
  if (selected?.status === 'payment_failed') actions.push({ label: 'Try payment again', path: `/sponsorships/milestones/${selected.milestone_id}/retry-payment` });
  if (selected?.status === 'awaiting_payment' && meta && meta.tuago !== 'live') actions.push({ label: 'Mark sponsor payment received (test)', path: `/sponsorships/milestones/${selected.milestone_id}/simulate-payment` });
  if (selected?.payout_id && selected.payout_status !== 'paid') {
    actions.push({ label: selected.payout_account ? 'Issue a new account to pay into' : 'Issue account to pay into', path: `/disbursements/${selected.payout_id}/funding` });
    if (selected.payout_account) actions.push({ label: 'Check with Tuago', path: `/disbursements/${selected.payout_id}/check` });
    if (selected.payout_account && meta && meta.tuago !== 'live') actions.push({ label: 'Mark transfer received (test)', path: `/disbursements/${selected.payout_id}/simulate` });
  }
  if (selected && ['active', 'pending_approval'].includes(selected.sponsorship_status)) actions.push({ label: 'Stop sponsorship', path: `/sponsorships/${selected.sponsorship_id}/cancel`, className: 'stop' });
  const details: [string, string | number | null | undefined][] = selected ? [
    ['Status', STATE_TEXT[selected.status]], ['Amount', money(selected.amount, selected.currency)], ['Sponsor', selected.sponsor],
    ['Farmer', selected.farmer && `${selected.farmer}${selected.farmer_has_bank ? '' : ' (no bank account yet)'}`],
    ['What the check saw', selected.verdict], ['Confidence', selected.confidence == null ? null : `${Math.round(selected.confidence * 100)}%`],
    ['Problem', selected.failure_reason], ['Farmer payout', selected.payout_status && `${money(selected.payout_naira || 0, 'NGN')}, ${(PAYOUT_TEXT[selected.payout_status] || selected.payout_status).toLowerCase()}`],
    ['Transfer to', selected.payout_account && `${selected.payout_bank} ${selected.payout_account} (${selected.payout_account_name})`], ['PayPal capture', selected.paypal_capture_id],
  ] : [];
  return <><Header consolePage /><main className="wrap"><ModeNotice meta={meta} /><div className="console-head"><div><p className="eyebrow">Operations overview</p><h1>Operator console</h1><p className="modes">{meta && `PayPal: ${meta.paypal}. Tuago: ${meta.tuago}. Farmer payouts convert at ₦${meta.usd_ngn_rate.toLocaleString('en-US')} to the dollar.`}</p></div>{meta?.operator_key_required && !operatorSession && <div className="key"><div className="field"><label htmlFor="key">Operator key</label><input id="key" type="password" autoComplete="off" placeholder="SECRET_KEY" value={key} onChange={event => { setKey(event.target.value); sessionStorage.setItem(KEY_STORE, event.target.value); }} /></div></div>}</div>
    <div className="console-stats"><div><span>All tranches</span><strong>{rows.length}</strong><small>Across every sponsorship</small></div><div><span>Needs review</span><strong>{rows.filter(row => row.status === 'needs_review').length}</strong><small>Photo checks to decide</small></div><div><span>Payments due</span><strong>{rows.filter(row => row.status === 'awaiting_payment' || row.status === 'payment_failed').length}</strong><small>Awaiting or retrying</small></div><div><span>Farmer payouts</span><strong>{rows.filter(row => row.payout_status === 'awaiting_funding' || row.payout_status === 'needs_bank_details').length}</strong><small>Require action</small></div></div>
    <div className="toolbar"><input type="text" placeholder="Search farm, sponsor, stage" aria-label="Search the ledger" value={search} onChange={event => setSearch(event.target.value)} /><select aria-label="Show" className="w-auto" value={filter} onChange={event => setFilter(event.target.value)}><option value="">All tranches</option><option value="needs_review">Needs review</option><option value="payment_failed">Payment failed</option><option value="awaiting_payment">Awaiting sponsor payment</option><option value="payout">Farmer payout to fund</option></select><button className="button quiet small" onClick={() => void loadLedger()}>Refresh</button><button className="button quiet small" onClick={() => grid.current?.api.exportDataAsCsv({ fileName: 'yieldra-ledger.csv' })}>Download CSV</button><span className="count">{totals(rows)}</span></div>
    <p className="error-text" role="alert">{ledgerError}</p><div id="grid"><AgGridReact<LedgerRow> ref={grid} theme={theme} columnDefs={columns} rowData={visible} defaultColDef={{ sortable: true, filter: true, resizable: true }} getRowId={p => String(p.data.milestone_id)} rowSelection={{ mode: 'singleRow', checkboxes: false, enableClickSelection: true }} onSelectionChanged={event => { const row = event.api.getSelectedRows()[0]; if (row) { setSelected(row); setActionResult(''); } }} quickFilterText={search} overlayNoRowsTemplate="No tranches yet. They appear here when a sponsorship starts." /></div>
    <div className="console-cols"><section className="panel" aria-live="polite">{selected ? <><h2>{selected.farm}: {selected.sequence}. {selected.stage}</h2>{selected.photo_url && <img src={selected.photo_url} alt={`Farm photo for ${selected.stage}`} />}<dl>{details.filter(([, value]) => value !== null && value !== undefined && value !== '').map(([term, value]) => <FragmentRow key={term} term={term} value={value!} />)}<dt>Sponsor page</dt><dd><a href={`/s/${encodeURIComponent(selected.reference)}`}>/s/{selected.reference}</a></dd></dl><div className="actions">{actions.length ? actions.map(item => <button key={item.label} className={`button small ${item.className || ''}`} disabled={actionBusy} onClick={() => void action(item.label, item.path, item.body)}>{item.label}</button>) : <p className="hint">Nothing to do for this tranche.</p>}</div><p className="result" role="status">{actionResult}</p></> : <><h2>Tranche</h2><p className="hint">Select a row to see its photo, verdict and actions.</p></>}</section>
      <section className="panel"><h2>Add a farm photo</h2><p className="hint mb-4">Farmers send photos in chat with the caption PROOF. Use this to submit one for them.</p><form onSubmit={submitEvidence}><div className="field"><label htmlFor="evidence-farm">Farm</label><select id="evidence-farm" required value={farmId} onChange={event => setFarmId(event.target.value)}>{farms.map(f => <option key={f.id} value={f.id}>{f.name}</option>)}</select></div><div className="field"><label htmlFor="evidence-file">Photo</label><input id="evidence-file" type="file" accept="image/jpeg,image/png,image/webp" required onChange={event => setFile(event.target.files?.[0] || null)} /></div><button className="button" type="submit" disabled={evidenceBusy}>Check photo</button></form><p className="result" role="status">{evidenceResult}</p>
        <h2 className="mt-8">Farmer bank account</h2><form onSubmit={submitBank}><div className="field"><label htmlFor="bank-farmer">Farmer</label><select id="bank-farmer" required value={farmerId} onChange={event => setFarmerId(event.target.value)}>{farmers.map(u => <option key={u.id} value={u.id}>{u.name}</option>)}</select></div><div className="field"><label htmlFor="bank-code">Bank</label><select id="bank-code" required value={bankCode} onChange={event => setBankCode(event.target.value)}>{banks.map(b => <option key={b.code} value={b.code}>{b.name}</option>)}</select></div><div className="field"><label htmlFor="bank-number">Account number</label><input id="bank-number" type="text" inputMode="numeric" pattern="\d{10}" maxLength={10} required value={bankNumber} onChange={event => setBankNumber(event.target.value)} /></div><button className="button" type="submit">Verify and save account</button></form><p className="result" role="status">{bankResult}</p>
      </section></div></main></>;
}
function FragmentRow({ term, value }: { term: string; value: string | number }) { return <><dt>{term}</dt><dd>{value}</dd></>; }
