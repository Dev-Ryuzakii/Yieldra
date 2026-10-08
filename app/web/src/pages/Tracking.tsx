import { motion, useReducedMotion } from 'motion/react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError, capital, Header, message, ModeNotice, remember, STATE_TEXT, Strip, useMeta, type Rail } from '../shared';

interface Payout { status: string; amount: string }
interface Milestone { sequence: number; title: string; amount: string; amount_minor: number; status: string; paid_at: string | null; evidence_required: string | null; sponsor_update: string | null; verdict_summary: string | null; verdict_confidence: number | null; farmer_payout: Payout | null; payment_url: string | null; evidence_photo_url: string | null; verified_at: string | null }
interface Sponsorship { reference: string; farm_name: string; crop_type: string; location: string; total: string; paid: string; rail: Rail; status: string; next_milestone: { title: string; status: string } | null; milestones: Milestone[] }
const SPONSORSHIP_TEXT: Record<string, string> = { pending_approval: 'Not started yet: the first payment has not been made.', cancelled: 'This sponsorship has been stopped. Nothing more will be collected.', completed: 'Complete. Every stage was checked and paid. Thank you.' };
const payoutText: Record<string, (amount: string) => string> = {
  paid: amount => `The farmer has been paid ${amount}.`, pending_manual: amount => `${amount} is recorded for the farmer. Payout is pending.`, awaiting_funding: amount => `${amount} is awaiting settlement.`,
  needs_bank_details: amount => `${amount} is held for the farmer until they add a bank account.`, failed: amount => `The farmer's payment of ${amount} did not go through and is being retried.`,
};
const date = (value: string) => new Date(value).toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' });
function summary(s: Sponsorship) {
  if (SPONSORSHIP_TEXT[s.status]) return SPONSORSHIP_TEXT[s.status];
  const next = s.next_milestone;
  return `${s.paid} of ${s.total} has been collected.${next ? ` Next: ${next.title.toLowerCase()}. ${STATE_TEXT[next.status]}.` : ''}`;
}
function Stage({ milestone: m }: { milestone: Milestone }) {
  const reduced = useReducedMotion();
  const payout = m.farmer_payout && payoutText[m.farmer_payout.status]?.(m.farmer_payout.amount);
  return <motion.li className="stage-row" initial={reduced ? false : { opacity: 0, y: 12 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }}><span className="n" aria-hidden="true">{m.sequence}</span><div><h3>{m.title}</h3><p className="amount">{m.amount}</p><p className="status" data-state={m.status}>{m.status === 'paid' && m.paid_at ? `Paid on ${date(m.paid_at)}` : STATE_TEXT[m.status] || m.status}</p>
    {['awaiting_evidence', 'locked'].includes(m.status) && m.evidence_required && <p className="detail small">The photo must show: {m.evidence_required}</p>}
    {m.sponsor_update && <p className="quote">{m.sponsor_update.replace(/^Yieldra:\s*/, '')}</p>}
    {m.verdict_summary && m.status !== 'awaiting_evidence' && <p className="detail small">What the check saw: {m.verdict_summary}{m.verdict_confidence !== null && ` Confidence ${Math.round(m.verdict_confidence * 100)}%.`}</p>}
    {payout && <p className="detail small">{payout}</p>}
    {m.status === 'payment_failed' && <p className="detail small">PayPal did not accept the charge. Check your PayPal account; we will try again.</p>}
  </div>{m.evidence_photo_url && m.status !== 'awaiting_evidence' && <figure><img src={m.evidence_photo_url} alt={`Farm photo for the stage: ${m.title}`} loading="lazy" /><figcaption>{m.verified_at ? `Sent by the farmer, checked ${date(m.verified_at)}` : 'Sent by the farmer'}</figcaption></figure>}</motion.li>;
}
export function Tracking() {
  const meta = useMeta();
  const reference = decodeURIComponent(window.location.pathname.split('/').filter(Boolean).pop() || '');
  const [current, setCurrent] = useState<Sponsorship | null>(null);
  const [error, setError] = useState('');
  const [status, setStatus] = useState(0);
  const [confirmError, setConfirmError] = useState('');
  const [stopping, setStopping] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const currentRef = useRef<Sponsorship | null>(null);
  const load = useCallback(async (recheck: boolean) => {
    const path = `/sponsorships/ref/${encodeURIComponent(reference)}`;
    try {
      const result = await api<Sponsorship>(recheck ? `${path}/refresh` : path, { method: recheck ? 'POST' : 'GET' });
      currentRef.current = result; setCurrent(result); setError('');
      document.title = `${result.farm_name} · Yieldra`;
      remember({ reference: result.reference, farm: result.farm_name, total: result.total });
    } catch (err) { if (err instanceof ApiError && err.status === 401) { window.location.assign(`/auth?next=${encodeURIComponent(window.location.pathname)}`); return; } if (!currentRef.current) { setError(message(err)); setStatus(err instanceof ApiError ? err.status : 0); } }
  }, [reference]);
  useEffect(() => {
    void load(true);
    const timer = window.setInterval(() => {
      if (document.visibilityState !== 'visible' || !currentRef.current) return;
      void load(currentRef.current.milestones.some(m => m.status === 'awaiting_payment'));
    }, 15000);
    return () => window.clearInterval(timer);
  }, [load]);
  async function stop() {
    setStopping(true); setConfirmError('');
    try {
      const result = await api<Sponsorship>(`/sponsorships/ref/${encodeURIComponent(reference)}/cancel`, { method: 'POST' });
      currentRef.current = result; setCurrent(result); dialog.current?.close();
    } catch (err) { setConfirmError(message(err)); } finally { setStopping(false); }
  }
  return <><Header /><main className="wrap"><ModeNotice meta={meta} /><section className="track-head" aria-live="polite">{current ? <><p className="eyebrow">Your farm journey</p><h1>{current.farm_name}</h1><p className="where">{capital(current.crop_type)} in {current.location}. Sponsored for {current.total} by {current.rail === 'paypal' ? 'PayPal' : 'bank transfer'}.</p><p className="summary">{summary(current)}</p><Strip stages={current.milestones.map(m => ({ title: m.title, amount: m.amount, share: m.amount_minor, state: m.status }))} /></> : error ? <><h1>{status === 404 ? 'Sponsorship not found' : 'Could not load this page'}</h1><p className="summary">{status === 404 ? 'Check the link you were given. Each sponsorship has its own private address.' : error}</p></> : <p className="empty">Loading your sponsorship…</p>}</section>
    {current && <><ol className="stages">{current.milestones.map(m => <Stage key={m.sequence} milestone={m} />)}</ol><div className="track-foot">{['active', 'pending_approval'].includes(current.status) && <button className="button stop" onClick={() => { setConfirmError(''); dialog.current?.showModal(); }}>Stop this sponsorship</button>}<a className="button quiet" href="/#farms">Sponsor another farm</a></div></>}
  </main><dialog ref={dialog} aria-labelledby="confirm-title"><div className="dialog-body"><h2 id="confirm-title">Stop this sponsorship?</h2><p className="sub">{current && `${current.paid} already collected remains recorded. Your saved PayPal account is deleted and no further stages are charged.`}</p><p className="error-text" role="alert">{confirmError}</p><div className="dialog-actions"><button className="button stop" disabled={stopping} onClick={stop}>Stop sponsorship</button><button className="button quiet" onClick={() => dialog.current?.close()}>Keep it going</button></div></div></dialog></>;
}
