// Thin fetch helpers for the kiosk API.
export const API = '/api/v1/kiosk';

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function request(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { const body = await res.json(); detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail); }
    catch (e) { /* not JSON */ }
    throw new ApiError(res.status, detail);
  }
  return res.status === 204 ? null : res.json();
}

export const getJSON = (url, opts) => request(url, opts);
export const sendJSON = (method, url, body) => request(url, {
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});

export const photoUrl = (sourceId, photoId, what) =>
  `${API}/sources/${encodeURIComponent(sourceId)}/photos/${photoId}/${what}`;
