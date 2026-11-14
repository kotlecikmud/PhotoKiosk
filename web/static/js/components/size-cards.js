// Print-size cards for the editor. Card elements are created once per slot and then
// updated in place, so tapping "+" repeatedly never loses a click to a re-render.
import { h, setText } from '../dom.js';
import { money, t } from '../i18n.js';

class SizeCard {
  constructor(index, finishes, cb) {
    this.index = index;
    this.cb = cb;
    this.size = h('span.sc-size');
    this.del = h('button.sc-del', { type: 'button', 'aria-label': 'remove', onclick: (e) => { e.stopPropagation(); cb.remove(this.index); } }, '✕');
    this.badge = h('div.sc-badge');
    this.count = h('div.sc-count');
    this.minus = h('button.sc-step.minus', { type: 'button', 'aria-label': '-', onclick: (e) => { e.stopPropagation(); cb.minus(this.index); } }, '−');
    this.plus = h('button.sc-step.plus', { type: 'button', 'aria-label': '+', onclick: (e) => { e.stopPropagation(); cb.plus(this.index); } }, '+');
    this.papers = {};
    this.paperBox = h('div.sc-paper' + (finishes.length > 2 ? '.many' : ''), {}, finishes.map((f) => {
      const b = h('button.sc-pill', { type: 'button', onclick: (e) => { e.stopPropagation(); cb.paper(this.index, f); } }, f);
      this.papers[f] = b;
      return b;
    }));
    this.price = h('div.sc-price');
    this.remove = h('button.sc-remove', { type: 'button', onclick: (e) => { e.stopPropagation(); cb.remove(this.index); } });
    this.el = h('div.size-card', { onclick: () => cb.select(this.index) },
      h('div.sc-head', {}, this.size, this.del), this.badge,
      h('div.sc-stepper', {}, this.minus, this.count, this.plus),
      this.paperBox, this.price, this.remove);
  }

  apply(card, active, price) {
    this.el.classList.toggle('active', active);
    setText(this.size, card.size_name);
    setText(this.badge, active ? `● ${t('gallery.active_badge')}` : `○ ${t('gallery.tap_to_preview')}`);
    setText(this.count, `${t('gallery.qty')}: ${card.count}`);
    this.count.classList.toggle('has', card.count > 0);
    this.minus.disabled = card.count === 0;
    for (const [f, b] of Object.entries(this.papers)) {
      b.classList.toggle('on', f.toUpperCase() === card.paper_type.toUpperCase());
      setText(b, t(`paper_finishes.${f.toLowerCase()}`, { default: f }));
    }
    setText(this.price, price > 0 ? money(price) : 'N/A');
    setText(this.remove, `✕ ${t('gallery.remove_card')}`);
  }
}

export class SizeCards {
  // cb: { select, plus, minus, paper, remove, add }
  constructor(el, finishes, cb) {
    this.el = el;
    this.finishes = finishes;
    this.cb = cb;
    this.pool = [];
    this.empty = h('div.sc-empty');
    this.addBtn = h('button.size-add', { type: 'button', onclick: (e) => { e.stopPropagation(); cb.add(this.addBtn); } },
      h('span.plus-big', {}, '+'), h('span.add-label'));
    this.addLabel = this.addBtn.querySelector('.add-label');
    el.append(this.addBtn);
    this._count = -1;
  }

  sync(cards, activeIdx, priceFn) {
    while (this.pool.length < cards.length) this.pool.push(new SizeCard(this.pool.length, this.finishes, this.cb));
    while (this.pool.length > cards.length) this.pool.pop().el.remove();
    if (this._count !== cards.length) {            // structure changed: re-attach in order
      this._count = cards.length;
      this.empty.remove();
      if (!cards.length) this.el.insertBefore(this.empty, this.addBtn);
      this.pool.forEach((c) => this.el.insertBefore(c.el, this.addBtn));
    }
    this.pool.forEach((c, i) => c.apply(cards[i], i === activeIdx, priceFn(cards[i].size_name, cards[i].paper_type)));
    setText(this.empty, t('gallery.no_sizes_added'));
    setText(this.addLabel, t('gallery.add_size'));
  }
}
