import { motion, useReducedMotion } from 'motion/react';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ChatWidget } from '../ChatWidget';
import { api, ApiError, capital, Header, message, ModeNotice, money, remember, SHARES, SHORT_TITLES, splitMinor, Strip, useMeta, type AccountSession, type Currency, type Rail } from '../shared';

interface Farm { id: number; name: string; crop_type: string; location: string; farmer_first_name: string; cover_image_url: string | null; cover_image_alt: string | null; cover_image_credit: string | null; cover_image_source: string | null; cover_image_license: string | null; latest_stage: string | null; sponsors: number; naira_ready: boolean; total_plots: number; available_plots: number; plot_price_usd: number }
interface Checkout { approve_url: string; sponsorship: { reference: string; farm_name: string; total: string } }
interface CropCover { crop: string; url: string; alt: string; credit: string; source: string; license: string; changes: string; illustrative: boolean }
const RAILS: Record<Rail, { currency: Currency; label: string; button: string }> = {
  paypal: { currency: 'USD', label: 'Total in US dollars', button: 'Continue to PayPal' },
};
const DEFAULT_LIMITS = { paypal: { min: 10, max: 10000 } };

export function Landing() {
  const meta = useMeta();
  const reduced = useReducedMotion();
  const [farms, setFarms] = useState<Farm[] | null>(null);
  const [cropCovers, setCropCovers] = useState<CropCover[]>([]);
  const [farmError, setFarmError] = useState('');
  const [farm, setFarm] = useState<Farm | null>(null);
  const rail: Rail = 'paypal';
  const [plots, setPlots] = useState('1');
  const [cropNotice, setCropNotice] = useState('');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [signedInEmail, setSignedInEmail] = useState('');
  const [account, setAccount] = useState<AccountSession | null>(null);
  const [accountError, setAccountError] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const plotsInput = useRef<HTMLInputElement>(null);
  const nameInput = useRef<HTMLInputElement>(null);
  const emailInput = useRef<HTMLInputElement>(null);
  const limits = meta?.limits || DEFAULT_LIMITS;
  const config = RAILS[rail];
  const bounds = limits[rail];
  useEffect(() => { api<Farm[]>('/sponsorships/farms/overview').then(setFarms).catch(err => setFarmError(message(err))); }, []);
  useEffect(() => { api<CropCover[]>('/media/covers').then(setCropCovers).catch(() => {}); }, []);
  useEffect(() => { api<AccountSession>('/account/session').then(result => { setAccount(result); if (result.authenticated && result.profile) { setName(result.profile.name); setEmail(result.profile.email); setSignedInEmail(result.profile.email); } }).catch(err => setAccountError(message(err))); }, []);
  function openSponsor(selected: Farm) {
    if (account === null) return;
    if (!account.authenticated) { window.location.assign('/signin?next=%2F%23farms'); return; }
    setFarm(selected); setPlots('1'); setError(''); setCropNotice(''); dialog.current?.showModal();
  }
  function pickCrop(crop: string) {
    const open = (farms || []).filter(item => item.crop_type === crop && item.available_plots > 0).sort((a, b) => b.available_plots - a.available_plots);
    if (open.length) { openSponsor(open[0]); return; }
    setCropNotice(`No ${crop} farm is open for sponsorship right now. Try another crop, or ask the assistant.`);
    document.getElementById('farms')?.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth' });
  }
  const plotCount = Number(plots);
  const plotsValid = farm !== null && Number.isInteger(plotCount) && plotCount >= 1 && plotCount <= farm.available_plots;
  const total = farm && plotsValid ? plotCount * farm.plot_price_usd : 0;
  function stepPlots(delta: number) {
    if (!farm) return;
    const next = Math.min(farm.available_plots, Math.max(1, (Number.isInteger(plotCount) ? plotCount : 1) + delta));
    setPlots(String(next));
  }
  async function submit(event: FormEvent) {
    event.preventDefault(); if (!farm) return;
    if (!plotsValid) { setError(`Choose between 1 and ${farm.available_plots} plot${farm.available_plots === 1 ? '' : 's'}.`); plotsInput.current?.focus(); return; }
    if (!(total >= bounds.min && total <= bounds.max)) { setError(`The total must be between ${money(bounds.min, config.currency)} and ${money(bounds.max, config.currency)}.`); plotsInput.current?.focus(); return; }
    if (!name.trim()) { setError('Enter your name.'); nameInput.current?.focus(); return; }
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())) { setError('Enter an email address, like ada@example.com.'); emailInput.current?.focus(); return; }
    setBusy(true); setError('');
    try {
      const started = await api<Checkout>('/sponsorships/checkout', { method: 'POST', body: { farm_id: farm.id, plots: plotCount, rail, name: name.trim(), email: email.trim() } });
      remember({ reference: started.sponsorship.reference, farm: started.sponsorship.farm_name, total: started.sponsorship.total });
      window.location.assign(started.approve_url);
    } catch (err) { if (err instanceof ApiError && err.status === 401) { window.location.assign('/signin?next=%2F%23farms'); return; } setError(message(err)); setBusy(false); }
  }
  const preview = total > 0 ? splitMinor(Math.round(total * 100)).map((minor, index) => ({ title: SHORT_TITLES[index], amount: money(minor / 100, config.currency), share: SHARES[index], state: index === 0 ? 'awaiting_payment' : 'locked', note: index === 0 ? 'Paid today' : 'After its photo passes' })) : [];
  return <><Header /><main>
    <section className="hero-section"><div className="wrap"><ModeNotice meta={meta} />
      <div className="hero-card">
        <motion.div className="hero-copy" initial={reduced ? false : { opacity: 0, y: 26 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.65 }}>
          <p className="eyebrow light"><span className="eyebrow-dot" /> A better way to back farming</p>
          <h1>Grow something <em>real.</em></h1>
          <p className="lead">Sponsor a farm in Nigeria and follow four clear stages of growth. Each new stage is backed by a verified update.</p>
          <div className="hero-actions"><a className="button hero-button" href="#farms">Explore open farms <span aria-hidden="true">↗</span></a>{account && <a className="button hero-login" href={account.authenticated ? '/dashboard' : '/signin'}>{account.authenticated ? 'My account' : 'Sign in'} <span aria-hidden="true">↗</span></a>}</div>
          <div className="hero-footnote"><span className="hero-footnote-icon" aria-hidden="true">✳</span><span>Visible progress. Purposeful support.</span></div>
        </motion.div>
        <motion.div className="hero-art" aria-hidden="true" initial={reduced ? false : { opacity: 0, scale: 0.93, rotate: -3 }} animate={{ opacity: 1, scale: 1, rotate: 0 }} transition={{ duration: 0.8, delay: 0.18 }}>
          <div className="art-sun" /><div className="art-hill hill-back" /><div className="art-hill hill-front" /><div className="art-lines" />
          <div className="art-label"><span>FIELD JOURNAL / 001</span><strong>From seed<br />to something.</strong></div>
          <div className="art-stamp"><span>04</span><small>STAGES<br />OF GROWTH</small></div>
        </motion.div>
      </div>
      <div className="impact-preview"><div className="preview-intro"><div><p className="eyebrow">See the journey</p><h2>Every step, accounted for.</h2></div><p>Each sponsorship is divided into four stages, with later payments tied to verified progress.</p></div>
        <Strip stages={[{ title: 'Seed and land', amount: '20%', share: 20, state: 'locked', note: 'At the start' }, { title: 'Planted', amount: '30%', share: 30, state: 'locked', note: 'After planting proof' }, { title: 'Established', amount: '30%', share: 30, state: 'locked', note: 'After growth proof' }, { title: 'Harvest', amount: '20%', share: 20, state: 'locked', note: 'After harvest proof' }]} />
      </div>
    </div></section>
    <section className="band farms-section" id="farms"><div className="wrap"><div className="section-heading"><div><p className="eyebrow">Farms you can support</p><h2>Meet the farms.</h2></div><p>Choose a farm, make your contribution, and follow its progress all the way to harvest.</p></div>
      {cropNotice && <p className="notice" role="status">{cropNotice}</p>}
      {accountError && <p className="notice" role="alert">Account status could not be checked. {accountError} <button className="text-link" type="button" onClick={() => window.location.reload()}>Retry</button></p>}
      <ul className="farms" aria-live="polite">{farmError ? <li className="empty">Farms could not be loaded. {farmError}</li> : farms === null ? <li className="empty">Loading farms…</li> : farms.length === 0 ? <li className="empty">No farms are open for sponsorship yet. Check back soon.</li> : farms.map((item, index) => <motion.li className="farm" key={item.id} initial={reduced ? false : { opacity: 0, y: 24 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, amount: 0.15 }} transition={{ delay: index * 0.08, duration: 0.5 }} whileHover={reduced ? undefined : { y: -6 }}>
        <div className={`farm-media crop-${item.crop_type}`}>{item.cover_image_url ? <img className="thumb" src={item.cover_image_url} alt={item.cover_image_alt || `${item.crop_type} field`} /> : <div className="thumb farm-placeholder" role="img" aria-label="No cover photo yet"><span className="field-rows" /><span className="field-sun" /></div>}<span className="crop-tag">{capital(item.crop_type)}</span></div>
        {item.cover_image_source && <a className="farm-credit" href={item.cover_image_source} target="_blank" rel="noopener noreferrer">Photo: {item.cover_image_credit || 'source'}{item.cover_image_license ? ` · ${item.cover_image_license}` : ''} ↗</a>}
        <div className="farm-content"><p className="farm-location"><span aria-hidden="true">⌁</span> {item.location}, Nigeria</p><h3>{item.name}</h3><p className="farm-farmer">Farmed by {item.farmer_first_name}</p><div className="farm-meta"><div><span className="meta-label">Current stage</span><strong>{item.latest_stage || 'Ready to start'}</strong></div><div><span className="meta-label">Community</span><strong>{item.sponsors === 0 ? 'Be the first' : `${item.sponsors} sponsor${item.sponsors === 1 ? '' : 's'}`}</strong></div></div><p className="farm-plots"><strong>{money(item.plot_price_usd, 'USD')}</strong> per plot · {item.available_plots} of {item.total_plots} plots open</p><button className="button farm-action" disabled={account === null || item.available_plots === 0} onClick={() => openSponsor(item)}>{account === null ? 'Checking account…' : item.available_plots === 0 ? 'Fully sponsored' : 'Sponsor this farm'} <span aria-hidden="true">↗</span></button></div>
      </motion.li>)}</ul>{cropCovers.length > 0 && <div className="crop-library"><div className="crop-library-head"><div><p className="eyebrow">Crop library</p><h3>From fields across Nigeria.</h3></div><p>Pick a crop to sponsor a farm growing it. These licensed images illustrate the crops; they are not proof from a listed farm.</p></div><div className="crop-gallery">{cropCovers.map(image => <figure key={image.crop}><button type="button" className="crop-pick" onClick={() => pickCrop(image.crop)} aria-label={`Sponsor a ${image.crop} farm`}><img src={image.url} alt={image.alt} loading="lazy" /><span className="crop-pick-label">Sponsor {image.crop} <span aria-hidden="true">↗</span></span></button><figcaption><strong>{capital(image.crop)}</strong><a href={image.source} target="_blank" rel="noopener noreferrer">{image.credit} · {image.license} · resized ↗</a></figcaption></figure>)}</div></div>}</div></section>
    <section className="band questions-section" id="questions"><div className="wrap"><div className="section-heading questions-heading"><div><p className="eyebrow">Good questions, clear answers</p><h2>Know where your support goes.</h2></div><p>Transparency is part of the journey. Here are the details that matter.</p></div><dl className="answers">
      <motion.div initial={reduced ? false : { opacity: 0, y: 18 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }}><span className="answer-number">01 / VERIFY</span><dt>Who checks the photo?</dt><dd>An AI model compares the farmer's photo with what that stage should look like and says how sure it is. Clear passes go through. Unsure ones wait for a person. A photo that fails, or has been used before, releases nothing.</dd></motion.div>
      <motion.div initial={reduced ? false : { opacity: 0, y: 18 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }} transition={{ delay: 0.08 }}><span className="answer-number">02 / PAYOUT</span><dt>How does the farmer get paid?</dt><dd>Each PayPal contribution records the amount owed to the farmer. Farmer payouts are pending while a new settlement method is set up.</dd></motion.div>
      <motion.div initial={reduced ? false : { opacity: 0, y: 18 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }} transition={{ delay: 0.16 }}><span className="answer-number">03 / CONTROL</span><dt>What if I change my mind?</dt><dd>Stop from your sponsorship page at any time. Captured payments remain recorded, and no later stages are charged. Your saved PayPal account is deleted.</dd></motion.div>
      <motion.div initial={reduced ? false : { opacity: 0, y: 18 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }} transition={{ delay: 0.24 }}><span className="answer-number">04 / PURPOSE</span><dt>Is this an investment?</dt><dd>No. You are paying for a farm to be planted and harvested. You get photos and updates, not a financial return.</dd></motion.div>
    </dl></div></section>
  </main><footer className="site-footer"><div className="wrap footer-inner"><div><a className="wordmark" href="/">Yieldra<span className="brand-star">✳</span></a><p>Good things take root together.</p></div><p>Sponsorship payments run on PayPal.</p></div></footer>
  <dialog ref={dialog} aria-labelledby="sponsor-title"><form className="dialog-body" onSubmit={submit} noValidate><h2 id="sponsor-title">Sponsor {farm?.name}</h2><p className="sub">{farm && `${capital(farm.crop_type)} in ${farm.location}, farmed by ${farm.farmer_first_name}.`}</p>
    <div className="rails"><p>Pay with PayPal in US dollars. Approve once; later parts are charged after verified milestones.</p></div>
    <div className="field"><label htmlFor="plots">How many plots do you want to sponsor?</label><div className="plots-stepper"><button type="button" className="button quiet small" aria-label="One plot fewer" onClick={() => stepPlots(-1)} disabled={!plotsValid || plotCount <= 1}>−</button><input ref={plotsInput} type="number" id="plots" inputMode="numeric" min="1" max={farm?.available_plots} step="1" required value={plots} onChange={event => setPlots(event.target.value)} /><button type="button" className="button quiet small" aria-label="One plot more" onClick={() => stepPlots(1)} disabled={!farm || (plotsValid && plotCount >= farm.available_plots)}>+</button></div><p className="hint">{farm && `${money(farm.plot_price_usd, 'USD')} per plot · ${farm.available_plots} of ${farm.total_plots} plots open.`}</p></div>
    <div className="total-line" aria-live="polite"><span>{config.label}</span><strong>{total > 0 ? money(total, config.currency) : '—'}</strong>{total > 0 && farm && <small>{plotCount} plot{plotCount === 1 ? '' : 's'} × {money(farm.plot_price_usd, 'USD')}</small>}</div>
    {preview.length > 0 && <Strip stages={preview} />}
    <div className="field"><label htmlFor="name">Your name</label><input ref={nameInput} id="name" type="text" autoComplete="name" maxLength={120} required value={name} onChange={event => setName(event.target.value)} /></div>
    <div className="field"><label htmlFor="email">Email</label><input ref={emailInput} id="email" type="email" autoComplete="email" maxLength={160} required readOnly={!!signedInEmail} value={email} onChange={event => setEmail(event.target.value)} /><p className="hint">{signedInEmail ? 'Using your signed-in account.' : 'So you can find this sponsorship again. Updates appear on your sponsorship page.'}</p></div>
    <p className="error-text" role="alert">{error}</p><div className="dialog-actions"><button className="button go" type="submit" disabled={busy}>{busy ? 'Continuing…' : config.button}</button><button className="button quiet" type="button" onClick={() => dialog.current?.close()}>Not now</button></div>
  </form></dialog><ChatWidget meta={meta} /></>;
}
