import { motion, useReducedMotion } from 'motion/react';
import { useEffect, useState } from 'react';
import { api, Header, message, money, type Currency } from '../shared';
interface Profile { id: number; name: string; email: string; role: string }
interface Item { id: number; title: string; subtitle: string; status: string; amount?: number; currency?: Currency; href?: string }
interface Data { profile: Profile; items: Item[] }
const roleText: Record<string, string> = { sponsor: 'Your sponsorships', farmer: 'Your farms', investor: 'Your investments', buyer: 'Your contracts', logistics: 'Your logistics work', operator: 'Operations overview' };
const emptyText: Record<string, string> = { sponsor: 'When you sponsor a farm, its progress will appear here.', farmer: 'No farms are linked to your account yet. An operator can connect your farm record.', investor: 'No investments are linked to this account yet.', buyer: 'No contracts are linked to this account yet.', logistics: 'Your logistics assignments will appear here as they are connected.', operator: 'No sponsorships have been created yet.' };
export function Dashboard() {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(true);
  const reduced = useReducedMotion();
  useEffect(() => { api<Data>('/auth/dashboard').then(setData).catch(cause => { if (cause?.status === 401) { location.href = '/auth'; return; } setError(message(cause)); }).finally(() => setBusy(false)); }, []);
  async function signOut() { try { await api('/auth/logout', { method: 'POST' }); location.href = '/'; } catch (cause) { setError(message(cause)); } }
  const role = data?.profile.role || 'sponsor';
  return <><Header /><main className="wrap dashboard-page">
    <motion.section className="dashboard-hero" initial={reduced ? false : { opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}><div><p className="eyebrow">Your space · {role}</p><h1>{data ? `Hello, ${data.profile.name.split(' ')[0]}.` : 'Your dashboard'}</h1><p>{data ? `A clear view of ${role === 'operator' ? 'the platform' : 'what is growing around you'}.` : 'Loading your space…'}</p></div><span aria-hidden="true">✳</span></motion.section>
    {error && <p className="notice" role="alert">{error}</p>}
    {busy ? <p className="dashboard-loading">Loading your dashboard…</p> : data && <div className="dashboard-grid"><section className="dashboard-main"><div className="section-heading"><div><p className="eyebrow">At a glance</p><h2>{roleText[role] || 'Your activity'}</h2></div><span className="dashboard-count">{data.items.length} {data.items.length === 1 ? 'item' : 'items'}</span></div>
      {data.items.length ? <div className="dashboard-list">{data.items.map((item, index) => <motion.article key={item.id} className="dashboard-item" initial={reduced ? false : { opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: index * .04 }}><div className="dashboard-item-mark" aria-hidden="true">✳</div><div><h3>{item.title}</h3><p>{item.subtitle}</p><span className="pill" data-state={item.status}>{item.status.replaceAll('_', ' ')}</span></div><div className="dashboard-item-end">{item.amount !== undefined && item.currency && <strong>{money(item.amount, item.currency)}</strong>}{item.href && <a href={item.href}>View details ↗</a>}</div></motion.article>)}</div> : <div className="dashboard-empty"><span aria-hidden="true">✳</span><h3>Room to grow.</h3><p>{emptyText[role]}</p>{role === 'sponsor' && <a className="button primary" href="/#farms">Explore farms →</a>}</div>}</section>
      <aside className="dashboard-side"><div className="dashboard-side-card"><p className="eyebrow">Account</p><h3>{data.profile.name}</h3><p>{data.profile.email}</p><span className="pill">{role}</span><button type="button" className="text-link" onClick={signOut}>Sign out ↗</button></div><div className="dashboard-side-card green"><p className="eyebrow">Next steps</p><h3>{role === 'operator' ? 'Keep things moving.' : 'Stay close to the work.'}</h3><p>{role === 'operator' ? 'Review milestones, payouts and farmer readiness in the console.' : 'Explore farms and check back for new updates.'}</p><a href={role === 'operator' ? '/console' : '/#farms'}>{role === 'operator' ? 'Open operator console →' : 'Explore farms →'}</a></div></aside></div>}
  </main></>;
}
