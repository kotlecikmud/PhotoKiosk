// Segmented language switch (EN | PL ...), identical on every screen.
import { h } from '../dom.js';
import { getAvailableLanguages, getLanguage, onLanguageChange, setLanguage } from '../i18n.js';

export function languageToggle() {
  const langs = getAvailableLanguages();
  const buttons = {};
  const root = h('div.seg.lang-toggle', {}, langs.map(({ code, name }) => {
    const b = h('button.seg-btn', { type: 'button', title: name, onclick: () => setLanguage(code) }, code.toUpperCase());
    buttons[code] = b;
    return b;
  }));
  const paint = () => { for (const [c, b] of Object.entries(buttons)) b.classList.toggle('on', c === getLanguage()); };
  paint();
  const off = onLanguageChange(paint);
  // Remove the listener when this element leaves the DOM.
  new MutationObserver((_, obs) => { if (!root.isConnected) { off(); obs.disconnect(); } })
    .observe(document.body, { childList: true, subtree: true });
  return root;
}
