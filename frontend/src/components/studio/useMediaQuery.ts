import { useEffect, useState } from 'react';

/** `matchMedia` theo dõi được (jsdom không có → false). */
export function useMediaQuery(query: string): boolean {
  const get = () => (typeof window !== 'undefined' && window.matchMedia ? window.matchMedia(query).matches : false);
  const [match, setMatch] = useState(get);
  useEffect(() => {
    if (!window.matchMedia) return;
    const mq = window.matchMedia(query);
    const on = () => setMatch(mq.matches);
    on();
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, [query]);
  return match;
}

/** Phím đơn chữ không chạy khi focus đang ở ô nhập (DESIGN §7.2). */
export function isTypingTarget(el: EventTarget | null): boolean {
  const t = el as HTMLElement | null;
  if (!t || !t.tagName) return false;
  if (t.isContentEditable) return true;
  if (t.tagName === 'TEXTAREA' || t.tagName === 'SELECT') return true;
  if (t.tagName === 'INPUT') {
    const type = (t as HTMLInputElement).type;
    return !['checkbox', 'radio', 'button', 'range', 'submit'].includes(type);
  }
  return false;
}

/** Có modal/drawer đang mở (dialog `:modal` hoặc `aria-modal`) → phím Studio nhường. */
export function modalOpen(): boolean {
  try {
    return !!document.querySelector('dialog[open]:modal, [aria-modal="true"]');
  } catch {
    return !!document.querySelector('dialog[open], [aria-modal="true"]');
  }
}
