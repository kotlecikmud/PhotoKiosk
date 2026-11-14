// Tiny i18n runtime. Dictionaries come from the server (config/locales/*.json), already
// merged with English so a missing translation never shows a raw key.
import { getJSON } from './api.js';

const dicts = {};
const listeners = new Set();
let current = 'en';
let available = [];

// "{name}" and Python-style "{elapsed:.1f}" placeholders.
export function format(str, params) {
  if (!params) return str;
  return str.replace(/\{(\w+)(?::\.(\d+)f)?\}/g, (m, key, digits) => {
    if (!(key in params)) return m;
    return digits !== undefined ? Number(params[key]).toFixed(Number(digits)) : String(params[key]);
  });
}

export function t(key, params) {
  const d = dicts[current] || {};
  const val = key in d ? d[key] : (params && 'default' in params ? params.default : key);
  return format(val, params);
}

export async function loadLanguage(code) {
  if (!dicts[code]) dicts[code] = (await getJSON(`/api/v1/kiosk/i18n/${code}`)).strings;
  return code;
}

export function initI18n(languages, code) {
  available = languages;
  current = code;
}

export async function setLanguage(code) {
  await loadLanguage(code);
  current = code;
  try { localStorage.setItem('pk.lang', code); } catch (e) { /* private mode */ }
  document.documentElement.lang = code;
  listeners.forEach((fn) => fn(code));
}

export const getLanguage = () => current;
export const getAvailableLanguages = () => available;
export function onLanguageChange(fn) { listeners.add(fn); return () => listeners.delete(fn); }

export function money(value, currency = 'PLN') { return `${value.toFixed(2)} ${currency}`; }
