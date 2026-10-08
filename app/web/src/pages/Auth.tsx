import { motion, useReducedMotion } from 'motion/react';
import { useEffect, useState, type FormEvent } from 'react';
import { api, Header, message, useMeta } from '../shared';

type FormMode = 'login' | 'register';
const requested = new URLSearchParams(window.location.search).get('next');
const afterSignIn = requested?.startsWith('/') && !requested.startsWith('//') && !requested.includes('\\') ? requested : '/dashboard';
export function Auth() {
  const [mode, setMode] = useState<FormMode>('login');
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const reduced = useReducedMotion();
  const meta = useMeta();
  useEffect(() => { api<{ authenticated: boolean }>('/auth/session').then(result => { if (result.authenticated) location.href = afterSignIn; }).catch(() => {}); }, []);
  async function submit(event: FormEvent) {
    event.preventDefault(); setError(''); setNotice(''); setBusy(true);
    try {
      const result = await api<{ status: string }>(`/auth/${mode}`, { method: 'POST', body: mode === 'register' ? { email, password, name } : { email, password } });
      if (result.status === 'signed_in') location.href = afterSignIn;
      else setNotice('Check your inbox to confirm your email, then sign in.');
    } catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  }
  return <><Header /><main className="auth-shell wrap">
    <motion.section className="auth-story" initial={reduced ? false : { opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }}>
      <p className="eyebrow">Your Yieldra account</p><h1>Grow something<br /><em>real.</em></h1>
      <p>One place to follow the farms, payments and work connected to you.</p>
      <div className="auth-art" aria-hidden="true"><span className="auth-orbit one" /><span className="auth-orbit two" /><span className="auth-sun">✳</span><span className="auth-caption">From first seed<br />to final harvest.</span></div>
    </motion.section>
    <motion.section className="auth-card" initial={reduced ? false : { opacity: 0, y: 24 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: .08 }}>
      <div className="auth-switch" role="group" aria-label="Account action"><button type="button" aria-pressed={mode === 'login'} onClick={() => { setMode('login'); setError(''); setNotice(''); }}>Sign in</button><button type="button" aria-pressed={mode === 'register'} onClick={() => { setMode('register'); setError(''); setNotice(''); }}>Create account</button></div>
      <p className="eyebrow">{mode === 'login' ? 'Welcome back' : 'Join Yieldra'}</p><h2>{mode === 'login' ? 'Your farm story continues.' : 'Start your story.'}</h2><p className="auth-copy">{mode === 'login' ? 'Sign in to see your personal dashboard.' : 'Create an account with email and password. Operator access is applied automatically to approved emails.'}</p>
      {meta && !meta.auth_ready && <p className="notice">Afribase auth is not configured on this deployment yet.</p>}
      <form onSubmit={submit}>{mode === 'register' && <div className="field"><label htmlFor="auth-name">Your name</label><input id="auth-name" autoComplete="name" required minLength={2} value={name} onChange={e => setName(e.target.value)} placeholder="Ada Okafor" /></div>}
        <div className="field"><label htmlFor="auth-email">Email address</label><input id="auth-email" type="email" autoComplete="email" required value={email} onChange={e => setEmail(e.target.value)} placeholder="you@example.com" /></div>
        <div className="field"><label htmlFor="auth-password">Password</label><input id="auth-password" type="password" autoComplete={mode === 'login' ? 'current-password' : 'new-password'} required minLength={8} value={password} onChange={e => setPassword(e.target.value)} placeholder="At least 8 characters" /></div>
        {error && <p className="auth-error" role="alert">{error}</p>}{notice && <p className="notice" role="status">{notice}</p>}
        <button className="button primary auth-submit" disabled={busy || meta?.auth_ready === false}>{busy ? 'Just a moment…' : mode === 'login' ? 'Sign in →' : 'Create account →'}</button>
      </form><p className="auth-help">Use your approved operator email to create a test account. Farmers, investors, buyers and logistics users can sign in once their verified email is linked to their Yieldra record.</p>
    </motion.section>
  </main></>;
}
