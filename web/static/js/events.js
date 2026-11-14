// Live server events (Server-Sent Events): device plug/unplug, phone-scan progress, new photos.
// EventSource reconnects by itself; we only surface the connection state and detect restarts.
import { API } from './api.js';

const listeners = {};
let bootId = null;
let es = null;

export function on(event, fn) {
  (listeners[event] ||= new Set()).add(fn);
  return () => listeners[event].delete(fn);
}

const emit = (event, data) => (listeners[event] || []).forEach((fn) => { try { fn(data); } catch (e) { console.error(e); } });

export function connect(expectedBootId) {
  bootId = expectedBootId;
  es = new EventSource(`${API}/events`);
  es.addEventListener('hello', (e) => {
    const data = JSON.parse(e.data);
    // The server restarted (e.g. after an update): reload to pick up the new UI and catalog.
    if (bootId && data.boot_id !== bootId) { location.reload(); return; }
    emit('connection', true);
    emit('scanner', data.scanner);
  });
  for (const name of ['scanner', 'device', 'library']) {
    es.addEventListener(name, (e) => emit(name, JSON.parse(e.data)));
  }
  es.onerror = () => emit('connection', false);
}
