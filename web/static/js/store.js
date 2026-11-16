// Application state shared by all screens, plus session persistence.
//
// The order draft lives in the browser (instant UI). It is mirrored to sessionStorage so an
// accidental page reload does not lose a customer's selection, and wiped on every reset.
import { API, sendJSON } from './api.js';
import { OrderDraft } from './draft.js';
import { debounce, uid } from './dom.js';

const KEY = 'pk.session';

export const state = {
  boot: null,          // bootstrap payload (catalog, theme, kiosk config...)
  source: null,        // { id, kind, label }
  draft: null,         // OrderDraft
  cartId: uid(24),
  photos: [],          // photos of the open source
  scanner: null,       // slim scanner state from SSE
  handledScanId: -1,   // phone scan whose date dialog was already shown/answered
  submitting: null,    // { jobId } while an order is processed
  lastOrder: null,     // { order_id, total_prints, total_price }
  activePhoto: null,
  connected: true,
};

export function newDraft(saved = null) {
  state.draft = new OrderDraft(state.boot.catalog, saved);
  return state.draft;
}

export function hasDraft() { return !!state.draft && !state.draft.isEmpty(); }

// ---- persistence --------------------------------------------------------------
export const saveSession = debounce(() => {
  try {
    if (!state.source) { sessionStorage.removeItem(KEY); return; }
    sessionStorage.setItem(KEY, JSON.stringify({
      source: state.source, cartId: state.cartId, draft: state.draft ? state.draft.toJSON() : null,
      activePhoto: state.activePhoto,
    }));
  } catch (e) { /* storage full or disabled: persistence is best-effort */ }
}, 300);

export function restoreSession() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(KEY) || 'null');
    if (!saved || !saved.source) return false;
    state.source = saved.source;
    state.cartId = saved.cartId || state.cartId;
    state.activePhoto = saved.activePhoto || null;
    newDraft(saved.draft);
    return true;
  } catch (e) { return false; }
}

// ---- staging of selected photos (server copies them while the customer keeps browsing) -----
const pending = { add: new Set(), remove: new Set() };
const flushStaging = debounce(async () => {
  if (!state.source) return;
  const add = [...pending.add], remove = [...pending.remove];
  pending.add.clear(); pending.remove.clear();
  for (let i = 0; i < Math.max(add.length, remove.length); i += 2000) {
    try {
      await sendJSON('PUT', `${API}/carts/${state.cartId}/staged`, {
        source_id: state.source.id, add: add.slice(i, i + 2000), remove: remove.slice(i, i + 2000),
      });
    } catch (e) { /* staging is an optimisation; the order falls back to the original file */ }
  }
}, 300);

export function stagePhotos(pids) { pids.forEach((p) => { pending.remove.delete(p); pending.add.add(p); }); flushStaging(); }
export function unstagePhotos(pids) { pids.forEach((p) => { pending.add.delete(p); pending.remove.add(p); }); flushStaging(); }

export function discardCart() {
  if (state.source && state.source.kind === 'bluetooth') {      // the received photos belong to this customer only
    fetch(`${API}/bluetooth/session/${state.source.id.replace(/^bt-/, '')}`, { method: 'DELETE', keepalive: true }).catch(() => {});
  }
  const id = state.cartId;
  fetch(`${API}/carts/${id}`, { method: 'DELETE', keepalive: true }).catch(() => {});
}
