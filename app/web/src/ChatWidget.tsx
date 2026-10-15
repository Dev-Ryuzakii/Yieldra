import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { api, message, type Meta } from './shared';

interface Line { role: 'user' | 'assistant'; content: string }
const GREETING = "Hi! I'm Yieldra's assistant. Ask me about the farms, what a plot costs, or how the staged PayPal payments work.";
const SUGGESTIONS = ['How much is one plot?', 'How do the 4 payments work?', 'Which farms are open?'];

export function ChatWidget({ meta }: { meta: Meta | null }) {
  const reduced = useReducedMotion();
  const [open, setOpen] = useState(false);
  const [lines, setLines] = useState<Line[]>([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const log = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { log.current?.scrollTo({ top: log.current.scrollHeight }); }, [lines, busy, open]);
  useEffect(() => { if (open) input.current?.focus(); }, [open]);
  async function send(text: string) {
    const content = text.trim();
    if (!content || busy) return;
    const next = [...lines, { role: 'user' as const, content: content.slice(0, 1000) }];
    setLines(next); setDraft(''); setError(''); setBusy(true);
    try {
      const result = await api<{ reply: string }>('/chat', { method: 'POST', body: { messages: next.slice(-20) } });
      setLines([...next, { role: 'assistant', content: result.reply }]);
    } catch (err) { setError(message(err)); }
    finally { setBusy(false); }
  }
  function submit(event: FormEvent) { event.preventDefault(); void send(draft); }
  const offline = meta !== null && !meta.model_ready;
  return <>
    <AnimatePresence>{open && <motion.section className="chat-panel" role="dialog" aria-label="Chat with Yieldra's assistant" initial={reduced ? false : { opacity: 0, y: 16, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={reduced ? undefined : { opacity: 0, y: 16, scale: 0.98 }} transition={{ duration: 0.2 }}>
      <header className="chat-head"><div><strong>Yieldra assistant</strong><small>AI answers about farms and sponsoring</small></div><button type="button" className="chat-close" aria-label="Close chat" onClick={() => setOpen(false)}>×</button></header>
      <div className="chat-log" ref={log} aria-live="polite">
        <p className="chat-line assistant">{GREETING}</p>
        {lines.map((line, index) => <p key={index} className={`chat-line ${line.role}`}>{line.content}</p>)}
        {busy && <p className="chat-line assistant typing" aria-label="The assistant is typing"><span /><span /><span /></p>}
        {lines.length === 0 && !offline && <div className="chat-suggestions">{SUGGESTIONS.map(item => <button key={item} type="button" onClick={() => void send(item)}>{item}</button>)}</div>}
        {offline && <p className="chat-error">The assistant is offline right now.</p>}
        {error && <p className="chat-error" role="alert">{error}</p>}
      </div>
      <form className="chat-form" onSubmit={submit}><textarea ref={input} rows={1} maxLength={1000} placeholder="Ask a question…" aria-label="Your message" value={draft} disabled={offline} onChange={event => setDraft(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void send(draft); } }} /><button className="button small" type="submit" disabled={busy || offline || !draft.trim()}>Send</button></form>
      {meta?.telegram_url && <a className="chat-telegram" href={meta.telegram_url} target="_blank" rel="noopener noreferrer">Prefer Telegram? Chat with our bot ↗</a>}
    </motion.section>}</AnimatePresence>
    <button type="button" className="chat-launcher" aria-expanded={open} aria-label={open ? 'Close chat' : 'Ask Yieldra a question'} onClick={() => setOpen(value => !value)}>{open ? '×' : <><span aria-hidden="true">✳</span> Ask Yieldra</>}</button>
  </>;
}
