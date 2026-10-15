'use strict';
// Display timestamps in the browser's local timezone with a fixed, 24-hour format.
window.CorkiDateTime = value => {
  if (!value) return '—';
  const text = String(value);
  const normalized = text.includes('T') ? text : text.replace(' ', 'T') + 'Z';
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return '—';
  const pad = n => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
};
