/* search.js — wyszukiwarka globalna (pasek w topbarze + strona /search).
 *
 * - Wyniki lokalne (pokoje, przedmioty, terminy, materiały, załączniki,
 *   wiadomości, userzy) przychodzą z cfg.api, pliki/foldery z FileVault z
 *   osobnego cfg.fvApi (zdalne API bywa wolne — nie blokuje reszty).
 * - Skróty: "/" albo Ctrl/Cmd+K — fokus; ↑/↓ — nawigacja; Enter — otwórz
 *   (bez zaznaczenia: pełna strona wyników); Esc — zamknij.
 * - Cały tekst z serwera jest escapowany; do atrybutów trafiają tylko
 *   indeksy, nigdy surowe dane z bazy.
 */
(function () {
  'use strict';
  const cfg = window.KOLOSEUM_SEARCH;
  if (!cfg) return;

  const MIN_LEN = 2;
  const DEBOUNCE_MS = 250;

  const form = document.getElementById('gsearch');
  const input = document.getElementById('gsearch-input');
  const panel = document.getElementById('gsearch-panel');
  const toggleBtn = document.getElementById('gsearch-toggle');
  const closeBtn = document.getElementById('gsearch-close');
  const kbd = document.getElementById('gsearch-kbd');

  // ---------- helpers ----------
  function esc(str) {
    return String(str == null ? '' : str)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function hl(text, q) {
    text = String(text == null ? '' : text);
    const i = q ? text.toLowerCase().indexOf(q.toLowerCase()) : -1;
    if (i < 0) return esc(text);
    return esc(text.slice(0, i)) + '<mark>' + esc(text.slice(i, i + q.length)) + '</mark>' + esc(text.slice(i + q.length));
  }
  function toast(msg, type) {
    if (typeof showToast === 'function') showToast(msg, type || 'danger');
    else alert(msg);
  }
  function getJson(url, q, limit, signal) {
    const u = url + '?q=' + encodeURIComponent(q) + (limit ? '&limit=' + limit : '');
    return fetch(u, { signal, headers: { 'Accept': 'application/json' }, credentials: 'same-origin' })
      .then(r => r.json().catch(() => ({})).then(data => ({ status: r.status, data })));
  }

  // ---------- renderowanie wspólne (panel + strona wyników) ----------
  // Wyniki trzymamy w rejestrze (key -> item), a w DOM jest tylko klucz —
  // dzięki temu akcje (DM, FileVault) nie wymagają wstrzykiwania danych w HTML.
  const registry = new Map();
  let keySeq = 0;

  function itemHtml(it, q) {
    const key = 'k' + (++keySeq);
    registry.set(key, it);
    const color = it.color ? ` style="color:${esc(it.color)}"` : '';
    const href = it.action ? '#' : esc(it.url);
    return `<a class="gsearch-item" role="option" href="${href}" data-key="${key}">
      <span class="gsearch-item-icon"><i class="fas fa-${esc(it.icon || 'file')}"${color}></i></span>
      <span class="gsearch-item-text">
        <span class="gsearch-item-title">${hl(it.title, q)}</span>
        ${it.subtitle ? `<span class="gsearch-item-sub">${esc(it.subtitle)}</span>` : ''}
      </span>
    </a>`;
  }
  function sectionsHtml(results, labels, q) {
    return Object.keys(results || {}).map(cat => `
      <div class="gsearch-section" role="group" aria-label="${esc(labels[cat] || cat)}">
        <div class="gsearch-section-title">${esc(labels[cat] || cat)}</div>
        ${results[cat].map(it => itemHtml(it, q)).join('')}
      </div>`).join('');
  }
  const countOf = r => Object.values(r || {}).reduce((n, a) => n + a.length, 0);

  // Wrapper dostępny w całym module — właściwa implementacja siedzi w bloku
  // panelu niżej (patrz window.closeSearchPanel).
  function closePanel(clear) {
    if (typeof window.closeSearchPanel === 'function') window.closeSearchPanel(clear);
  }

  // ---------- akcje ----------
  function openDm(userId, name) {
    // Otwieramy panel czatu od razu na rozmowie. chatConversation=null +
    // chatStopPolling() czyści ewentualną poprzednią rozmowę.
    if (typeof openChatPanel !== 'function') return;
    if (typeof chatStopPolling === 'function') chatStopPolling();
    chatConversation = null;
    openChatPanel();
    openDmConversation(userId, name);
  }
  function shareThen(urlTpl, id, cb) {
    fetch(urlTpl.replace(/\/0$/, '/' + id), { method: 'POST', credentials: 'same-origin' })
      .then(r => r.json())
      .then(d => d.share_url ? cb(d.share_url) : toast('Nie udało się otworzyć elementu z FileVault.'))
      .catch(() => toast('Błąd połączenia z FileVault.'));
  }
  function runAction(it) {
    switch (it.action) {
      case 'dm':
        openDm(it.user_id, it.title);
        break;
      case 'fv_file': {
        const open = url => it.previewable && typeof openFvPreviewModal === 'function'
          ? openFvPreviewModal(url + '/preview', it.name, url)
          : window.open(url, '_blank', 'noopener');
        it.share_url ? open(it.share_url) : shareThen(FV_QUICK_SHARE_FILE_URL_TPL, it.id, url => { it.share_url = url; open(url); });
        break;
      }
      case 'fv_folder':
        it.share_url ? window.open(it.share_url, '_blank', 'noopener')
                     : shareThen(FV_QUICK_SHARE_FOLDER_URL_TPL, it.id, url => { it.share_url = url; window.open(url, '_blank', 'noopener'); });
        break;
      case 'fv_room_file':
        if (it.preview_url && typeof openFvPreviewModal === 'function') openFvPreviewModal(it.preview_url, it.name, it.download_url);
        else if (it.download_url) window.open(it.download_url, '_blank', 'noopener');
        break;
    }
  }
  // Delegowany klik — działa i w panelu, i na stronie /search.
  document.addEventListener('click', function (e) {
    const el = e.target.closest('.gsearch-item[data-key]');
    if (!el) return;
    const it = registry.get(el.dataset.key);
    if (it && it.action) {
      e.preventDefault();
      closePanel(true);
      runAction(it);
    } else {
      closePanel(true);
    }
  });
  // Na stronie /search userzy renderowani przez serwer (data-dm-user).
  document.addEventListener('click', function (e) {
    const el = e.target.closest('[data-dm-user]');
    if (!el) return;
    e.preventDefault();
    openDm(parseInt(el.dataset.dmUser, 10), el.dataset.dmName || '');
  });

  // ---------- panel w topbarze ----------
  if (form && input && panel) {
    let timer = null;
    let seq = 0;
    let ctrls = [];
    let active = -1;
    let last = { q: '', local: null, fv: null, fvState: 'idle' };

    const isMobile = () => window.matchMedia('(max-width: 768px)').matches;

    function items() { return Array.from(panel.querySelectorAll('.gsearch-item')); }
    function setActive(i) {
      const list = items();
      list.forEach(el => { el.classList.remove('active'); el.removeAttribute('aria-selected'); });
      active = list.length ? (i + list.length) % list.length : -1;
      if (active >= 0) {
        list[active].classList.add('active');
        list[active].setAttribute('aria-selected', 'true');
        list[active].scrollIntoView({ block: 'nearest' });
      }
    }
    function showPanel(html) {
      panel.innerHTML = html;
      panel.hidden = false;
      input.setAttribute('aria-expanded', 'true');
      active = -1;
    }
    function closePanel(clear) {
      if (!panel) return;
      panel.hidden = true;
      input.setAttribute('aria-expanded', 'false');
      active = -1;
      if (clear === true) {
        input.blur();
        form.classList.remove('open');
      }
    }
    window.closeSearchPanel = closePanel;

    function render() {
      const q = last.q;
      if (q.length < MIN_LEN) {
        showPanel(`<div class="gsearch-hint">Wpisz co najmniej ${MIN_LEN} znaki…</div>`);
        return;
      }
      registry.clear();
      const local = last.local || {};
      const fv = last.fv || {};
      let html = sectionsHtml(local.results, local.labels || {}, q) +
                 sectionsHtml(fv.results, fv.labels || {}, q);
      const total = countOf(local.results) + countOf(fv.results);

      if (last.fvState === 'loading') {
        html += '<div class="gsearch-hint"><i class="fas fa-spinner fa-spin"></i> Szukam w FileVault…</div>';
      } else if (last.fvState === 'error') {
        html += '<div class="gsearch-hint gsearch-hint-muted"><i class="fas fa-plug"></i> FileVault jest teraz niedostępny.</div>';
      }
      if (!total && last.local && last.fvState !== 'loading') {
        html = `<div class="gsearch-empty"><i class="fas fa-magnifying-glass"></i>Brak wyników dla „${esc(q)}”.</div>` + html;
      } else if (!last.local) {
        html = '<div class="gsearch-hint"><i class="fas fa-spinner fa-spin"></i> Szukam…</div>' + html;
      }
      if (last.local) {
        html += `<a class="gsearch-all" href="${esc(cfg.page)}?q=${encodeURIComponent(q)}">
          <i class="fas fa-arrow-right"></i> Pokaż wszystkie wyniki dla „${esc(q)}”</a>`;
      }
      const prev = active;
      showPanel(html);
      if (prev >= 0) setActive(prev);
    }

    function run(q) {
      ctrls.forEach(c => c.abort());
      ctrls = [];
      const my = ++seq;
      last = { q, local: null, fv: null, fvState: 'idle' };
      if (q.length < MIN_LEN) { render(); return; }

      last.fvState = 'loading';
      render();

      const c1 = new AbortController(), c2 = new AbortController();
      ctrls = [c1, c2];

      getJson(cfg.api, q, 6, c1.signal).then(({ data }) => {
        if (my !== seq) return;
        last.local = data || { results: {}, labels: {} };
        render();
      }).catch(err => {
        if (err.name === 'AbortError' || my !== seq) return;
        last.local = { results: {}, labels: {} };
        render();
      });

      getJson(cfg.fvApi, q, 5, c2.signal).then(({ status, data }) => {
        if (my !== seq) return;
        if (status === 200) { last.fv = data; last.fvState = 'done'; }
        // Konto niepołączone z FileVault — po prostu pomijamy sekcję, bez
        // straszenia błędem (to normalna sytuacja dla userów logujących hasłem).
        else if (status === 400) { last.fvState = 'done'; }
        else { last.fvState = 'error'; }
        render();
      }).catch(err => {
        if (err.name === 'AbortError' || my !== seq) return;
        last.fvState = 'error';
        render();
      });
    }

    function schedule() {
      clearTimeout(timer);
      const q = input.value.replace(/\s+/g, ' ').trim();
      if (!q) { ctrls.forEach(c => c.abort()); seq++; closePanel(); return; }
      timer = setTimeout(() => run(q), DEBOUNCE_MS);
    }

    input.addEventListener('input', schedule);
    input.addEventListener('focus', () => {
      if (kbd) kbd.style.visibility = 'hidden';
      if (input.value.trim().length >= MIN_LEN && panel.innerHTML) panel.hidden = false;
    });
    input.addEventListener('blur', () => { if (kbd) kbd.style.visibility = ''; });

    input.addEventListener('keydown', e => {
      if (e.key === 'ArrowDown') { e.preventDefault(); if (panel.hidden) schedule(); setActive(active + 1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(active < 0 ? -1 : active - 1); }
      else if (e.key === 'Enter') {
        const list = items();
        if (active >= 0 && list[active]) { e.preventDefault(); list[active].click(); }
        // bez zaznaczenia: zwykły submit formularza -> /search?q=...
      } else if (e.key === 'Escape') {
        if (!panel.hidden) { e.preventDefault(); closePanel(); }
        else { input.blur(); form.classList.remove('open'); }
      }
    });

    // Klik poza wyszukiwarką zamyka panel.
    document.addEventListener('mousedown', e => {
      if (!form.contains(e.target) && !(toggleBtn && toggleBtn.contains(e.target))) {
        closePanel();
        if (isMobile()) form.classList.remove('open');
      }
    });

    // Skróty: "/" i Ctrl/Cmd+K.
    document.addEventListener('keydown', e => {
      const t = e.target;
      const typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
      const isK = (e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'k';
      const isSlash = e.key === '/' && !e.ctrlKey && !e.metaKey && !e.altKey && !typing;
      if (isK || isSlash) {
        e.preventDefault();
        openSearch();
      }
    });

    function openSearch() {
      if (isMobile()) form.classList.add('open');
      input.focus();
      input.select();
      if (input.value.trim().length >= MIN_LEN && panel.innerHTML) panel.hidden = false;
    }
    if (toggleBtn) toggleBtn.addEventListener('click', openSearch);
    if (closeBtn) closeBtn.addEventListener('click', () => { closePanel(); form.classList.remove('open'); input.blur(); });

    // Na stronie /search wypełnij pasek bieżącym zapytaniem (bez otwierania panelu).
    const pq = new URLSearchParams(location.search).get('q');
    if (pq && document.getElementById('search-page')) input.value = pq;
  }

  // ---------- strona /search: dociągnij FileVault ----------
  const pageFv = document.getElementById('search-page-fv');
  if (pageFv) {
    const q = pageFv.dataset.q || '';
    if (q.length >= MIN_LEN) {
      pageFv.innerHTML = '<div class="gsearch-hint"><i class="fas fa-spinner fa-spin"></i> Szukam w FileVault…</div>';
      getJson(cfg.fvApi, q, 30).then(({ status, data }) => {
        if (status === 200 && countOf(data.results)) {
          pageFv.innerHTML = sectionsHtml(data.results, data.labels, q);
        } else if (status === 200 || status === 400) {
          pageFv.innerHTML = '';
        } else {
          pageFv.innerHTML = '<div class="gsearch-hint gsearch-hint-muted"><i class="fas fa-plug"></i> FileVault jest teraz niedostępny.</div>';
        }
        const empty = document.getElementById('search-page-empty');
        if (empty) empty.hidden = !!pageFv.innerHTML.trim() ? true : empty.dataset.localEmpty !== '1';
      }).catch(() => { pageFv.innerHTML = ''; });
    }
  }
})();
