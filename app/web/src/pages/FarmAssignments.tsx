import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react';
import { ApiError, api, message } from '../shared';

interface Person { id: number; name: string; registered: boolean }
interface HistoryEvent { id: number; previous_farmer: string | null; new_farmer: string | null; actor: string | null; actor_via: string; reason: string; created_at: string | null }
interface FarmRow {
  id: number; name: string; crop_type: string; location: string; status: string; farmer: Person | null;
  photo: { open_stage: { milestone_id: number; title: string } | null; note: string };
  blockers: string[]; history: HistoryEvent[];
}
interface Farmer { id: number; name: string; email_hint: string | null; farm_count: number }
interface Overview { farms: FarmRow[]; farmers: Farmer[]; farmers_hint: string | null }
interface Saved { status: 'assigned' | 'unchanged'; detail: string; farm: FarmRow }

const VIA: Record<string, string> = { session: 'signed-in operator', operator_key: 'operator key', development: 'local development' };

export function FarmAssignments({ adminKey = '' }: { adminKey?: string }) {
  const [data, setData] = useState<Overview | null>(null);
  const [loadError, setLoadError] = useState('');
  const [query, setQuery] = useState('');
  const [farmId, setFarmId] = useState('');
  const [farmerId, setFarmerId] = useState('');
  const [reason, setReason] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const call = useCallback(<T,>(path: string, options: { method?: string; body?: unknown } = {}) => api<T>(path, { ...options, adminKey: adminKey || undefined }), [adminKey]);

  const load = useCallback(async () => {
    setLoadError('');
    try { setData(await call<Overview>('/console/farm-assignments')); }
    catch (cause) { setLoadError(cause instanceof ApiError && cause.status === 401 ? 'Sign in as an operator to manage farm assignments.' : `Farm assignments could not be loaded. ${message(cause)}`); }
  }, [call]);
  useEffect(() => { void load(); }, [load]);

  const farms = useMemo(() => {
    const term = query.trim().toLowerCase();
    const all = data?.farms || [];
    return term ? all.filter(f => `${f.name} ${f.crop_type} ${f.location} ${f.farmer?.name || ''}`.toLowerCase().includes(term)) : all;
  }, [data, query]);
  const farm = data?.farms.find(f => String(f.id) === farmId) || null;
  const farmer = data?.farmers.find(f => String(f.id) === farmerId) || null;
  const same = !!farm && !!farmer && farm.farmer?.id === farmer.id;
  const replacesRegistered = !!farm?.farmer?.registered && !same;
  const blocked = !!farm && farm.blockers.length > 0 && !same;
  const reasonOk = reason.trim().length >= 10;
  const canSave = !!farm && !!farmer && !busy && !blocked && (same || (confirmed && reasonOk));

  function reset() { setConfirmed(false); setError(''); setSuccess(''); }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!farm || !farmer || !farm.farmer) return;
    setBusy(true); setError(''); setSuccess('');
    try {
      const saved = await call<Saved>(`/console/farms/${farm.id}/assignment`, { method: 'POST', body: { farmer_id: farmer.id, expected_farmer_id: farm.farmer.id, reason, confirm: confirmed } });
      setSuccess(saved.status === 'unchanged' ? `${saved.detail} Nothing was changed.` : `Saved. ${saved.detail} They will see it on their dashboard next time it loads.`);
      setReason(''); setConfirmed(false); setFarmerId('');
      await load();
    } catch (cause) {
      setError(`Not saved. ${message(cause)}`);
      if (cause instanceof ApiError && cause.status === 409) await load();
    } finally { setBusy(false); }
  }

  return <section className="panel assign-panel" aria-labelledby="assign-heading">
    <div className="assign-head"><div><p className="eyebrow">Farm assignments</p><h2 id="assign-heading">Assign a farm to a farmer</h2></div><button type="button" className="button quiet small" onClick={() => { reset(); void load(); }}>Refresh</button></div>
    <p className="hint">Links an existing farm to a registered farmer so it appears on their dashboard and photo form. Sponsorships, payments, payout records and photo history do not move.</p>
    {loadError && <p className="error-text" role="alert">{loadError}</p>}
    {!data && !loadError && <p className="hint" role="status">Loading farms and farmers…</p>}
    {data && <form onSubmit={submit} className="assign-form" noValidate>
      {data.farms.length === 0 ? <p className="assign-empty">No farms exist yet. Farms are created separately; this panel only links existing farms.</p> : <>
        <div className="field"><label htmlFor="assign-search">Find a farm</label><input id="assign-search" type="text" placeholder="Farm, crop, place or current farmer" value={query} onChange={event => setQuery(event.target.value)} /></div>
        <div className="field"><label htmlFor="assign-farm">Farm</label><select id="assign-farm" value={farmId} onChange={event => { setFarmId(event.target.value); reset(); }}><option value="">Choose a farm ({farms.length} shown)</option>{farms.map(f => <option key={f.id} value={f.id}>{f.name} · {f.crop_type} · now {f.farmer?.name || 'unassigned'}</option>)}</select></div>
      </>}
      {farm && <div className="assign-current" aria-live="polite">
        <dl><dt>Current farmer</dt><dd>{farm.farmer ? <>{farm.farmer.name} <span className="pill" data-state={farm.farmer.registered ? 'paid' : 'locked'}>{farm.farmer.registered ? 'Registered' : 'Not signed in to Yieldra'}</span></> : 'None'}</dd>
          <dt>Location</dt><dd>{farm.crop_type} · {farm.location}</dd>
          <dt>Milestone photo</dt><dd>{farm.photo.note}</dd></dl>
        {!farm.photo.open_stage && <p className="hint">The farmer will see this farm, but the photo form stays closed until a funded stage is waiting for a photo.</p>}
      </div>}
      {data.farmers.length === 0 ? <div className="assign-empty" role="status"><strong>No registered farmers to choose from.</strong><p>{data.farmers_hint}</p></div>
        : <div className="field"><label htmlFor="assign-farmer">Registered farmer</label><select id="assign-farmer" value={farmerId} onChange={event => { setFarmerId(event.target.value); reset(); }} disabled={!farm}><option value="">{farm ? 'Choose a farmer' : 'Choose a farm first'}</option>{data.farmers.map(f => <option key={f.id} value={f.id}>{f.name}{f.email_hint ? ` (${f.email_hint})` : ''} · {f.farm_count} {f.farm_count === 1 ? 'farm' : 'farms'}</option>)}</select><p className="hint">Only farmer accounts that have signed in to Yieldra are listed.</p></div>}
      {same && <p className="result" role="status">{farm!.name} is already assigned to {farmer!.name}. Saving changes nothing.</p>}
      {blocked && <div className="assign-warning" role="alert"><strong>Reassignment blocked</strong><ul>{farm!.blockers.map(item => <li key={item}>{item}</li>)}</ul></div>}
      {farm && farmer && !same && !blocked && <>
        <div className={`assign-warning ${replacesRegistered ? '' : 'soft'}`}><strong>{replacesRegistered ? `This takes the farm away from ${farm.farmer?.name}.` : `This replaces ${farm.farmer?.name || 'the current owner'}.`}</strong><p>{replacesRegistered ? `${farm.farmer?.name} will stop seeing ${farm.name} and cannot send its photos.` : `${farm.farmer?.name || 'The current owner'} has not signed in to Yieldra.`} Earlier photos and payout records stay with the farmer who earned them.</p></div>
        <div className="field"><label htmlFor="assign-reason">Reason for the audit log</label><textarea id="assign-reason" value={reason} onChange={event => setReason(event.target.value)} minLength={10} maxLength={500} required aria-describedby="assign-reason-hint" placeholder="For example: farmer confirmed this plot with the field officer." /><p className="hint" id="assign-reason-hint">At least 10 characters. Saved with your name and the time.</p></div>
        <div className="assign-confirm"><input id="assign-confirm" type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} /> <label htmlFor="assign-confirm">I confirm assigning <strong>{farm.name}</strong> to <strong>{farmer.name}</strong>{farm.farmer ? <> instead of <strong>{farm.farmer.name}</strong></> : null}.</label></div>
      </>}
      <div className="actions"><button className={`button ${replacesRegistered ? 'stop' : ''}`} type="submit" disabled={!canSave} aria-busy={busy}>{busy ? 'Saving…' : same ? 'Keep assignment' : replacesRegistered ? 'Reassign farm' : 'Assign farm'}</button></div>
      {error && <p className="error-text" role="alert">{error}</p>}
      {success && <p className="assign-success" role="status">{success}</p>}
    </form>}
    {farm && farm.history.length > 0 && <div className="assign-history"><h3>Recent changes to {farm.name}</h3><ol>{farm.history.map(item => <li key={item.id}><span>{item.previous_farmer || 'No one'} → <strong>{item.new_farmer}</strong></span><small>{item.created_at ? new Date(item.created_at).toLocaleString() : ''} · by {item.actor || VIA[item.actor_via] || item.actor_via}</small><q>{item.reason}</q></li>)}</ol></div>}
  </section>;
}
