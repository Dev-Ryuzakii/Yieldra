import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { ApiError, api, apiBlob, apiForm, message } from '../shared';

interface Review { action: string; rationale: string; actor_role: string; created_at: string | null }
export interface PhotoCheck {
  id: number; farm_id: number; farm_name?: string; milestone_title: string; stage_key: string;
  evidence_required: string; status: string; superseded: boolean; provider_mode: string | null;
  model: string | null; upstream_provider: string | null; analyzed_at: string | null;
  observations: string[]; issues: string[]; limitations: string[]; recommended_disposition: string | null;
  model_reported_confidence: number | null; confidence_label: string | null; uncertainty_notes: string;
  visible_text_notes: string; visible_text_label: string; failure_code: string | null; photo_url: string;
  disclaimer: string; payment_effect: string; human_review_required: boolean; reviews: Review[];
}
interface OpenStage { milestone_id: number; key: string; title: string; evidence_required: string }
interface FarmChecks {
  farm_id: number; farm_name: string; crop_type: string; open_stage: OpenStage | null;
  checks: PhotoCheck[]; provider_mode: string; model: string; legacy_auto_charge: boolean;
}
interface FarmOption { id: number; name: string }
const MODE_TEXT: Record<string, string> = {
  live: 'Live Cencori',
  mock: 'Mocked demo — not a live model',
  blocked: 'Live AI blocked — this is not a successful check',
};
const STATUS_TEXT: Record<string, string> = {
  pending: 'Analysis pending',
  needs_review: 'Needs human review',
  approved: 'Reviewed: accepted',
  rejected: 'Reviewed: rejected',
  new_photo_requested: 'Reviewed: new photo requested',
  superseded: 'Replaced by a newer photo',
};

function badge(mode: string | null) {
  const key = mode || 'pending';
  return <span className={`provider-badge provider-${key}`}>{MODE_TEXT[key] || 'Analysis pending'}</span>;
}

export function PhotoChecks({ mode, farms, adminKey = '' }: { mode: 'farmer' | 'operator'; farms: FarmOption[]; adminKey?: string }) {
  const [selectedFarm, setSelectedFarm] = useState(String(farms[0]?.id || ''));
  const [detail, setDetail] = useState<FarmChecks | null>(null);
  const [queue, setQueue] = useState<PhotoCheck[]>([]);
  const [selected, setSelected] = useState<PhotoCheck | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [rationale, setRationale] = useState('');
  const [reviewError, setReviewError] = useState('');
  const call = useCallback(<T,>(path: string, options: { method?: string; body?: unknown } = {}) => api<T>(path, { ...options, adminKey: adminKey || undefined }), [adminKey]);

  const load = useCallback(async (farmId = selectedFarm) => {
    if (mode === 'operator') {
      const result = await call<{ checks: PhotoCheck[] }>('/console/photo-checks');
      setQueue(result.checks);
    }
    if (!farmId) { setDetail(null); return; }
    setDetail(await call<FarmChecks>(`/farms/${farmId}/photo-checks`));
  }, [call, mode, selectedFarm]);

  const farmKey = farms.map(farm => farm.id).join(',');
  useEffect(() => {
    setSelectedFarm(current => farms.some(farm => String(farm.id) === current) ? current : String(farms[0]?.id || ''));
  }, [farmKey]);
  useEffect(() => { load().catch(cause => setError(message(cause))); }, [load]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file || !selectedFarm) return;
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) { setError('Choose a JPEG, PNG, or WebP photo.'); return; }
    if (file.size > 5 * 1024 * 1024) { setError('Choose a photo smaller than 5 MB.'); return; }
    setBusy(true); setError(''); setNotice('Analysis pending. The photo has not been approved.');
    try {
      const form = new FormData();
      form.append('file', file);
      const check = await apiForm<PhotoCheck>(`/farms/${selectedFarm}/photo-checks`, form, adminKey || undefined);
      setSelected(check);
      setNotice(check.status === 'pending' ? 'Analysis pending.' : 'Analysis finished. A person still has to review it.');
      await load();
    } catch (cause) {
      setNotice('');
      setError(cause instanceof ApiError ? message(cause) : 'The photo could not be submitted.');
    } finally { setBusy(false); }
  }

  async function review(action: string) {
    if (!selected) return;
    setReviewError('');
    try {
      const updated = await call<PhotoCheck>(`/photo-checks/${selected.id}/review`, { method: 'POST', body: { action, rationale } });
      setSelected(updated);
      setNotice('Review recorded. No PayPal charge and no farmer payout were created.');
      await load();
    } catch (cause) { setReviewError(message(cause)); }
  }

  const shown = selected || detail?.checks.find(item => !item.superseded) || null;
  return <section className="photo-checks panel">
    <div className="photo-checks-head"><div><p className="eyebrow">AI photo check</p><h2>{mode === 'farmer' ? 'Send a milestone photo' : 'AI photo review'}</h2></div>{detail && badge(detail.provider_mode)}</div>
    <p className="disclaimer">AI-assisted; human review required. A recommendation does not charge a sponsor or pay a farmer.</p>
    {detail?.legacy_auto_charge && <p className="hint">Older Telegram photos captioned PROOF, and the legacy photo form, can still collect a PayPal tranche. This review does not.</p>}
    {error && <p className="error-text" role="alert">{error}</p>}
    {notice && <p className="result" role="status">{notice}</p>}
    <form onSubmit={submit}>
      <div className="field"><label htmlFor={`photo-farm-${mode}`}>Farm</label><select id={`photo-farm-${mode}`} value={selectedFarm} onChange={event => { setSelectedFarm(event.target.value); setSelected(null); }}>{farms.map(farm => <option key={farm.id} value={farm.id}>{farm.name}</option>)}</select></div>
      {detail?.open_stage ? <p className="hint">Current stage: {detail.open_stage.title}. The photo is compared with this requirement: {detail.open_stage.evidence_required}</p> : <p className="hint">No stage on this farm is waiting for a photo.</p>}
      <div className="field"><label htmlFor={`photo-file-${mode}`}>Photo</label><input id={`photo-file-${mode}`} type="file" accept="image/jpeg,image/png,image/webp" onChange={event => setFile(event.target.files?.[0] || null)} /></div>
      <button className="button" type="submit" disabled={busy || !detail?.open_stage}>{busy ? 'Analysis pending…' : 'Submit for analysis'}</button>
    </form>
    {shown && <CheckDetail check={shown} adminKey={adminKey} />}
    {mode === 'operator' && <div className="review-box">
      <h3>Queue</h3>
      {queue.length === 0 ? <p className="hint">No photo checks yet.</p> : <ul className="check-queue">{queue.map(item => <li key={item.id}><button type="button" className="text-link" onClick={() => { setSelected(item); setReviewError(''); }}>{item.farm_name}: {item.milestone_title}</button> <span className="pill" data-state={item.status}>{STATUS_TEXT[item.status] || item.status}</span></li>)}</ul>}
      {selected?.status === 'needs_review' && !selected.superseded && <>
        <label htmlFor="review-rationale">Review rationale</label>
        <textarea id="review-rationale" value={rationale} onChange={event => setRationale(event.target.value)} minLength={15} maxLength={1000} required placeholder="Say what you compared with the stage requirement. This does not move money." />
        <div className="actions">
          <button type="button" className="button small" disabled={rationale.trim().length < 15} onClick={() => void review('approve')}>Approve review</button>
          <button type="button" className="button small stop" disabled={rationale.trim().length < 15} onClick={() => void review('reject')}>Reject</button>
          <button type="button" className="button small quiet" disabled={rationale.trim().length < 15} onClick={() => void review('request_new_photo')}>Request another photo</button>
        </div>
        <p className="hint">These actions are audited and do not charge PayPal or pay the farmer.</p>
        {reviewError && <p className="error-text" role="alert">{reviewError}</p>}
      </>}
      {selected && selected.reviews.length > 0 && <dl>{selected.reviews.map((item, index) => <span key={index}><dt>{item.action}</dt><dd>{item.rationale}</dd></span>)}</dl>}
    </div>}
  </section>;
}

function CheckDetail({ check, adminKey }: { check: PhotoCheck; adminKey: string }) {
  const [src, setSrc] = useState('');
  useEffect(() => {
    let url = '';
    let ignore = false;
    apiBlob(check.photo_url, adminKey || undefined).then(blob => { if (!ignore) { url = URL.createObjectURL(blob); setSrc(url); } }).catch(() => { if (!ignore) setSrc(''); });
    return () => { ignore = true; if (url) URL.revokeObjectURL(url); };
  }, [adminKey, check.id, check.photo_url]);
  return <article className="check-detail" data-status={check.status} data-provider-mode={check.provider_mode || 'pending'} aria-live="polite">
    <div className="check-detail-top">{badge(check.provider_mode)}<span className="pill" data-state={check.status}>{STATUS_TEXT[check.status] || check.status}</span></div>
    <h3>{check.milestone_title}</h3>
    <p className="disclaimer">{check.disclaimer}</p>
    {src && <img src={src} alt={`Submitted photo for ${check.milestone_title}`} />}
    {check.status === 'pending' ? <p>Analysis pending. Nothing has been approved.</p> : <>
      <h4>What the check reported</h4>
      {check.observations.length ? <ul>{check.observations.map(item => <li key={item}>{item}</li>)}</ul> : <p className="hint">No crop observations were accepted.</p>}
      {check.issues.length > 0 && <><h4>Issues</h4><ul>{check.issues.map(item => <li key={item}>{item}</li>)}</ul></>}
      <p><strong>Recommended outcome: </strong>{(check.recommended_disposition || 'review').replaceAll('_', ' ')}. A person decides.</p>
      {check.model_reported_confidence !== null && <p>{check.confidence_label} Value reported by the model: {check.model_reported_confidence.toFixed(2)}.</p>}
      {check.uncertainty_notes && <p>{check.uncertainty_notes}</p>}
      {check.visible_text_notes && <p><strong>Text in the photo: </strong>{check.visible_text_notes} {check.visible_text_label}</p>}
      <h4>What this photo cannot prove</h4>
      <ul className="limitations">{check.limitations.map(item => <li key={item}>{item}</li>)}</ul>
      <p className="hint">Provider {check.upstream_provider || 'cencori'}{check.model ? `, model ${check.model}` : ''}{check.analyzed_at ? `, analyzed ${new Date(check.analyzed_at).toLocaleString()}` : ''}. Evidence check {check.id}.</p>
    </>}
  </article>;
}
