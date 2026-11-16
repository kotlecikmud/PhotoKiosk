// Screen 2: split view - justified photo grid (left) and print editor (right).
import { API, getJSON, photoUrl, sendJSON } from '../api.js';
import { Binder, clamp, debounce, h, setText } from '../dom.js';
import { on } from '../events.js';
import { money, t } from '../i18n.js';
import { languageToggle } from '../components/lang-toggle.js';
import { confirmDialog, toast } from '../components/dialogs.js';
import { PhotoGrid } from '../components/photo-grid.js';
import { SizeCards } from '../components/size-cards.js';
import { ThumbLoader } from '../loader.js';
import { describeSource } from '../sources.js';
import { hasDraft, saveSession, stagePhotos, state, unstagePhotos } from '../store.js';

const DENSITIES = [4, 8, 16];
const CATEGORY_ORDER = ['all', 'camera', 'whatsapp', 'messenger', 'screenshots', 'instagram', 'telegram', 'downloads', 'snapchat', 'twitter', 'other'];
const CROP_MODES = ['fill_in', 'fit_in', 'border', 'no_resize'];
const PREVIEW_CACHE = 24;

export function mount(root, ctx) {
  const b = new Binder();
  const draft = state.draft;
  const sid = state.source.id;
  const catalog = state.boot.catalog;
  let destroyed = false;
  let photos = [], byId = new Map();
  let activePid = null, category = 'all';
  let density = Number(localStorage.getItem('pk.density')) || 4;
  if (!DENSITIES.includes(density)) density = 4;

  // ================================= top bar =========================================
  const backBtn = b.text(h('button.btn.secondary', { onclick: onBack }), () => `← ${t('gallery.back_to_sources')}`);
  const titleEl = b.text(h('h1.gt-title'), () => `${t('gallery.title')}: ${sourceTitle()}`);
  const selCount = h('span.gt-selected');
  const densityBtns = DENSITIES.map((d) => h('button.seg-btn', { type: 'button', onclick: () => setDensity(d) }, String(d)));
  const densityBox = h('div.seg.density', {}, b.text(h('span.seg-label'), () => `${t('gallery.density')}:`), densityBtns);
  const selAllBtn = h('button.btn.secondary', { onclick: toggleSelectAll });
  const proceedBtn = h('button.btn.success', { disabled: true, onclick: onProceed });
  const topbar = h('header.topbar', {}, backBtn, titleEl, selCount, densityBox, h('div.grow'), selAllBtn, languageToggle(), proceedBtn);

  function sourceTitle() { return describeSource(state.source).title; }

  // ================================= left pane =======================================
  const chips = h('div.chips');
  const gridEl = h('div.grid');
  const status = h('div.grid-status');
  const newPhotos = h('button.btn.primary.sm.new-photos', { hidden: true, onclick: () => reload(true) });
  const loader = new ThumbLoader(4);
  const grid = new PhotoGrid(gridEl, {
    loader,
    thumbUrl: (p) => `${photoUrl(sid, p.id, 'thumb')}?v=${Math.floor(p.mtime || 0)}`,
    onActivate: (pid) => { closePopup(); activate(pid); },
    onToggle: (pid) => { closePopup(); toggleSelection(pid); },
    stateOf: (pid) => ({ selected: draft.selected.has(pid), prints: draft.printsFor(pid) }),
    onBackground: closePopup,
  });
  const left = h('section.pane.left', {}, chips, h('div.grid-host', {}, gridEl, status, newPhotos));

  // ================================= right pane (editor) =============================
  const placeholder = b.text(h('div.editor-placeholder'), () => t('gallery.tap_image_prompt'));
  const nameEl = h('div.ed-name'), metaEl = h('div.ed-meta');
  const cb = h('input', { type: 'checkbox', id: 'ed-cb', onchange: onEditorCheckbox });
  const cbLabel = h('label.ed-check', { for: 'ed-cb' }, cb, b.text(h('span'), () => t('gallery.add_to_order')));
  const caption = h('div.preview-caption');
  const previewImg = h('img.preview-img', { alt: '', draggable: 'false' });
  const previewArea = h('div.preview-area', {}, previewImg, h('div.spinner.preview-spin'));
  const cropBtns = {};
  const cropBox = h('div.seg.crop', {}, CROP_MODES.map((m) => (cropBtns[m] = h('button.seg-btn', { type: 'button', onclick: () => onCrop(m) }))));
  const cardsEl = h('div.cards');
  const sizeCards = new SizeCards(cardsEl, catalog.finishes, {
    select: (i) => { closePopup(); draft.setActiveCard(activePid, i); renderCards(); requestPreview(); },
    plus: (i) => { closePopup(); cardChange(() => draft.increment(activePid, i)); },
    minus: (i) => { closePopup(); cardChange(() => draft.decrement(activePid, i)); },
    paper: (i, f) => { closePopup(); cardChange(() => draft.setPaper(activePid, i, f)); refreshCaption(); },
    remove: (i) => { closePopup(); cardChange(() => draft.removeCard(activePid, i)); requestPreview(); },
    add: openAddPopup,
  });
  const editor = h('div.editor', { hidden: true },
    h('div.ed-head', {}, h('div', {}, nameEl, metaEl), cbLabel),
    h('div.viewport', {}, caption, previewArea),
    h('div.ed-row', {}, b.text(h('span.ed-label'), () => `${t('gallery.crop_mode')}:`), cropBox),
    b.text(h('div.ed-label'), () => `${t('gallery.sizes_and_quantities')}:`), cardsEl);
  const right = h('section.pane.right', { onclick: (e) => { if (!e.target.closest('.popup, .size-add')) closePopup(); } }, placeholder, editor);

  root.append(h('div.screen.gallery', {}, topbar, h('div.gbody', {}, left, right)));

  // ================================= loading =========================================
  async function reload(keepActive = false) {
    setStatus(t('gallery.scanning_msg'));
    newPhotos.hidden = true;
    let data;
    try { data = await getJSON(`${API}/sources/${encodeURIComponent(sid)}/photos`); }
    catch (e) {
      if (destroyed) return;
      setStatus(e.status === 404 ? t('web.source_gone') : e.message);
      return;
    }
    if (destroyed) return;
    photos = data.photos;
    byId = new Map(photos.map((p) => [p.id, p]));
    draft.setPhotos(photos);
    if (!photos.length) { setStatus(t('gallery.no_photos')); grid.setPhotos([]); renderChips(); updateTop(); return; }
    setStatus('');
    const keep = keepActive ? activePid : state.activePhoto;
    category = category !== 'all' && !photos.some(inCategory) ? 'all' : category;
    renderChips();
    grid.setPhotos(photos);
    grid.setVisible(visibleIds());
    activate(byId.has(keep) ? keep : visibleIds()[0], true);
    updateTop();
    pumpDims();
    // re-stage whatever was already selected (e.g. returning from the summary)
    if (draft.selected.size) stagePhotos([...draft.selected]);
  }

  function setStatus(text) { setText(status, text); status.hidden = !text; }

  // ---- background probing of image sizes (drives the justified layout) -------------
  const pendingDims = new Set();
  let dimsBusy = false;
  async function pumpDims() {
    photos.forEach((p) => { if (!p.w) pendingDims.add(p.id); });
    if (dimsBusy) return;
    dimsBusy = true;
    try {
      while (pendingDims.size && !destroyed) {
        const vis = visibleIds();
        const start = Math.max(0, vis.indexOf(grid.anchorPid()));
        const chunk = [];
        for (let i = 0; i < vis.length && chunk.length < 120; i++) {
          const pid = vis[(start + i) % vis.length];
          if (pendingDims.has(pid)) chunk.push(pid);
        }
        if (!chunk.length) { pendingDims.clear(); break; }
        chunk.forEach((id) => pendingDims.delete(id));
        const { dims } = await sendJSON('POST', `${API}/sources/${encodeURIComponent(sid)}/dims`, { ids: chunk });
        if (destroyed) return;
        for (const [pid, wh] of Object.entries(dims)) if (wh && byId.has(pid)) [byId.get(pid).w, byId.get(pid).h] = wh;
        grid.setDims(dims);
      }
    } catch (e) { /* probing is best effort: tiles just keep the default aspect */ }
    dimsBusy = false;
  }

  // ================================= categories ======================================
  const inCategory = (p) => category === 'all' || p.category === category || p.album === category;
  const visibleIds = () => photos.filter(inCategory).map((p) => p.id);

  function renderChips() {
    const counts = {};
    photos.forEach((p) => { counts[p.category] = (counts[p.category] || 0) + 1; });
    const order = ['all', ...CATEGORY_ORDER.filter((c) => c !== 'all' && counts[c])];
    Object.keys(counts).forEach((c) => { if (!order.includes(c)) order.push(c); });
    const existing = [...chips.children].map((c) => c.dataset.cat);
    if (existing.join() !== order.join()) {
      chips.replaceChildren(...order.map((c) => h('button.chip', { type: 'button', 'data-cat': c, onclick: () => selectCategory(c) })));
    }
    for (const chip of chips.children) {
      const c = chip.dataset.cat;
      const label = c === 'all' ? t('gallery.album_all') : t(`gallery.category_${c}`, { default: c[0].toUpperCase() + c.slice(1) });
      setText(chip, `${label} (${c === 'all' ? photos.length : counts[c]})`);
      chip.classList.toggle('on', c === category);
    }
  }

  function selectCategory(c) {
    closePopup();
    if (c === category) return;
    category = c;
    renderChips();
    const ids = visibleIds();
    grid.setVisible(ids);
    if (ids.length && !ids.includes(activePid)) activate(ids[0]);
    updateTop();
    pumpDims();
  }

  // ================================= selection =======================================
  function track(pid, fn) {
    const was = draft.selected.has(pid);
    const res = fn();
    const now = draft.selected.has(pid);
    if (!was && now) stagePhotos([pid]);
    if (was && !now) unstagePhotos([pid]);
    return res;
  }

  function toggleSelection(pid) {
    track(pid, () => (draft.selected.has(pid) ? draft.deselect(pid) : draft.select(pid)));
    afterChange(pid);
  }

  function toggleSelectAll() {
    closePopup();
    const ids = visibleIds();
    if (!ids.length) return;
    if (draft.allSelected(ids)) { draft.deselectMany(ids); unstagePhotos(ids); }
    else { draft.selectMany(ids); stagePhotos(ids); }
    grid.refreshAll();
    afterChange(activePid);
  }

  function afterChange(pid) {
    if (pid) grid.refreshTile(pid);
    updateTop();
    if (pid && pid === activePid) { cb.checked = draft.selected.has(pid); renderCards(); }
    saveSession();
  }

  function updateTop() {
    const total = draft.totalPrints();
    const ids = visibleIds();
    setText(selCount, `${t('gallery.selected')}: ${draft.selected.size}`);
    setText(selAllBtn, draft.allSelected(ids) ? t('gallery.deselect_all') : t('gallery.select_all'));
    selAllBtn.disabled = !ids.length;
    setText(proceedBtn, total > 0 ? `${t('gallery.proceed_to_summary')} (${total}) →` : `${t('gallery.proceed_to_summary')} →`);
    proceedBtn.disabled = !(draft.selected.size > 0 && total > 0);
    densityBtns.forEach((btn, i) => btn.classList.toggle('on', DENSITIES[i] === density));
  }

  function setDensity(d) {
    closePopup();
    density = d;
    localStorage.setItem('pk.density', String(d));
    grid.setDensity(d);
    updateTop();
  }

  // ================================= editor ==========================================
  function activate(pid, silent = false) {
    if (!pid || !byId.has(pid)) { activePid = null; grid.setActive(null); showEditor(false); return; }
    activePid = pid; state.activePhoto = pid;
    draft.ensure(pid);
    grid.setActive(pid);
    if (!silent) grid.scrollToPhoto(pid);
    showEditor(true);
    const p = byId.get(pid);
    setText(nameEl, p.name);
    const dims = p.w ? `${p.w}×${p.h} px · ` : '';
    setText(metaEl, `${dims}${(p.size / 1024).toFixed(1)} KB`);
    cb.checked = draft.selected.has(pid);
    renderCrop();
    renderCards();
    requestPreview();
    saveSession();
  }

  function showEditor(on_) { editor.hidden = !on_; placeholder.hidden = on_; }

  function renderCrop() {
    const mode = draft.ensure(activePid).crop_mode;
    for (const m of CROP_MODES) { setText(cropBtns[m], t(`crop_modes.${m}`)); cropBtns[m].classList.toggle('on', m === mode); }
  }

  function renderCards() {
    if (!activePid) return;
    sizeCards.sync(draft.cards(activePid), draft.activeCardIdx(activePid), draft.price.bind(draft));
  }

  function cardChange(fn) {
    const pid = activePid;
    const prevActive = draft.activeCardIdx(pid);
    track(pid, fn);
    afterChange(pid);
    if (draft.activeCardIdx(pid) !== prevActive) requestPreview();   // quantities never re-render the preview
  }

  function onEditorCheckbox() {
    closePopup();
    const pid = activePid;
    track(pid, () => (cb.checked ? draft.select(pid) : draft.deselect(pid)));
    afterChange(pid);
  }

  function onCrop(mode) { closePopup(); draft.setCrop(activePid, mode); renderCrop(); requestPreview(); saveSession(); }

  // ---- "+ Add size" popup --------------------------------------------------------------
  let popup = null;
  function closePopup() { if (popup) { popup.remove(); popup = null; document.removeEventListener('pointerdown', outside, true); } }
  const outside = (e) => { if (popup && !popup.contains(e.target) && !e.target.closest('.size-add')) closePopup(); };
  function openAddPopup(anchor) {
    if (popup) { closePopup(); return; }
    const options = draft.availableSizesFor(activePid);
    popup = h('div.popup', {},
      h('div.popup-title', {}, t('gallery.select_size_to_add')),
      h('div.popup-list', {}, options.map((s) => h('button.popup-item', { type: 'button', onclick: () => addSize(s) }, s))));
    editor.append(popup);
    document.addEventListener('pointerdown', outside, true);
  }
  function addSize(size) {
    closePopup();
    draft.addSizeEverywhere(size);
    const i = draft.cards(activePid).findIndex((c) => c.size_name === size);
    if (i >= 0) draft.setActiveCard(activePid, i);
    renderCards(); requestPreview(); saveSession();
  }

  // ================================= preview =========================================
  // Cached, debounced, cancellable. The previous image stays visible until the new one is ready.
  const cache = new Map();      // key -> { url, meta }
  let shownKey = null, lastMeta = null, abort = null, timer = 0, spinTimer = 0;

  function boxSize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const q = (v) => clamp(Math.round((v * dpr) / 50) * 50, 100, 1600);
    return [q(previewArea.clientWidth || 600), q(previewArea.clientHeight || 450)];
  }

  function currentRequest() {
    if (!activePid) return null;
    const cards = draft.cards(activePid);
    const card = cards[draft.activeCardIdx(activePid)];
    const size = card ? card.size_name : catalog.sizes[0].name;
    const crop = draft.ensure(activePid).crop_mode;
    const [w, hgt] = boxSize();
    return { size, crop, w, h: hgt, key: `${activePid}|${size}|${crop}|${w}x${hgt}` };
  }

  function requestPreview() { clearTimeout(timer); timer = setTimeout(doPreview, 40); }

  async function doPreview() {
    const req = currentRequest();
    if (!req || req.key === shownKey) return;
    const hit = cache.get(req.key);
    if (hit) { cache.delete(req.key); cache.set(req.key, hit); showPreview(req.key, hit); return; }
    abort && abort.abort();
    abort = new AbortController();
    clearTimeout(spinTimer);
    spinTimer = setTimeout(() => previewArea.classList.add('loading'), 150);
    const q = new URLSearchParams({ size: req.size, crop: req.crop, w: req.w, h: req.h });
    try {
      const res = await fetch(`${photoUrl(sid, activePid, 'preview')}?${q}`, { signal: abort.signal });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
      const meta = JSON.parse(res.headers.get('X-Preview-Meta') || '{}');
      const entry = { url: URL.createObjectURL(await res.blob()), meta };
      cache.set(req.key, entry);
      while (cache.size > PREVIEW_CACHE) {
        const [k, v] = cache.entries().next().value;
        cache.delete(k);
        if (k !== shownKey) URL.revokeObjectURL(v.url);
      }
      const now = currentRequest();
      if (now && now.key === req.key) showPreview(req.key, entry);
    } catch (e) {
      if (e.name !== 'AbortError') { previewArea.classList.add('failed'); toast(t('web.preview_failed'), { kind: 'error' }); }
    } finally {
      clearTimeout(spinTimer);
      previewArea.classList.remove('loading');
    }
  }

  function showPreview(key, entry) {
    shownKey = key; lastMeta = entry.meta;
    previewArea.classList.remove('failed');
    previewImg.src = entry.url;
    refreshCaption();
  }

  function refreshCaption() {
    if (!lastMeta || !activePid) return;
    const req = currentRequest();
    const card = draft.cards(activePid)[draft.activeCardIdx(activePid)];
    const [pw, ph] = lastMeta.paper_mm || [0, 0];
    const finish = card ? card.paper_type : '';
    setText(caption, `${t('gallery.canvas_format')}: ${req.size} (${pw.toFixed(0)}×${ph.toFixed(0)} mm) • ${finish} • ${t(`crop_modes.${req.crop}`)}`);
  }

  new ResizeObserver(debounce(() => requestPreview(), 150)).observe(previewArea);

  // ================================= navigation ======================================
  async function onBack() {
    closePopup();
    if (hasDraft()) {
      const ok = await confirmDialog({
        title: t('web.leave_title'), message: t('web.leave_message', { count: draft.totalPrints() }),
        confirmLabel: t('web.discard'), danger: true,
      });
      if (!ok) return;
    }
    ctx.resetToSources();
  }

  function onProceed() { closePopup(); saveSession.flush(); ctx.navigate('summary'); }

  // ================================= live events / language ==========================
  const offs = [
    on('library', () => {
      if (state.source.kind === 'shared') { setText(newPhotos, t('web.new_photos')); newPhotos.hidden = false; }
    }),
    ctx.onLanguage(() => {
      b.refresh(); renderChips(); updateTop();
      if (activePid) { renderCrop(); renderCards(); lastMeta && (shownKey = null, doPreview()); }
      else sizeCards.sync([], 0, () => 0);
      if (!newPhotos.hidden) setText(newPhotos, t('web.new_photos'));
    }),
  ];

  updateTop();
  sizeCards.sync([], 0, () => 0);
  reload();

  return {
    destroy() {
      destroyed = true;
      offs.forEach((f) => f());
      clearTimeout(timer); clearTimeout(spinTimer);
      abort && abort.abort();
      closePopup();
      grid.destroy();
      cache.forEach((v) => URL.revokeObjectURL(v.url));
      cache.clear();
      saveSession.flush();
    },
  };
}
