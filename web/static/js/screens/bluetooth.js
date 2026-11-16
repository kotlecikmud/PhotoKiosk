// Screen: receive photos sent from a phone over Bluetooth.
// Opening the screen starts a fresh, empty session; photos appear live as they arrive.
import { API, getJSON, photoUrl, sendJSON } from '../api.js';
import { Binder, h, setText } from '../dom.js';
import { on } from '../events.js';
import { t } from '../i18n.js';
import { languageToggle } from '../components/lang-toggle.js';
import { toast } from '../components/dialogs.js';

const MAX_THUMBS = 30;

export function mount(root, ctx) {
  const b = new Binder();
  let session = null, name = 'PhotoKiosk', keep = false, destroyed = false;

  const nameEl = h('div.bt-name');
  const steps = h('ol.bt-steps');
  const counter = h('div.bt-count');
  const progress = h('div.bt-progress');
  const sender = h('div.bt-sender');
  const thumbs = h('div.bt-thumbs');
  const status = h('div.bt-status', {}, h('div.spinner'), counter, progress, sender);
  const back = h('button.btn.secondary', { onclick: goBack });
  const proceed = h('button.btn.success.big', { disabled: true, onclick: proceedToGallery });
  const error = h('div.bt-error', { hidden: true });

  root.append(h('div.screen.bluetooth', {},
    h('header.topbar', {}, back, b.text(h('h1.gt-title.always'), () => t('bluetooth.title')), h('div.grow'), languageToggle()),
    h('div.bt-body', {},
      h('section.card.bt-card', {},
        b.text(h('div.bt-hint'), () => t('bluetooth.device_name_lbl')), nameEl, steps, error),
      h('section.card.bt-card', {}, status, thumbs)),
    h('footer.sumbar', {}, h('div.grow'), proceed)));

  function render() {
    setText(nameEl, `📶 ${name}`);
    steps.replaceChildren(...['step1', 'step2', 'step3'].map((k) => h('li', {}, t(`bluetooth.${k}`, { name }))));
    setText(back, t('bluetooth.btn_back'));
    const photos = session ? session.photos : [];
    const receiving = session ? session.receiving : [];
    setText(counter, photos.length || receiving.length ? t('bluetooth.received', { count: photos.length }) : t('bluetooth.waiting'));
    setText(progress, receiving.length ? t('bluetooth.receiving', { name: receiving[0].name }) : '');
    setText(sender, session && session.sender ? t('bluetooth.sender', { sender: session.sender }) : '');
    status.querySelector('.spinner').style.display = receiving.length || !photos.length ? '' : 'none';

    const have = new Set([...thumbs.children].map((c) => c.dataset.id));
    for (const p of photos.slice(-MAX_THUMBS)) {
      if (have.has(p.id)) continue;
      const img = h('img', { alt: '', draggable: 'false', src: `${photoUrl(session.source_id, p.id, 'thumb')}?v=${Math.floor(p.mtime || 0)}` });
      thumbs.append(h('div.bt-thumb', { 'data-id': p.id }, img));
    }
    setText(proceed, t('bluetooth.btn_continue', { count: photos.length }));
    proceed.disabled = photos.length === 0;
  }

  async function open() {
    try {
      const st = await sendJSON('POST', `${API}/bluetooth/session`, {});
      if (destroyed) return;
      name = st.name; session = st.session;
      render();
    } catch (e) {
      error.hidden = false;
      error.textContent = `${t('bluetooth.unavailable')} ${e.message || ''}`.trim();
    }
  }

  async function goBack() {
    keep = false;
    ctx.resetToSources();
  }

  function proceedToGallery() {
    keep = true;                                           // the gallery now owns the session
    ctx.openSource({ id: session.source_id, kind: 'bluetooth', label: 'Bluetooth' });
  }

  const offs = [
    on('bluetooth', (st) => {
      name = st.name;
      if (!st.available) { error.hidden = false; error.textContent = t('bluetooth.unavailable'); }
      if (st.session && session && st.session.id === session.id) { session = st.session; render(); }
    }),
    ctx.onLanguage(() => { b.refresh(); render(); }),
  ];
  render();
  open();

  return {
    destroy() {
      destroyed = true;
      offs.forEach((f) => f());
      // leaving without continuing: forget the received photos right away
      if (!keep && session) fetch(`${API}/bluetooth/session/${session.id}`, { method: 'DELETE', keepalive: true }).catch(() => {});
    },
  };
}
