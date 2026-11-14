// Screens 5 & 6: order processing (with real progress) and completion.
import { Binder, h, setText } from '../dom.js';
import { money, t } from '../i18n.js';
import { languageToggle } from '../components/lang-toggle.js';
import { state } from '../store.js';

export function mountProcessing(root, ctx) {
  const bar = h('div.progress-fill');
  const label = h('div.progress-label');
  const subtitle = h('p.proc-sub');
  const title = h('h1.proc-title');
  const refresh = () => { setText(title, t('processing.title_preparing')); setText(subtitle, t('processing.subtitle_preparing')); };
  root.append(h('div.screen.center', {},
    h('section.card.proc-card', {}, title, subtitle, h('div.progress', {}, bar), label)));
  refresh();

  const off = ctx.onLanguage(refresh);
  ctx.onProgress = (job) => {
    const total = job.total || 0;
    const pct = total ? Math.max(6, Math.round((job.done / total) * 100)) : 6;
    bar.style.width = `${pct}%`;
    setText(label, total ? t('web.processing_progress', { done: job.done, total }) : '');
  };
  return { destroy() { off(); ctx.onProgress = null; } };
}

export function mountDone(root, ctx) {
  const b = new Binder();
  const o = state.lastOrder;
  root.append(h('div.screen.center', {},
    h('section.card.proc-card.done', {},
      b.text(h('h1.proc-title.ok'), () => t('processing.title_ready')),
      b.text(h('p.proc-sub'), () => t('processing.subtitle_ready')),
      h('div.order-id', {}, b.text(h('span'), () => t('processing.order_id_lbl', { order_id: o.order_id }))),
      b.text(h('div.proc-prints'), () => t('processing.total_prints_lbl', { total_prints: o.total_prints })),
      o.total_price > 0 && h('div.proc-price', {}, money(o.total_price)),
      b.text(h('button.btn.primary.big', { onclick: () => ctx.resetToSources() }), () => t('processing.btn_finish')),
      h('div.lang-row', {}, languageToggle()))));
  const off = ctx.onLanguage(() => b.refresh());
  return { destroy: off };
}
