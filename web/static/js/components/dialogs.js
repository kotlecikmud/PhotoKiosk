// Modal dialogs and toasts.
import { h } from '../dom.js';
import { t } from '../i18n.js';

const modalRoot = () => document.getElementById('modal-root');
const toastRoot = () => document.getElementById('toast-root');

// openModal({ title, subtitle, body, actions:[{label, kind, onClick}], onClose })
// -> { close(), el }
export function openModal({ title, subtitle, body, actions = [], onClose, wide = false }) {
  const close = () => { backdrop.remove(); onClose && onClose(); };
  const buttons = actions.map((a) => h(`button.btn.${a.kind || 'secondary'}`, {
    onclick: () => { if (a.onClick) a.onClick(); if (!a.keepOpen) close(); },
  }, a.label));
  const dialog = h('div.modal' + (wide ? '.wide' : ''), { role: 'dialog', 'aria-modal': 'true' },
    title && h('h2.modal-title', {}, title),
    subtitle && h('p.modal-sub', {}, subtitle),
    body && h('div.modal-body', {}, body),
    buttons.length && h('div.modal-actions', {}, buttons));
  const backdrop = h('div.modal-backdrop', {}, dialog);
  modalRoot().append(backdrop);
  return { close, el: dialog };
}

export function confirmDialog({ title, message, confirmLabel, cancelLabel, danger = false }) {
  return new Promise((resolve) => {
    openModal({
      title, subtitle: message,
      actions: [
        { label: cancelLabel || t('web.cancel'), kind: 'secondary', onClick: () => resolve(false) },
        { label: confirmLabel || t('web.ok'), kind: danger ? 'danger' : 'primary', onClick: () => resolve(true) },
      ],
    });
  });
}

// toast(text, { actionLabel, onAction, kind, ms })
export function toast(text, { actionLabel, onAction, kind = 'info', ms = 5000 } = {}) {
  const remove = () => el.remove();
  const el = h(`div.toast.${kind}`, { role: 'status' },
    h('span', {}, text),
    actionLabel && h('button.btn.primary.sm', { onclick: () => { remove(); onAction && onAction(); } }, actionLabel),
    h('button.toast-x', { 'aria-label': t('web.close'), onclick: remove }, '✕'));
  toastRoot().append(el);
  if (ms) setTimeout(remove, ms);
  return { remove };
}
