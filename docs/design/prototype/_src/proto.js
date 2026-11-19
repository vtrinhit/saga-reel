// Prototype SagaReel — tương tác tối thiểu: bảng lệnh, panel Hoạt động, hộp ước tính, phím duyệt, chuyển dạng xem.
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const typing = (e) => e.target.closest('input, textarea, select, [contenteditable="true"]');

  // Đưa chặng đang mở vào giữa thanh chặng (điện thoại cuộn ngang).
  const sp = $('.spine'), cur = $('.stage[aria-current="page"]');
  if (sp && cur) sp.scrollLeft = cur.offsetLeft - (sp.clientWidth - cur.offsetWidth) / 2;

  // Mở dialog bằng data-open="<id>" (Invoker Commands chưa Baseline rộng — dùng showModal()).
  document.addEventListener('click', (e) => {
    const o = e.target.closest('[data-open]');
    if (o) { const d = document.getElementById(o.dataset.open); if (d && !d.open) d.showModal(); }
    const c = e.target.closest('[data-close]');
    if (c) c.closest('dialog')?.close();
  });

  // Toast có Hoàn tác (8 s, dừng đếm khi hover/focus).
  let tt;
  window.toast = (msg) => {
    let t = $('.toast');
    if (!t) {
      t = document.createElement('div'); t.className = 'toast'; t.setAttribute('role', 'status'); t.setAttribute('aria-live', 'polite');
      document.body.append(t);
    }
    const s = document.createElement('span'); s.textContent = msg;
    const u = document.createElement('button'); u.className = 'btn ghost'; u.type = 'button'; u.textContent = 'Hoàn tác';
    t.replaceChildren(s, u);
    t.hidden = false; clearTimeout(tt); tt = setTimeout(() => (t.hidden = true), 8000);
    t.onmouseenter = () => clearTimeout(tt);
    t.querySelector('button').onclick = () => (t.hidden = true);
  };
  $$('[data-toast]').forEach((b) => b.addEventListener('click', () => toast(b.dataset.toast)));

  // SegmentedControl và chuyển dạng xem (data-view="a" ↔ [data-pane="a"]).
  $$('.seg').forEach((g) => g.addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return;
    $$('button', g).forEach((x) => x.setAttribute('aria-checked', String(x === b)));
    if (b.dataset.view) $$('[data-pane]').forEach((p) => (p.hidden = p.dataset.pane !== b.dataset.view));
  }));

  // Chọn hàng trong ma trận / bảng → thanh lô.
  const bar = $('[data-batchbar]');
  const syncBar = () => {
    if (!bar) return;
    const n = $$('[data-pick]:checked').length;
    bar.hidden = n === 0;
    const c = $('[data-count]', bar); if (c) c.textContent = n;
  };
  document.addEventListener('change', (e) => { if (e.target.matches('[data-pick]')) { e.target.closest('tr')?.classList.toggle('sel', e.target.checked); syncBar(); } });
  syncBar();

  // Bảng lệnh Ctrl K (gõ không dấu vẫn khớp).
  const pal = $('#palette');
  const fold = (s) => s.normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/đ/g, 'd').replace(/Đ/g, 'D').toLowerCase();
  if (pal) {
    const inp = $('.pin', pal);
    inp.addEventListener('input', () => {
      const q = fold(inp.value.trim());
      $$('li', pal).forEach((li) => { if (!li.classList.contains('grp')) li.hidden = q && !fold(li.textContent).includes(q); });
    });
  }
  const panel = $('#activity');
  let gPending = 0;
  const gMap = { o: 'overview.html', c: 'story.html', l: 'library.html', t: 'episode.html', d: 'review.html', x: 'export.html', h: 'activity.html', i: 'activity.html' };
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); pal?.showModal(); $('.pin', pal)?.focus(); return; }
    if (typing(e) || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === '.' && panel) { panel.togglePopover(); return; }
    if (document.body.dataset.page === 'studio') return; // G = safe-zone trong Studio
    if (e.key.toLowerCase() === 'g') { gPending = Date.now(); return; }
    if (gPending && Date.now() - gPending < 1200 && gMap[e.key.toLowerCase()]) { location.href = gMap[e.key.toLowerCase()]; }
    gPending = 0;
  });

  // Phím duyệt (chuẩn Task H): 1–9 chọn, A duyệt, R tạo lại, S bỏ qua, J/K clip trước/kế.
  if (document.body.dataset.page === 'review') {
    const takes = $$('.take');
    const pick = (i) => takes.forEach((t, j) => { t.classList.toggle('picked', i === j); $('.take-no', t)?.classList.toggle('mine', i === j); });
    document.addEventListener('keydown', (e) => {
      if (typing(e) || e.ctrlKey || e.metaKey || e.altKey) return;
      const k = e.key.toLowerCase();
      if (/^[1-9]$/.test(k) && takes[+k - 1]) pick(+k - 1);
      else if (k === 'a') toast('Đã chọn take cho Clip 18 — sang Clip 19');
      else if (k === 'r') toast('Đã xếp tạo lại Clip 18 · 1 lượt');
      else if (k === 's') toast('Đã bỏ qua Clip 18');
      else if (k === 'e') $('#prompt')?.focus({ preventScroll: false });
      else return;
      e.preventDefault();
    });
  }
})();
