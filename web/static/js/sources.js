// Human-friendly descriptions of photo sources ("HOLIDAY", "SanDisk Cruzer · E: · 29.8 GB").
import { t } from './i18n.js';

export function formatSize(bytes) {
  if (!bytes || bytes < 0) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = bytes, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return i === 0 ? `${Math.round(v)} B` : `${v.toFixed(1)} ${units[i]}`;
}

// -> { title, subtitle } for a source as sent by the server ({kind,label,device,drive,size})
export function describeSource(s) {
  const size = formatSize(s.size);
  switch (s.kind) {
    case 'shared':
      return { title: t('app.network_shared'), subtitle: t('app.server_storage') };
    case 'removable': {
      const title = s.label || s.device || t('web.unnamed_usb');
      const parts = [t('app.removable_media'), s.device && s.device !== title ? s.device : '', s.drive, size];
      return { title, subtitle: parts.filter(Boolean).join(' · ') };
    }
    case 'optical': {
      const title = s.label || t('web.unnamed_disc');
      return { title, subtitle: [t('app.optical_media'), s.drive, size].filter(Boolean).join(' · ') };
    }
    case 'bluetooth':
      return { title: t('bluetooth.row_title'), subtitle: t('bluetooth.source_type') };
    default:
      return { title: s.label || '', subtitle: t('app.connected_mobile') };
  }
}
