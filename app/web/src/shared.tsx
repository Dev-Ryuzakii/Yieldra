import { motion, useReducedMotion } from 'motion/react';
import { useEffect, useState } from 'react';

export class ApiError extends Error {
  constructor(public status: number, detail: unknown) {
    super(typeof detail === 'string' ? detail : 'Something went wrong. Please try again.');
  }
}
export async function api<T>(path: string, options: { method?: string; body?: unknown; adminKey?: string } = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  if (options.adminKey) headers['X-Admin-Key'] = options.adminKey;
  let response: Response;
  try {
    response = await fetch(path, { method: options.method || 'GET', headers, body: options.body === undefined ? undefined : JSON.stringify(options.body) });
  } catch {
    throw new ApiError(0, 'Could not reach Yieldra. Check your connection and try again.');
  }
  const text = await response.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { /* empty or non-JSON response */ }
  if (!response.ok) {
    let detail = data?.detail;
    if (Array.isArray(detail)) detail = detail.map((item: { msg: string }) => item.msg).join('. ');
    throw new ApiError(response.status, detail);
  }
  return data as T;
}
export type Rail = 'paypal' | 'tuago';
export type Currency = 'USD' | 'NGN';
export interface Meta {
  paypal: string; tuago: string; model_ready: boolean; auth_ready: boolean; operator_key_required: boolean;
  usd_ngn_rate: number; limits: Record<Rail, { currency: Currency; min: number; max: number }>;
}
export interface AccountSession { authenticated: boolean; profile?: { name: string; email: string; role: string } }
export const STATE_TEXT: Record<string, string> = {
  locked: 'Not open yet', awaiting_evidence: "Waiting for the farmer's photo",
  needs_review: 'Photo is being checked by a person', awaiting_payment: 'Photo passed. Payment due',
  payment_failed: 'Photo passed. Payment did not go through', paid: 'Paid',
};
export const SHARES = [20, 30, 30, 20];
export const SHORT_TITLES = ['Start', 'Planted', 'Established', 'Harvest'];
export const capital = (value: string) => value ? value.charAt(0).toUpperCase() + value.slice(1) : '';
export const money = (value: number, currency: Currency) => `${currency === 'USD' ? '$' : '₦'}${value.toLocaleString('en-US', { minimumFractionDigits: Number.isInteger(value) ? 0 : 2, maximumFractionDigits: 2 })}`;
export function splitMinor(totalMinor: number) {
  const parts = SHARES.map(share => Math.floor(totalMinor * share / 100));
  parts[3] += totalMinor - parts.reduce((a, b) => a + b, 0);
  return parts;
}
export interface StripStage { title: string; amount: string; share: number; state: string; note?: string }
export function Strip({ stages }: { stages: StripStage[] }) {
  const reduced = useReducedMotion();
  return <ol className="strip">{stages.map((stage, index) => <motion.li key={index} data-state={stage.state} style={{ flexGrow: stage.share }} initial={reduced ? false : { opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: index * 0.08 }}>
    <span className="n">{index + 1}</span><span className="stage">{stage.title}</span><span className="money">{stage.amount}</span><span className="state">{stage.note || STATE_TEXT[stage.state] || stage.state}</span>
  </motion.li>)}</ol>;
}
export function useMeta() {
  const [meta, setMeta] = useState<Meta | null>(null);
  useEffect(() => { api<Meta>('/meta').then(setMeta).catch(() => {}); }, []);
  return meta;
}
export function ModeNotice({ meta }: { meta: Meta | null }) {
  if (!meta) return null;
  const mock = [meta.paypal === 'mock' && 'PayPal', meta.tuago === 'mock' && 'Tuago'].filter(Boolean);
  const notes: string[] = [];
  if (mock.length) notes.push(`Demo mode: ${mock.join(' and ')} payments are simulated here, so no money moves.`);
  else if (meta.paypal === 'sandbox' || meta.tuago === 'test') notes.push('Test mode: payments run in the PayPal sandbox and Tuago test mode, so no real money moves.');
  if (!meta.model_ready) notes.push('No model key is set, so farm photos cannot be checked yet.');
  return notes.length ? <p className="notice" role="status">{notes.join(' ')}</p> : null;
}
const STORE = 'yieldra.sponsorships';
export interface Remembered { reference: string; farm: string; total: string }
export function remembered(): Remembered[] {
  try { const value: unknown = JSON.parse(localStorage.getItem(STORE) || '[]'); return Array.isArray(value) ? value as Remembered[] : []; } catch { return []; }
}
export function remember(entry: Remembered) {
  try { localStorage.setItem(STORE, JSON.stringify([entry, ...remembered().filter(item => item.reference !== entry.reference)].slice(0, 20))); } catch { /* storage unavailable */ }
}
export function Header({ consolePage = false }: { consolePage?: boolean }) {
  const [session, setSession] = useState<AccountSession | null>(null);
  useEffect(() => { api<AccountSession>('/auth/session').then(setSession).catch(() => {}); }, []);
  return <header className="wrap site-header"><a className="wordmark" href="/">Yieldra<span className="brand-star" aria-hidden="true">✳</span></a><nav className="site-nav" aria-label="Site">{consolePage ? <><a href="/">Sponsor pages</a><a href="/dashboard">Dashboard</a><a href="/docs">API</a></> : <><a href="/#farms">Farms</a><a href="/#questions">Questions</a>{session?.authenticated ? <a href="/dashboard">My account</a> : session ? <a href="/auth">Sign in</a> : null}</>}</nav></header>;
}
export function message(error: unknown) { return error instanceof Error ? error.message : 'Something went wrong. Please try again.'; }
