// Prioritised, cancellable thumbnail loader.
//
// Browsers allow ~6 connections per host and the live-event stream permanently uses one, so
// we cap thumbnail downloads at 4. That keeps a slot free for print previews, which must
// never queue behind hundreds of thumbnails. Tiles that scroll out of view are cancelled.
export class ThumbLoader {
  constructor(maxConcurrent = 4) {
    this.max = maxConcurrent;
    this.active = new Map();   // key -> { img }
    this.queue = [];           // { key, url, priority, onLoad, onError }
  }

  request(key, url, priority, onLoad, onError) {
    if (this.active.has(key) || this.queue.some((j) => j.key === key)) return;
    this.queue.push({ key, url, priority, onLoad, onError });
    this._pump();
  }

  cancel(key) {
    const qi = this.queue.findIndex((j) => j.key === key);
    if (qi >= 0) this.queue.splice(qi, 1);
    const a = this.active.get(key);
    if (a) {
      a.img.onload = a.img.onerror = null;
      a.img.src = '';          // aborts the in-flight request
      this.active.delete(key);
      this._pump();
    }
  }

  clear() {
    [...this.active.keys()].forEach((k) => this.cancel(k));
    this.queue.length = 0;
  }

  _pump() {
    while (this.active.size < this.max && this.queue.length) {
      let best = 0;
      for (let i = 1; i < this.queue.length; i++) if (this.queue[i].priority < this.queue[best].priority) best = i;
      const job = this.queue.splice(best, 1)[0];
      const img = new Image();
      img.decoding = 'async';
      img.draggable = false;
      this.active.set(job.key, { img });
      img.onload = () => { this.active.delete(job.key); job.onLoad(img); this._pump(); };
      img.onerror = () => { this.active.delete(job.key); job.onError && job.onError(); this._pump(); };
      img.src = job.url;
    }
  }
}
