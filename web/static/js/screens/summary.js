// Screen 3: review the order (grouped by format & paper, and per photo) and submit it.
import { Binder, h, setText } from '../dom.js';
import { money, t } from '../i18n.js';
import { languageToggle } from '../components/lang-toggle.js';
import { saveSession, state } from '../store.js';

export function mount(root, ctx) {
  const b = new Binder();
  const draft = state.draft;

  const groupedList = h('div.list'), itemsList = h('div.list');
  const totalEl = h('div.sum-total'), priceEl = h('div.sum-price');
  const submit = b.text(h('button.btn.success.big', { onclick: () => ctx.submitOrder(items()) }), () => t('order_summary.btn_submit'));
  const back = b.text(h('button.btn.secondary', { onclick: () => ctx.navigate('gallery') }), () => t('order_summary.btn_back'));

  root.append(h('div.screen.summary', {},
    h('header.topbar', {}, back, b.text(h('h1.gt-title'), () => t('order_summary.title')), h('div.grow'), languageToggle()),
    h('div.sbody', {},
      h('section.pane', {}, b.text(h('h2.pane-title'), () => t('order_summary.section_grouped')), groupedList),
      h('section.pane', {}, b.text(h('h2.pane-title'), () => t('order_summary.section_individual')), itemsList)),
    h('footer.sumbar', {}, totalEl, priceEl, h('div.grow'), submit)));

  const items = () => draft.buildItems();

  function finishLabel(f) { return t(`paper_finishes.${f.toLowerCase()}`, { default: f }); }

  function render() {
    const list = items();

    // grouped by (finish, size)
    const groups = new Map();
    for (const it of list) {
      const key = `${it.finish}|${it.size}`;
      const g = groups.get(key) || { finish: it.finish, size: it.size, count: 0, photos: 0, unit: it.unit_price, subtotal: 0 };
      g.count += it.qty; g.photos += 1; g.subtotal += it.subtotal;
      groups.set(key, g);
    }
    const sorted = [...groups.values()].sort((a, c) => (a.finish + a.size).localeCompare(c.finish + c.size));
    groupedList.replaceChildren(...(sorted.length ? sorted.map((g) => h('div.row', {},
      h('div.row-main', {},
        h('div.row-title', {}, `${g.size} ${finishLabel(g.finish)}`),
        h('div.row-sub', {}, t('order_summary.total_prints_group', { count: g.count, items: g.photos }) + (g.unit > 0 ? ` @ ${money(g.unit)}` : ''))),
      h('div.row-price', {}, g.subtotal > 0 ? money(g.subtotal) : '-'))) : [h('div.empty', {}, t('order_summary.empty_items'))]));

    // one line per photo & card
    itemsList.replaceChildren(...list.map((it) => h('div.row', {},
      h('div.row-main', {},
        h('div.row-title', {}, it.filename),
        h('div.row-sub', {}, [it.size, finishLabel(it.finish), t(`crop_modes.${it.crop}`), `${t('gallery.qty')}: ${it.qty}`,
          it.unit_price > 0 ? money(it.subtotal) : null].filter(Boolean).join(' | '))),
      h('button.btn.danger.icon', { 'aria-label': t('gallery.remove_card'), onclick: () => { draft.clearCard(it.photo_id, it.card_index); saveSession(); render(); } }, '✕'))));

    const prints = list.reduce((s, i) => s + i.qty, 0);
    const price = list.reduce((s, i) => s + i.subtotal, 0);
    setText(totalEl, t('order_summary.total_prints_summary', { items: list.length, prints }));
    setText(priceEl, price > 0 ? money(price) : '');
    submit.disabled = list.length === 0;
  }

  const off = ctx.onLanguage(() => { b.refresh(); render(); });
  render();
  return { destroy: off };
}
