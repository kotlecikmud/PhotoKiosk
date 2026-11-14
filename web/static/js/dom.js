// Minimal DOM helpers.

// h('div.card.active', { onclick, 'data-id': 1 }, 'text', childNode, ...)
export function h(tag, attrs, ...children) {
  const [name, ...classes] = tag.split('.');
  const el = document.createElement(name || 'div');
  if (classes.length) el.className = classes.join(' ');
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.classList.add(...String(v).split(' '));
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

// Set text only when it changed (avoids needless layout / flicker).
export function setText(el, text) {
  if (el.textContent !== text) el.textContent = text;
}

// Collects text bindings so a language switch relabels widgets in place (no rebuild).
export class Binder {
  constructor() { this.fns = []; }
  text(el, fn) { this.fns.push(() => setText(el, fn())); this.fns.at(-1)(); return el; }
  attr(el, name, fn) { this.fns.push(() => el.setAttribute(name, fn())); this.fns.at(-1)(); return el; }
  run(fn) { this.fns.push(fn); fn(); }
  refresh() { this.fns.forEach((f) => f()); }
}

export const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
export const raf = (fn) => requestAnimationFrame(fn);

export function debounce(fn, ms) {
  let t = null;
  const wrapped = (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  wrapped.cancel = () => clearTimeout(t);
  wrapped.flush = (...a) => { clearTimeout(t); fn(...a); };
  return wrapped;
}

export function uid(n = 16) {
  const a = new Uint8Array(n / 2);
  crypto.getRandomValues(a);
  return [...a].map((b) => b.toString(16).padStart(2, '0')).join('');
}
