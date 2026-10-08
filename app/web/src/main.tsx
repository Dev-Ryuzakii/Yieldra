import { lazy, Suspense } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

const Landing = lazy(() => import('./pages/Landing').then(module => ({ default: module.Landing })));
const Tracking = lazy(() => import('./pages/Tracking').then(module => ({ default: module.Tracking })));
const Console = lazy(() => import('./pages/Console').then(module => ({ default: module.Console })));
const Auth = lazy(() => import('./pages/Auth').then(module => ({ default: module.Auth })));
const Dashboard = lazy(() => import('./pages/Dashboard').then(module => ({ default: module.Dashboard })));
const path = window.location.pathname;
if (path === '/console') document.title = 'Operator console · Yieldra';
if (path.startsWith('/s/')) document.title = 'Your sponsorship · Yieldra';
if (path === '/auth') document.title = 'Sign in · Yieldra';
if (path === '/dashboard') document.title = 'Your dashboard · Yieldra';
const Page = path === '/auth' ? Auth : path === '/dashboard' ? Dashboard : path === '/console' ? Console : path.startsWith('/s/') ? Tracking : Landing;
createRoot(document.getElementById('root')!).render(<Suspense fallback={<p className="wrap empty">Loading Yieldra…</p>}><Page /></Suspense>);
