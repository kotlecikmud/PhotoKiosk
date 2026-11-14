// Virtualised justified photo grid.
//
// * Only tiles near the viewport exist in the DOM; each is absolutely positioned.
// * Tile nodes are kept per photo id, so density / resize / filter changes only move them
//   (no rebuild, no flicker) and loaded thumbnails stay on screen.
// * State changes (selected / active / quantity) touch only the affected tile.
// * One delegated click handler; native scrolling means a drag never counts as a tap.
import { h, raf } from '../dom.js';
import { computeJustifiedLayout, rowsInRange, DEFAULT_ASPECT } from '../layout.js';

const GAP = 4;
const PREFETCH_VIEWPORTS = 1.0;
const SMALL_TILE = 60;

export class PhotoGrid {
  // opts: { loader, thumbUrl(photo), onActivate(pid), onToggle(pid), stateOf(pid)->{selected,prints}, onBackground() }
  constructor(el, opts) {
    this.el = el;
    this.opts = opts;
    this.spacer = h('div.grid-spacer');
    el.append(this.spacer);
    this.photos = new Map();   // pid -> photo
    this.aspects = new Map();  // pid -> w/h
    this.visible = [];         // pids in display order
    this.density = 4;
    this.active = null;
    this.layout = { tiles: [], rows: [], totalHeight: 0, width: 1 };
    this.slotOf = new Map();
    this.live = new Map();     // pid -> { node, img, chip, state }
    this._raf = 0; this._relayoutTimer = 0; this._lastWidth = 0; this._lastTop = 0; this._settle = 0;

    el.addEventListener('scroll', () => this._scheduleUpdate(), { passive: true });
    el.addEventListener('click', (e) => this._onClick(e));
    this._ro = new ResizeObserver(() => {
      const w = this.el.clientWidth;
      if (Math.abs(w - this._lastWidth) > 2) { this._lastWidth = w; this.relayout(true); } else this._scheduleUpdate();
    });
    this._ro.observe(el);
  }

  destroy() {
    this._ro.disconnect();
    this.opts.loader.clear();
    cancelAnimationFrame(this._raf);
    clearTimeout(this._relayoutTimer);
    clearTimeout(this._settle);
  }

  setPhotos(photos) {
    this.photos = new Map(photos.map((p) => [p.id, p]));
    this.aspects.clear();
    for (const p of photos) if (p.w && p.h) this.aspects.set(p.id, p.w / p.h);
    this._clearLive();
    this.visible = photos.map((p) => p.id);
    this.relayout(false);
  }

  setVisible(pids) {
    this.visible = pids;
    this.el.scrollTop = 0;
    this.relayout(false);
  }

  setDensity(n) { if (n !== this.density) { this.density = n; this.relayout(true); } }

  // dims: { pid: [w,h] } as they are probed by the server; coalesced into one relayout
  setDims(dims) {
    let changed = false;
    for (const [pid, wh] of Object.entries(dims)) {
      if (wh && wh[0] && wh[1] && this.photos.has(pid)) { this.aspects.set(pid, wh[0] / wh[1]); changed = true; }
    }
    if (changed && !this._relayoutTimer) {
      this._relayoutTimer = setTimeout(() => { this._relayoutTimer = 0; this.relayout(true); }, 250);
    }
  }

  setActive(pid) {
    const old = this.active;
    this.active = pid;
    if (old) this.refreshTile(old);
    if (pid) this.refreshTile(pid);
  }

  refreshTile(pid) { const t = this.live.get(pid); if (t) this._applyState(t, pid); }
  refreshAll() { for (const [pid, t] of this.live) this._applyState(t, pid); }

  scrollToPhoto(pid) {
    const slot = this.slotOf.get(pid);
    if (slot === undefined) return;
    const tile = this.layout.tiles[slot];
    const top = this.el.scrollTop, bottom = top + this.el.clientHeight;
    if (tile.y < top || tile.y + tile.h > bottom) this.el.scrollTop = Math.max(0, tile.y - this.el.clientHeight / 3);
  }

  // ---- layout ---------------------------------------------------------------
  relayout(keepAnchor) {
    const width = this.el.clientWidth || 480;
    const anchor = keepAnchor ? this._anchorPid() : null;
    const aspects = this.visible.map((pid) => this.aspects.get(pid) || DEFAULT_ASPECT);
    this.layout = computeJustifiedLayout(aspects, width, this.density, GAP);
    this.slotOf = new Map(this.visible.map((pid, i) => [pid, i]));
    this.spacer.style.height = `${this.layout.totalHeight}px`;
    if (anchor && this.slotOf.has(anchor)) this.el.scrollTop = this.layout.tiles[this.slotOf.get(anchor)].y;
    this._lastTop = this.el.scrollTop;
    this._update();
  }

  anchorPid() { return this._anchorPid(); }

  _anchorPid() {
    const { rows } = this.layout;
    if (!rows.length) return null;
    const [first] = rowsInRange(this.layout, this.el.scrollTop, this.el.scrollTop + 1);
    return first < rows.length ? this.visible[rows[first].first] : null;
  }

  _scheduleUpdate() {
    if (!this._raf) this._raf = raf(() => { this._raf = 0; this._update(); });
  }

  _update() {
    const lay = this.layout;
    if (!lay.tiles.length) { this._clearLive(); return; }
    const viewH = Math.max(this.el.clientHeight, 1), top = this.el.scrollTop;
    // While flinging, tiles are gone before their thumbnails would arrive: wait until scrolling
    // settles instead of queueing requests (and server work) nobody will see.
    const fast = Math.abs(top - this._lastTop) > viewH * 0.3;
    this._lastTop = top;
    if (fast) { clearTimeout(this._settle); this._settle = setTimeout(() => this._update(), 140); }
    const margin = viewH * PREFETCH_VIEWPORTS;
    const [fr, lr] = rowsInRange(lay, top - margin, top + viewH + margin);
    const needed = new Set();
    if (lr > fr) for (let s = lay.rows[fr].first; s < lay.rows[lr - 1].last; s++) needed.add(this.visible[s]);

    for (const [pid, t] of this.live) {
      if (!needed.has(pid)) { this.opts.loader.cancel(pid); t.node.remove(); this.live.delete(pid); }
    }
    for (const pid of needed) {
      const slot = this.slotOf.get(pid), g = lay.tiles[slot];
      let t = this.live.get(pid);
      if (!t) { t = this._createTile(pid); this.live.set(pid, t); this.spacer.append(t.node); }
      const s = t.node.style;
      s.transform = `translate(${g.x}px,${g.y}px)`; s.width = `${g.w}px`; s.height = `${g.h}px`;
      t.node.classList.toggle('small', g.w < SMALL_TILE);
      t.node.classList.toggle('tiny', g.w < 40);
      t.chip.hidden = g.w < SMALL_TILE || !t.prints;
      if (!t.loaded && !t.failed && !fast) {
        this.opts.loader.request(pid, this.opts.thumbUrl(this.photos.get(pid)), Math.abs(g.y - top), (img) => {
          const cur = this.live.get(pid);
          if (!cur) return;
          cur.loaded = true;
          img.className = 'tile-img';
          cur.node.prepend(img);
          requestAnimationFrame(() => img.classList.add('in'));
        }, () => { const cur = this.live.get(pid); if (cur) { cur.failed = true; cur.node.classList.add('broken'); } });
      }
    }
  }

  _createTile(pid) {
    const badge = h('button.tile-badge', { type: 'button', tabindex: -1, 'aria-label': 'select' }, '✓');
    const chip = h('span.tile-chip', { hidden: true });
    const node = h('div.tile', { 'data-id': pid }, badge, chip);
    const t = { node, chip, loaded: false, failed: false, selected: null, prints: 0, active: null };
    this._applyState(t, pid);
    return t;
  }

  _applyState(t, pid) {
    const { selected, prints } = this.opts.stateOf(pid);
    const active = pid === this.active;
    if (t.selected !== selected) { t.selected = selected; t.node.classList.toggle('selected', selected); }
    if (t.active !== active) { t.active = active; t.node.classList.toggle('active', active); }
    const shown = selected ? prints : 0;
    if (t.prints !== shown) { t.prints = shown; t.chip.textContent = `×${shown}`; }
    t.chip.hidden = !shown || t.node.classList.contains('small');
  }

  _clearLive() {
    for (const [pid, t] of this.live) { this.opts.loader.cancel(pid); t.node.remove(); }
    this.live.clear();
  }

  _onClick(e) {
    const node = e.target.closest('.tile');
    if (!node) { this.opts.onBackground && this.opts.onBackground(); return; }
    const pid = node.dataset.id;
    if (e.target.closest('.tile-badge') && !node.classList.contains('tiny')) this.opts.onToggle(pid);
    else this.opts.onActivate(pid);
  }
}
