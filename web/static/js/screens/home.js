// Screen 1: choose where the photos come from.
import { API, getJSON, sendJSON } from '../api.js';
import { Binder, h, setText } from '../dom.js';
import { on } from '../events.js';
import { t } from '../i18n.js';
import { languageToggle } from '../components/lang-toggle.js';
import { openModal, toast } from '../components/dialogs.js';
import { describeSource } from '../sources.js';
import { state } from '../store.js';

const SCANNING = 'SCANNING', COMPLETE = 'COMPLETE', ERROR = 'ERROR';

export function mount(root, ctx) {
  const b = new Binder();
  let sources = [], phone = null, bt = null, destroyed = false, dateDialog = null, busy = null;
  const fresh = new Set();     // ids of sources that just appeared (highlighted for a moment)
  let known = null;

  // ---- header ----------------------------------------------------------------
  const title = b.text(h('h1.hero-title'), () => t('app.title'));
  const subtitle = b.text(h('p.hero-sub'), () => t('app.subtitle'));
  const header = h('header.home-head', {}, h('div', {}, title, subtitle), languageToggle());

  // ---- phone scanner card ------------------------------------------------------
  const pc = {
    title: h('div.pc-title'), badge: h('span.pc-badge'), path: h('div.pc-path'),
    images: h('b.pc-images'), videos: h('b'), files: h('span'), folders: h('span'), errors: h('span'),
    rescan: h('button.btn.primary.sm', { onclick: () => startScan() }),
    breakdown: h('div.pc-breakdown'), hint: h('div.pc-hint', { hidden: true }),
  };
  const phoneCard = h('section.card.phone-card', { hidden: true },
    h('div.pc-top', {}, pc.title, pc.badge), pc.path,
    h('div.pc-stats', {}, pc.images, pc.videos, pc.files, pc.folders, pc.errors, pc.rescan),
    pc.breakdown, pc.hint);

  // ---- scanning overlay ---------------------------------------------------------
  const ov = { photos: h('div.ov-photos'), time: h('div.ov-time'), path: h('div.ov-path') };
  const overlay = h('div.scan-overlay', { hidden: true },
    h('div.scan-box', {}, h('div.spinner'), b.text(h('div.ov-title'), () => t('scanner.modal_scanning')), ov.photos, ov.time, ov.path));

  // ---- sources list --------------------------------------------------------------
  const list = h('div.source-list');
  const refreshBtn = b.text(h('button.btn.secondary', { onclick: () => refreshSources(true) }), () => t('app.scan_devices'));
  const center = h('section.card.sources-card', {},
    b.text(h('h2.card-title'), () => t('app.prompt_sources')), list, h('div.card-foot', {}, refreshBtn));

  root.append(h('div.screen.home', {}, header, phoneCard, center, overlay));

  // ---- rendering -------------------------------------------------------------------
  function row(name, type, actionLabel, onClick, { disabled = false, isNew = false } = {}) {
    return h('div.source-row' + (isNew ? '.new' : ''), {},
      h('div.src-info', {}, h('div.src-name', {}, name), h('div.src-type', {}, type)),
      h('button.btn.primary', { disabled, onclick: onClick }, actionLabel));
  }

  function renderList() {
    const rows = [];
    const sc = state.scanner;
    if (phone && sc && sc.connection_status === 'connected') {
      const ready = sc.scan_status === COMPLETE && sc.images_found > 0;
      const scanning = sc.scan_status === SCANNING;
      rows.push(row(`📱 ${sc.device || 'Mobile Phone'}`, t('app.connected_mobile'),
        ready ? t('scanner.btn_view_photos', { count: sc.images_found }) : t('app.scan_device_btn'),
        () => (ready ? showDateDialog() : startScan()), { disabled: scanning }));
    }
    if (bt && bt.enabled && bt.available) {
      rows.push(row(`📶 ${t('bluetooth.row_title')}`, t('bluetooth.row_type', { name: bt.name }),
        t('bluetooth.btn_receive'), () => ctx.navigate('bluetooth')));
    }
    for (const s of sources) {
      const d = describeSource(s);
      rows.push(row(d.title, d.subtitle, t('app.look_here'), () => ctx.openSource(s), { isNew: fresh.has(s.id) }));
    }
    list.replaceChildren(...(rows.length ? rows : [h('div.empty', {}, t('web.no_sources'))]));
  }

  // A new list arrived (live event or manual refresh): highlight what is new.
  function setSources(next) {
    if (known) for (const s of next) if (!known.has(s.id)) {
      fresh.add(s.id);
      setTimeout(() => { fresh.delete(s.id); renderList(); }, 3000);
    }
    known = new Set(next.map((s) => s.id));
    sources = next;
    renderList();
  }

  function renderScanner() {
    const sc = state.scanner;
    const show = !!(phone && sc && sc.connection_status === 'connected');
    phoneCard.hidden = !show;
    if (!show) { overlay.hidden = true; return; }
    const status = sc.scan_status;
    setText(pc.title, t('scanner.phone_title', { name: sc.device || 'Mobile Phone' }));
    setText(pc.badge, status);
    setText(pc.path, `${t('scanner.path_prefix', { path: sc.current_path || '' })}`);
    setText(pc.images, t('scanner.images_count', { count: sc.images_found }));
    setText(pc.videos, t('scanner.videos_count', { count: sc.videos_found }));
    setText(pc.files, t('scanner.total_files', { count: sc.files_scanned }));
    setText(pc.folders, t('scanner.folders_count', { count: sc.folders_scanned }));
    setText(pc.errors, t('scanner.errors_count', { count: sc.errors }));
    setText(pc.rescan, t('scanner.btn_rescan'));
    pc.rescan.disabled = status === SCANNING;

    let breakdown = '', hint = '';
    if (status === SCANNING) breakdown = t('scanner.scan_in_progress');
    else if (status === COMPLETE) {
      if (sc.images_found > 0) {
        const cats = Object.entries(sc.images_by_category || {}).sort((a, c) => c[1] - a[1])
          .map(([k, n]) => `${t(`gallery.category_${k}`, { default: k })}: ${n}`).join(' • ');
        breakdown = t('scanner.categories_summary', { summary: cats });
      } else { breakdown = t('scanner.scan_complete_none'); hint = t('scanner.error_auth_hint'); }
    } else if (status === ERROR) { breakdown = t('scanner.scan_error'); hint = t('scanner.error_general_hint'); }
    setText(pc.breakdown, breakdown);
    setText(pc.hint, hint);
    pc.hint.hidden = !hint;

    overlay.hidden = status !== SCANNING;
    if (status === SCANNING) {
      setText(ov.photos, t('scanner.modal_progress', { count: sc.images_found }));
      setText(ov.time, t('scanner.modal_time', { elapsed: sc.elapsed_seconds || 0 }));
      setText(ov.path, t('scanner.modal_path', { path: sc.current_path || '...' }));
    }
    // Offer the date-range dialog once per finished scan with photos
    if (status === COMPLETE && sc.images_found > 0 && state.handledScanId !== sc.scan_id) showDateDialog();
  }

  // ---- actions -----------------------------------------------------------------------
  async function startScan() {
    try { await sendJSON('POST', `${API}/phone/scan`, {}); } catch (e) { toast(e.message, { kind: 'error' }); }
  }

  async function refreshSources(manual = false) {
    try {
      const data = await getJSON(`${API}/sources`);
      if (destroyed) return;
      phone = data.phone;
      bt = data.bluetooth;
      if (phone) state.scanner = phone;
      setSources(data.sources);
      renderScanner();
    } catch (e) { if (manual) toast(e.message, { kind: 'error' }); }
  }

  async function showDateDialog() {
    if (dateDialog || busy) return;
    const sc = state.scanner;
    state.handledScanId = sc.scan_id;
    let ranges = [];
    try { ranges = (await getJSON(`${API}/phone/date-ranges`)).ranges; } catch (e) { return; }
    if (destroyed) return;
    const labels = { '1_month': 'scanner.filter_1_month', '3_months': 'scanner.filter_3_months', '6_months': 'scanner.filter_6_months' };
    const buttons = ranges.map((r) => h('button.btn.secondary.range-btn', { disabled: r.count === 0, onclick: () => pick(r.key) },
      h('span', {}, r.key === 'all' ? t('scanner.filter_all', { count: r.count }) : `${t(labels[r.key])} (${r.count})`),
      h('small', {}, r.range)));
    dateDialog = openModal({
      title: t('scanner.date_filter_heading'), subtitle: t('scanner.date_filter_subheading'),
      body: h('div.range-list', {}, buttons),
      actions: [{ label: t('web.cancel'), kind: 'secondary' }],
      onClose: () => { dateDialog = null; },
    });
  }

  async function pick(rangeKey) {
    dateDialog && dateDialog.close();
    busy = openModal({ title: t('web.preparing_photos'), body: h('div.spinner.center') });
    try {
      const { source } = await sendJSON('POST', `${API}/phone/open`, { range_key: rangeKey });
      busy.close(); busy = null;
      ctx.openSource(source);
    } catch (e) { busy.close(); busy = null; toast(e.message, { kind: 'error' }); }
  }

  const offs = [
    on('scanner', (s) => {
      state.scanner = s;
      phone = s.connection_status === 'connected' ? (phone || s) : null;
      renderList(); renderScanner();
    }),
    on('sources', (e) => setSources(e.sources)),                // USB stick / disc plugged in or removed
    on('device', () => refreshSources()),                       // phone connected
    on('config', () => refreshSources()),                       // administrator toggled a source
    on('bluetooth', (st) => { bt = { enabled: st.enabled, available: st.available, name: st.name }; renderList(); }),
    ctx.onLanguage(() => { b.refresh(); renderList(); renderScanner(); }),
  ];
  state.scanner && (phone = state.scanner.connection_status === 'connected' ? state.scanner : null);
  renderList(); renderScanner();
  refreshSources();

  return {
    destroy() {
      destroyed = true;
      offs.forEach((f) => f());
      dateDialog && dateDialog.close();
      busy && busy.close();
    },
  };
}
