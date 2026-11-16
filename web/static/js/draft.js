// Order draft: all selection / quantity / price rules of the gallery (pure, DOM-free).
//
// Port of the original Python `OrderDraft`, keyed by photo id instead of list index so a
// re-scan or re-ordering of the photo list can never attach a setting to the wrong photo.
//   * adding a size adds it (count 0) to every photo; removing a card only affects one photo
//   * raising a count auto-selects the photo; lowering every count to 0 deselects it

export const DEFAULT_CROP = 'fill_in';

export function priceKey(size, finish) {
  return `${String(size).trim().toLowerCase()}|${String(finish).trim().toLowerCase()}`;
}

export class OrderDraft {
  // catalog: { sizes:[{name,width_mm,height_mm}], finishes:[...], prices:{"10x15|glossy":1.1} }
  constructor(catalog, saved = null) {
    this.catalog = catalog;
    this.photoIds = [];
    this.configs = {};          // pid -> { crop_mode, cards:[{size_name, paper_type, count}] }
    this.selected = new Set();  // pids
    this.activeCard = {};       // pid -> card index
    this.names = {};            // pid -> file name (for the summary, survives a reload)
    const names = this.sizeNames();
    this.sessionCardSizes = names.slice(0, 2);
    this.defaultFinish = catalog.finishes[0] || 'GLOSSY';
    if (saved) this._restore(saved);
  }

  sizeNames() { return this.catalog.sizes.map((s) => s.name); }

  // The administrator changed sizes / finishes / prices while a customer is mid-order:
  // drop what no longer exists, keep everything else.
  updateCatalog(catalog) {
    this.catalog = catalog;
    const sizes = new Set(this.sizeNames());
    const finishes = catalog.finishes;
    this.defaultFinish = finishes[0] || 'GLOSSY';
    this.sessionCardSizes = this.sessionCardSizes.filter((s) => sizes.has(s));
    if (!this.sessionCardSizes.length) this.sessionCardSizes = this.sizeNames().slice(0, 2);
    for (const [pid, cfg] of Object.entries(this.configs)) {
      cfg.cards = cfg.cards.filter((c) => sizes.has(c.size_name));
      for (const c of cfg.cards) {
        if (!finishes.some((f) => f.toUpperCase() === c.paper_type.toUpperCase())) c.paper_type = this.defaultFinish;
      }
      if (!cfg.cards.length) this.activeCard[pid] = 0;
      else if ((this.activeCard[pid] ?? 0) >= cfg.cards.length) this.activeCard[pid] = 0;
      this._autoDeselectIfEmpty(pid);
    }
  }
  price(size, finish) { return this.catalog.prices[priceKey(size, finish)] || 0; }

  setPhotos(photos) {
    this.photoIds = photos.map((p) => p.id);
    for (const p of photos) this.names[p.id] = p.name;
    // forget selections of photos that disappeared (e.g. a USB stick that was swapped)
    const known = new Set(this.photoIds);
    for (const pid of [...this.selected]) if (!known.has(pid)) this.selected.delete(pid);
  }

  ensure(pid) {
    let cfg = this.configs[pid];
    if (!cfg) {
      cfg = {
        crop_mode: DEFAULT_CROP,
        cards: this.sessionCardSizes.map((s) => ({ size_name: s, paper_type: this.defaultFinish, count: 0 })),
      };
      this.configs[pid] = cfg;
    }
    if (!(pid in this.activeCard)) this.activeCard[pid] = 0;
    return cfg;
  }

  cards(pid) { return this.ensure(pid).cards; }

  activeCardIdx(pid) {
    const cards = (this.configs[pid] || {}).cards || [];
    if (!cards.length) return 0;
    let idx = this.activeCard[pid] ?? 0;
    if (idx < 0 || idx >= cards.length) { idx = 0; this.activeCard[pid] = 0; }
    return idx;
  }

  setActiveCard(pid, idx) {
    const cards = (this.configs[pid] || {}).cards || [];
    this.activeCard[pid] = cards.length ? Math.max(0, Math.min(idx, cards.length - 1)) : 0;
  }

  printsFor(pid) {
    const cfg = this.configs[pid];
    return cfg ? cfg.cards.reduce((s, c) => s + c.count, 0) : 0;
  }

  totalPrints() {
    let t = 0;
    for (const pid of this.selected) t += this.printsFor(pid);
    return t;
  }

  isEmpty() { return this.totalPrints() === 0; }

  // ---- selection ----------------------------------------------------
  select(pid) {
    this.selected.add(pid);
    const cards = this.cards(pid);
    if (cards.length && cards.reduce((s, c) => s + c.count, 0) === 0) cards[0].count = 1;
  }

  deselect(pid) {
    this.selected.delete(pid);
    const cfg = this.configs[pid];
    if (cfg) for (const c of cfg.cards) c.count = 0;
  }

  selectMany(pids) { for (const pid of pids) this.select(pid); }
  deselectMany(pids) { for (const pid of pids) this.deselect(pid); }
  allSelected(pids) { return pids.length > 0 && pids.every((p) => this.selected.has(p)); }

  // ---- card operations ----------------------------------------------
  _autoDeselectIfEmpty(pid) {
    if (this.cards(pid).reduce((s, c) => s + c.count, 0) === 0) this.selected.delete(pid);
  }

  increment(pid, ci) {
    const cards = this.cards(pid);
    if (ci < 0 || ci >= cards.length) return false;
    cards[ci].count += 1;
    this.setActiveCard(pid, ci);
    this.selected.add(pid);
    return true;
  }

  decrement(pid, ci) {
    const cards = this.cards(pid);
    if (ci < 0 || ci >= cards.length) return false;
    cards[ci].count = Math.max(0, cards[ci].count - 1);
    this.setActiveCard(pid, ci);
    this._autoDeselectIfEmpty(pid);
    return true;
  }

  setPaper(pid, ci, paper) {
    const cards = this.cards(pid);
    if (ci < 0 || ci >= cards.length) return false;
    cards[ci].paper_type = paper;
    this.setActiveCard(pid, ci);
    return true;
  }

  setCrop(pid, mode) { this.ensure(pid).crop_mode = mode; }

  removeCard(pid, ci) {
    const cards = this.cards(pid);
    if (ci < 0 || ci >= cards.length) return false;
    const active = this.activeCardIdx(pid);
    cards.splice(ci, 1);
    if (!cards.length) this.setActiveCard(pid, 0);
    else if (active >= cards.length) this.setActiveCard(pid, cards.length - 1);
    else if (active > ci) this.setActiveCard(pid, active - 1);
    this._autoDeselectIfEmpty(pid);
    return true;
  }

  // Zero one card (used by the order summary's "remove line").
  clearCard(pid, ci) {
    const cards = this.cards(pid);
    if (ci < 0 || ci >= cards.length) return false;
    cards[ci].count = 0;
    this._autoDeselectIfEmpty(pid);
    return true;
  }

  // Add a print format to every photo with count 0 (skips photos that already have it).
  addSizeEverywhere(size) {
    if (!this.sessionCardSizes.includes(size)) this.sessionCardSizes.push(size);
    for (const pid of this.photoIds) {
      const cards = this.ensure(pid).cards;
      if (!cards.some((c) => c.size_name === size)) {
        cards.push({ size_name: size, paper_type: this.defaultFinish, count: 0 });
      }
    }
  }

  availableSizesFor(pid) {
    const have = new Set(this.cards(pid).map((c) => c.size_name));
    const names = this.sizeNames();
    const free = names.filter((s) => !have.has(s));
    return free.length ? free : names;
  }

  // ---- output -------------------------------------------------------
  // One line per card with count > 0, in photo order of `order` (defaults to selection order).
  buildItems(order = null) {
    const pids = order || [...this.selected];
    const items = [];
    for (const pid of pids) {
      if (!this.selected.has(pid)) continue;
      const cfg = this.configs[pid];
      cfg.cards.forEach((card, ci) => {
        if (card.count > 0) {
          const unit = this.price(card.size_name, card.paper_type);
          items.push({
            photo_id: pid,
            card_index: ci,
            filename: this.names[pid] || pid,
            size: card.size_name,
            finish: card.paper_type.toUpperCase(),
            crop: cfg.crop_mode,
            qty: card.count,
            unit_price: unit,
            subtotal: unit * card.count,
          });
        }
      });
    }
    return items;
  }

  // ---- persistence (sessionStorage survives an accidental page reload) --------
  toJSON() {
    const names = {};
    for (const pid of this.selected) names[pid] = this.names[pid];
    // Only photos the customer actually touched are worth persisting.
    const configs = {};
    for (const [pid, cfg] of Object.entries(this.configs)) {
      if (this.selected.has(pid) || cfg.crop_mode !== DEFAULT_CROP) configs[pid] = cfg;
    }
    return {
      configs, names,
      selected: [...this.selected],
      activeCard: this.activeCard,
      sessionCardSizes: this.sessionCardSizes,
    };
  }

  _restore(saved) {
    this.configs = saved.configs || {};
    this.selected = new Set(saved.selected || []);
    this.names = saved.names || {};
    this.activeCard = saved.activeCard || {};
    if (saved.sessionCardSizes && saved.sessionCardSizes.length) {
      this.sessionCardSizes = saved.sessionCardSizes.filter((s) => this.sizeNames().includes(s));
    }
  }
}
