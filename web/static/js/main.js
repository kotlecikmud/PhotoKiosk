// Kiosk bootstrap: load config + language, then run a tiny hash router between the screens.
import { API, getJSON, sendJSON } from './api.js';
import { connect, on } from './events.js';
import { getLanguage, initI18n, loadLanguage, onLanguageChange, setLanguage, t } from './i18n.js';
import { debounce, uid } from './dom.js';
import { confirmDialog, openModal, toast } from './components/dialogs.js';
import { discardCart, hasDraft, newDraft, restoreSession, saveSession, state } from './store.js';
import { h } from './dom.js';
import * as home from './screens/home.js';
import * as gallery from './screens/gallery.js';
import * as summary from './screens/summary.js';
import { mountDone, mountProcessing } from './screens/processing.js';

const appRoot = document.getElementById('app');
let current = null;     // { name, screen }
let renderToken = 0;

const ROUTES = {
  sources: (root, ctx) => home.mount(root, ctx),
  gallery: (root, ctx) => gallery.mount(root, ctx),
  summary: (root, ctx) => summary.mount(root, ctx),
  processing: (root, ctx) => mountProcessing(root, ctx),
  done: (root, ctx) => mountDone(root, ctx),
};

const ctx = {
  state,
  onProgress: null,
  navigate(name, { replace = false } = {}) {
    const hash = `#/${name}`;
    if (location.hash === hash) { render(); return; }
    if (replace) location.replace(hash); else location.hash = hash;
  },
  onLanguage: (fn) => onLanguageChange(fn),

  // A source was chosen: start a fresh order for it.
  openSource(source) {
    discardCart();
    state.source = source;
    state.cartId = uid(24);
    state.activePhoto = null;
    newDraft();
    saveSession();
    ctx.navigate('gallery');
  },

  // Back to the start screen with a clean session (the next customer sees nothing of the last).
  async resetToSources() {
    discardCart();
    state.source = null; state.draft = null; state.lastOrder = null; state.submitting = null;
    state.activePhoto = null; state.cartId = uid(24);
    saveSession.cancel && saveSession.cancel();
    try { sessionStorage.removeItem('pk.session'); } catch (e) { /* ignore */ }
    try { state.boot = await getJSON(`${API}/bootstrap`); } catch (e) { /* keep the old catalog */ }
    ctx.navigate('sources', { replace: true });
  },

  async submitOrder(items) {
    if (!items.length || state.submitting) return;
    const requestId = uid(16);
    state.submitting = { jobId: null };   // set first: the route guard requires it
    ctx.navigate('processing');
    try {
      const { job_id: jobId } = await sendJSON('POST', `${API}/orders`, {
        cart_id: state.cartId, request_id: requestId,
        items: items.map((i) => ({ source_id: state.source.id, photo_id: i.photo_id, size: i.size, finish: i.finish, crop: i.crop, qty: i.qty })),
      });
      state.submitting = { jobId };
      for (;;) {
        const job = await getJSON(`${API}/orders/jobs/${jobId}`);
        ctx.onProgress && ctx.onProgress(job);
        if (job.status === 'completed') { state.lastOrder = job.result; break; }
        if (job.status === 'failed') throw new Error(job.error || 'Order failed');
        await new Promise((r) => setTimeout(r, 400));
      }
      state.submitting = null;
      saveSession.cancel && saveSession.cancel();
      try { sessionStorage.removeItem('pk.session'); } catch (e) { /* ignore */ }
      ctx.navigate('done', { replace: true });
    } catch (e) {
      state.submitting = null;
      toast(`${t('web.order_failed')}: ${e.message}`, { kind: 'error', ms: 9000 });
      ctx.navigate('summary', { replace: true });
    }
  },
};

// ---- router ---------------------------------------------------------------------------
function routeName() { return (location.hash.replace(/^#\/?/, '') || 'sources'); }

function guard(name) {
  if (!(name in ROUTES)) return 'sources';
  if ((name === 'gallery' || name === 'summary') && (!state.source || !state.draft)) return 'sources';
  if (name === 'summary' && !hasDraft()) return 'gallery';
  if (name === 'processing' && !state.submitting && !state.lastOrder) return state.source ? 'summary' : 'sources';
  if (name === 'done' && !state.lastOrder) return 'sources';
  return name;
}

function render() {
  const wanted = routeName();
  const name = guard(wanted);
  if (name !== wanted) { location.replace(`#/${name}`); return; }
  if (current && current.name === name) return;
  const token = ++renderToken;
  current && current.screen.destroy && current.screen.destroy();
  appRoot.replaceChildren();
  current = { name, screen: ROUTES[name](appRoot, ctx) || {} };
  document.body.dataset.screen = name;
  if (token !== renderToken) return;
}
window.addEventListener('hashchange', render);

// ---- idle reset (customer walked away) ------------------------------------------------------
function startIdleWatch(seconds) {
  if (!seconds) return;
  let last = Date.now(), warning = null, countdown = 0, tick = null;
  const touch = () => { last = Date.now(); if (warning) dismiss(); };
  ['pointerdown', 'keydown', 'wheel', 'touchstart'].forEach((ev) => window.addEventListener(ev, touch, { passive: true, capture: true }));

  const dismiss = () => { clearInterval(tick); warning && warning.close(); warning = null; };
  const hasSomethingToLose = () => ['gallery', 'summary'].includes(routeName()) || hasDraft();

  setInterval(() => {
    const name = routeName();
    if (name === 'processing' || warning) return;
    const idle = (Date.now() - last) / 1000;
    if (name === 'done' && idle > 45) { ctx.resetToSources(); return; }
    if (!hasSomethingToLose() || idle < seconds) return;
    countdown = 20;
    const msg = h('span');
    const upd = () => { msg.textContent = t('web.idle_message', { seconds: countdown }); };
    upd();
    warning = openModal({
      title: t('web.idle_title'), body: msg,
      actions: [{ label: t('web.idle_stay'), kind: 'primary', onClick: () => { last = Date.now(); dismiss(); } }],
    });
    tick = setInterval(() => {
      countdown -= 1; upd();
      if (countdown <= 0) { dismiss(); ctx.resetToSources(); }
    }, 1000);
  }, 1000);
}

// ---- global live events ----------------------------------------------------------------------
function wireEvents() {
  let banner = null;
  on('connection', (up) => {
    state.connected = up;
    if (!up && !banner) { banner = h('div.conn-banner', {}, t('web.reconnecting')); document.body.append(banner); }
    if (up && banner) { banner.remove(); banner = null; }
  });
  on('device', (d) => {
    if (d.action === 'removed') { toast(t('web.device_removed')); return; }
    if (d.action !== 'connected' && d.action !== 'mounted') return;
    if (routeName() === 'sources') { toast(t('app.device_detected_title')); return; }
    toast(t('app.device_detected_title'), {
      actionLabel: t('web.open'), ms: 12000,
      onAction: async () => {
        if (hasDraft() && !(await confirmDialog({
          title: t('web.leave_title'), message: t('web.leave_message', { count: state.draft.totalPrints() }),
          confirmLabel: t('web.discard'), danger: true,
        }))) return;
        ctx.resetToSources();
      },
    });
  });
}

// ---- kiosk hardening (no context menu, no pinch zoom, no drag ghosts) --------------------------
function hardenKiosk() {
  document.addEventListener('contextmenu', (e) => e.preventDefault());
  document.addEventListener('dragstart', (e) => e.preventDefault());
  document.addEventListener('gesturestart', (e) => e.preventDefault());
  document.addEventListener('wheel', (e) => { if (e.ctrlKey) e.preventDefault(); }, { passive: false });
}

// ---- boot --------------------------------------------------------------------------------------
async function boot() {
  hardenKiosk();
  try {
    state.boot = await getJSON(`${API}/bootstrap`);
    let lang = state.boot.language.default;
    try { lang = localStorage.getItem('pk.lang') || lang; } catch (e) { /* ignore */ }
    if (!state.boot.language.available.some((l) => l.code === lang)) lang = state.boot.language.default;
    initI18n(state.boot.language.available, lang);
    await loadLanguage(lang);
    if (lang !== state.boot.language.default) await loadLanguage(state.boot.language.default);
    await setLanguage(lang);
  } catch (e) {
    appRoot.replaceChildren(h('div.boot-error', {}, `Cannot reach the kiosk server (${e.message}). Retrying…`));
    setTimeout(() => location.reload(), 3000);
    return;
  }
  restoreSession();
  wireEvents();
  connect(state.boot.boot_id);
  startIdleWatch(state.boot.kiosk.auto_reset_seconds);
  render();
}

boot();
