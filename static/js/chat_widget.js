// ===== KOLOSEUM — Panel czatu "z boku" (pokoje/przedmioty, wiadomości
// prywatne, zgłoszenia problemów) =====
// Wzorowane na panelu FileVault (patrz templates/base.html: #fv-drawer) —
// ta sama konwencja wysuwanego z lewej panelu, dostępnego z każdej strony.
// Odświeżanie wiadomości działa przez zwykłe pollowanie (fetch co kilka
// sekund), bez WebSocketów — spójnie z resztą appki (patrz NotificationManager
// w app.js), i bezpiecznie względem obecnej konfiguracji Gunicorn/Tailscale.

let chatOpen = false;
let chatActiveTab = 'rooms';
let chatPanelData = null;      // wynik /chat/panel-data: {rooms, contacts, unread_total, open_tickets_admin}
let chatConversation = null;   // {type: 'subject'|'dm'|'ticket', id, name, lastId, lastDayKey}
let chatPollTimer = null;
let chatBadgePollTimer = null;
let chatTicketsCache = null;

function openChatPanel() {
  document.getElementById('chat-backdrop').classList.add('open');
  document.getElementById('chat-drawer').classList.add('open');
  document.body.style.overflow = 'hidden';
  chatOpen = true;
  chatLoadPanelData();
  if (!chatBadgePollTimer) {
    chatBadgePollTimer = setInterval(chatUpdateUnreadBadge, 30000);
  }
}

function closeChatPanel() {
  document.getElementById('chat-backdrop').classList.remove('open');
  document.getElementById('chat-drawer').classList.remove('open');
  document.body.style.overflow = '';
  chatOpen = false;
  chatStopPolling();
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && chatOpen) closeChatPanel();
});

function chatEscapeHtml(str) {
  return String(str == null ? '' : str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function chatEscapeJs(str) {
  return String(str == null ? '' : str).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}

// ---------------------------------------------------------------------
// Ładowanie danych panelu (drzewo pokój->przedmioty + kontakty do DM)
// ---------------------------------------------------------------------
function chatLoadPanelData() {
  const body = document.getElementById('chat-drawer-body');
  body.style.padding = '';
  document.getElementById('chat-back-btn').style.display = 'none';
  document.getElementById('chat-drawer-tabs').style.display = 'flex';
  body.innerHTML = '<div class="fv-loading"><i class="fas fa-spinner fa-spin"></i> Ładowanie…</div>';

  fetch(CHAT_PANEL_DATA_URL)
    .then(r => r.json())
    .then(data => {
      chatPanelData = data;
      chatUpdateBadges(data.unread_total, data.open_tickets_admin);
      chatRenderList();
    })
    .catch(() => {
      body.innerHTML = '<div class="fv-empty fv-empty-danger">Nie udało się wczytać czatu.</div>';
    });
}

function switchChatTab(tab) {
  chatActiveTab = tab;
  document.getElementById('chat-tab-rooms').classList.toggle('active', tab === 'rooms');
  document.getElementById('chat-tab-dm').classList.toggle('active', tab === 'dm');
  document.getElementById('chat-tab-tickets').classList.toggle('active', tab === 'tickets');
  chatRenderList();
}

function chatRenderList() {
  const body = document.getElementById('chat-drawer-body');
  if (chatActiveTab === 'rooms') {
    if (!chatPanelData) return;
    body.innerHTML = chatRoomsListHtml(chatPanelData.rooms);
  } else if (chatActiveTab === 'dm') {
    if (!chatPanelData) return;
    body.innerHTML = chatContactsListHtml(chatPanelData.contacts);
  } else {
    chatLoadTicketsList();
  }
}

function chatRoomsListHtml(rooms) {
  if (!rooms || !rooms.length) {
    return `<div class="fv-empty"><i class="fas fa-door-open"></i>
      <p>Nie należysz jeszcze do żadnego pokoju — dołącz do pokoju, żeby zobaczyć tu czaty przedmiotów.</p></div>`;
  }
  return rooms.map(room => {
    const subjectsHtml = room.subjects.length
      ? room.subjects.map(s => `
        <div class="fv-item" onclick="openSubjectConversation(${s.id}, '${chatEscapeJs(s.name)}')">
          <div class="fv-thumb-icon" style="color:${chatEscapeHtml(s.color || '#4f46e5')}"><i class="fas fa-comment-dots"></i></div>
          <div class="fv-item-info">
            <div class="fv-item-name">${chatEscapeHtml(s.name)}</div>
          </div>
          <i class="fas fa-chevron-right fv-chevron"></i>
        </div>`).join('')
      : `<div class="fv-empty" style="padding:14px 8px;font-size:.78rem">Brak przedmiotów w tym pokoju.</div>`;
    return `
      <div class="fv-room-header"><i class="fas fa-door-open"></i> ${chatEscapeHtml(room.name)}
        <span class="fv-room-count">${room.subjects.length}</span>
      </div>
      <div class="fv-list">${subjectsHtml}</div>`;
  }).join('');
}

function chatContactsListHtml(contacts) {
  if (!contacts || !contacts.length) {
    return `<div class="fv-empty"><i class="fas fa-user-friends"></i>
      <p>Nie masz jeszcze z kim pisać na priv — wiadomości prywatne działają między osobami z tego samego pokoju.</p></div>`;
  }
  return '<div class="fv-list">' + contacts.map(c => `
    <div class="fv-item" onclick="openDmConversation(${c.id}, '${chatEscapeJs(c.username)}')">
      <div class="user-avatar" style="width:36px;height:36px;font-size:.85rem;flex-shrink:0">${chatEscapeHtml(c.username[0].toUpperCase())}</div>
      <div class="fv-item-info">
        <div class="fv-item-name">${chatEscapeHtml(c.username)}</div>
        ${c.last_message
          ? `<div class="fv-item-meta"><span>${chatEscapeHtml(c.last_message)}</span></div>`
          : `<div class="fv-item-meta"><span>Brak wiadomości — napisz pierwszy!</span></div>`}
      </div>
      ${c.unread > 0 ? `<span class="badge chat-unread-count">${c.unread}</span>` : ''}
    </div>`).join('') + '</div>';
}

// ---------------------------------------------------------------------
// Widok rozmowy (przedmiot / DM / ticket) — wspólny szkielet
// ---------------------------------------------------------------------
function chatShowList() {
  chatStopPolling();
  const wasTicket = chatConversation && chatConversation.type === 'ticket';
  chatConversation = null;
  document.getElementById('chat-back-btn').style.display = 'none';
  document.getElementById('chat-drawer-tabs').style.display = 'flex';
  document.getElementById('chat-drawer-body').style.padding = '';
  if (wasTicket) chatActiveTab = 'tickets';
  chatRenderList();
  // Odśwież listę kontaktów/nieprzeczytanych po ewentualnej rozmowie DM.
  chatLoadPanelData();
}

function chatConversationSkeleton(title, subtitleHtml, extraHeaderHtml) {
  document.getElementById('chat-back-btn').style.display = 'flex';
  document.getElementById('chat-drawer-tabs').style.display = 'none';
  const body = document.getElementById('chat-drawer-body');
  body.style.padding = '0';
  body.innerHTML = `
    <div class="chat-conv">
      <div class="chat-conv-header">
        <div class="chat-conv-title">${chatEscapeHtml(title)}</div>
        ${subtitleHtml ? `<div class="chat-conv-subtitle">${subtitleHtml}</div>` : ''}
        ${extraHeaderHtml || ''}
      </div>
      <div class="chat-conv-messages" id="chat-conv-messages">
        <div class="fv-loading"><i class="fas fa-spinner fa-spin"></i> Ładowanie…</div>
      </div>
      <form class="chat-conv-input" id="chat-conv-form" onsubmit="return chatSendCurrent(event)">
        <input type="text" id="chat-conv-text" maxlength="4000" autocomplete="off" placeholder="Napisz wiadomość…">
        <button type="submit" class="btn btn-primary btn-sm"><i class="fas fa-paper-plane"></i></button>
      </form>
    </div>`;
}

// ── Separator dnia — patrz utils/timezone.py: day_label() ──
function chatDividerHtml(label) {
  return `<div class="chat-day-divider"><span>${chatEscapeHtml(label)}</span></div>`;
}

function chatAppendWithDividers(listEl, items, bubbleFn) {
  items.forEach(item => {
    if (item.day_key && item.day_key !== chatConversation.lastDayKey) {
      listEl.insertAdjacentHTML('beforeend', chatDividerHtml(item.day_label));
      chatConversation.lastDayKey = item.day_key;
    }
    listEl.insertAdjacentHTML('beforeend', bubbleFn(item));
  });
}

// ---------------------------------------------------------------------
// Czat przedmiotu / DM
// ---------------------------------------------------------------------
function openSubjectConversation(subjectId, name) {
  chatConversation = { type: 'subject', id: subjectId, name: name, lastId: 0, lastDayKey: null };
  chatConversationSkeleton(name, 'Czat grupowy przedmiotu');
  chatFetchMessages(true);
  chatStartPolling();
}

function openDmConversation(userId, username) {
  chatConversation = { type: 'dm', id: userId, name: username, lastId: 0, lastDayKey: null };
  chatConversationSkeleton(username, 'Wiadomość prywatna');
  chatFetchMessages(true);
  chatStartPolling();
  // Otwarcie rozmowy oznacza wiadomości jako przeczytane po stronie serwera —
  // odśwież lokalnie widoczne liczniki nieprzeczytanych.
  setTimeout(chatUpdateUnreadBadge, 400);
}

function chatMessagesUrl(after) {
  const c = chatConversation;
  const base = c.type === 'subject'
    ? CHAT_SUBJECT_MESSAGES_URL_TPL.replace('/0', '/' + c.id)
    : CHAT_DM_MESSAGES_URL_TPL.replace('/0', '/' + c.id);
  return after ? `${base}?after_id=${after}` : base;
}

function chatFetchMessages(initial) {
  if (!chatConversation) return;
  const c = chatConversation;
  fetch(chatMessagesUrl(initial ? null : c.lastId))
    .then(r => r.json())
    .then(data => {
      if (!chatConversation || chatConversation.id !== c.id || chatConversation.type !== c.type) return;
      const list = document.getElementById('chat-conv-messages');
      if (!list) return;
      if (initial) { list.innerHTML = ''; chatConversation.lastDayKey = null; }
      if (!data.messages || !data.messages.length) {
        if (initial) list.innerHTML = `<div class="fv-empty" style="padding:30px 10px">
          <i class="fas fa-comment-slash"></i><p>Brak wiadomości. Napisz pierwszą!</p></div>`;
        return;
      }
      const wasAtBottom = list.scrollTop + list.clientHeight >= list.scrollHeight - 30;
      chatAppendWithDividers(list, data.messages, m => chatMessageBubbleHtml(m, c.type));
      data.messages.forEach(m => { chatConversation.lastId = Math.max(chatConversation.lastId, m.id); });
      if (initial || wasAtBottom) list.scrollTop = list.scrollHeight;
    })
    .catch(() => {});
}

function chatMessageBubbleHtml(m, type) {
  const mine = m.mine;
  const author = type === 'subject' && !mine ? `<div class="chat-bubble-author">${chatEscapeHtml(m.author.username)}</div>` : '';
  return `<div class="chat-bubble-row ${mine ? 'mine' : ''}">
    <div class="chat-bubble">
      ${author}
      <div class="chat-bubble-text">${chatEscapeHtml(m.content)}</div>
      <div class="chat-bubble-time">${chatEscapeHtml(m.created_at)}</div>
    </div>
  </div>`;
}

// ---------------------------------------------------------------------
// Zgłoszenia problemów ("Zgłoś problem")
// ---------------------------------------------------------------------
const TICKET_STATUS_LABELS = { open: 'Otwarte', in_progress: 'W trakcie', closed: 'Zamknięte' };
const TICKET_STATUS_CLASS = { open: 'badge-danger', in_progress: 'badge-warning', closed: 'badge-gray' };

function chatLoadTicketsList() {
  const body = document.getElementById('chat-drawer-body');
  body.innerHTML = '<div class="fv-loading"><i class="fas fa-spinner fa-spin"></i> Ładowanie…</div>';
  fetch(CHAT_TICKETS_URL)
    .then(r => r.json())
    .then(data => {
      chatTicketsCache = data.tickets || [];
      body.innerHTML = chatTicketsListHtml(chatTicketsCache);
    })
    .catch(() => {
      body.innerHTML = '<div class="fv-empty fv-empty-danger">Nie udało się wczytać zgłoszeń.</div>';
    });
}

function chatTicketsListHtml(tickets) {
  const newBtn = `
    <div class="fv-item" style="border:1px dashed var(--border);margin-bottom:8px" onclick="chatShowNewTicketForm()">
      <div class="fv-thumb-icon" style="color:var(--primary)"><i class="fas fa-plus"></i></div>
      <div class="fv-item-info"><div class="fv-item-name">Zgłoś nowy problem</div></div>
    </div>`;
  if (!tickets || !tickets.length) {
    return newBtn + `<div class="fv-empty"><i class="fas fa-life-ring"></i>
      <p>Brak zgłoszeń. Jeśli coś w appce nie działa — zgłoś to tutaj.</p></div>`;
  }
  const list = tickets.map(t => `
    <div class="fv-item" onclick="openTicketThread(${t.id})">
      <div class="fv-thumb-icon"><i class="fas fa-ticket-alt"></i></div>
      <div class="fv-item-info">
        <div class="fv-item-name">${chatEscapeHtml(t.title)}</div>
        <div class="fv-item-meta">
          <span>${CHAT_CURRENT_USER_IS_ADMIN ? chatEscapeHtml(t.reporter_username) + ' • ' : ''}${chatEscapeHtml(t.updated_at)}</span>
          ${t.reply_count > 0 ? `<span><i class="fas fa-reply"></i> ${t.reply_count}</span>` : ''}
        </div>
      </div>
      <span class="badge ${TICKET_STATUS_CLASS[t.status] || 'badge-gray'}">${TICKET_STATUS_LABELS[t.status] || t.status}</span>
    </div>`).join('');
  return newBtn + '<div class="fv-list">' + list + '</div>';
}

function chatShowNewTicketForm() {
  const body = document.getElementById('chat-drawer-body');
  body.innerHTML = `
    <div class="chat-ticket-form">
      <div class="fv-room-header" style="padding-left:0"><i class="fas fa-life-ring"></i> Nowe zgłoszenie</div>
      <form onsubmit="return chatSubmitNewTicket(event)">
        <input type="text" id="chat-ticket-title" class="form-control" maxlength="200"
               placeholder="Krótki tytuł problemu…" required style="margin-bottom:10px">
        <textarea id="chat-ticket-desc" class="form-control" rows="5" maxlength="4000"
                  placeholder="Opisz dokładnie, co się dzieje, kiedy i na jakiej stronie…" required
                  style="margin-bottom:10px;resize:vertical"></textarea>
        <div style="display:flex;gap:8px;justify-content:flex-end">
          <button type="button" class="btn btn-ghost btn-sm" onclick="chatRenderList()">Anuluj</button>
          <button type="submit" class="btn btn-primary btn-sm"><i class="fas fa-paper-plane"></i> Wyślij zgłoszenie</button>
        </div>
      </form>
    </div>`;
}

function chatSubmitNewTicket(evt) {
  evt.preventDefault();
  const title = document.getElementById('chat-ticket-title').value.trim();
  const description = document.getElementById('chat-ticket-desc').value.trim();
  if (!title || !description) return false;
  fetch(CHAT_TICKETS_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, description }),
  })
    .then(r => r.json())
    .then(data => {
      if (data.ticket) {
        openTicketThread(data.ticket.id);
      }
    });
  return false;
}

function chatTicketStatusControlHtml(ticket) {
  if (CHAT_CURRENT_USER_IS_ADMIN) {
    return `<select id="chat-ticket-status-select" class="form-control" style="width:auto;font-size:.72rem;padding:4px 8px"
              onchange="chatChangeTicketStatus(${ticket.id}, this.value)">
      <option value="open" ${ticket.status === 'open' ? 'selected' : ''}>Otwarte</option>
      <option value="in_progress" ${ticket.status === 'in_progress' ? 'selected' : ''}>W trakcie</option>
      <option value="closed" ${ticket.status === 'closed' ? 'selected' : ''}>Zamknięte</option>
    </select>`;
  }
  if (ticket.status !== 'closed') {
    return `<button type="button" class="btn btn-ghost btn-sm" onclick="chatChangeTicketStatus(${ticket.id}, 'closed')">
      <i class="fas fa-check"></i> Zamknij zgłoszenie</button>`;
  }
  return `<span class="badge badge-gray">Zamknięte</span>`;
}

function openTicketThread(ticketId) {
  chatConversation = { type: 'ticket', id: ticketId, lastDayKey: null };
  chatConversationSkeleton('Zgłoszenie', '', '<div id="chat-ticket-header-extra"></div>');
  chatFetchTicketThread(true);
  chatStartPolling();
}

function chatFetchTicketThread(initial) {
  if (!chatConversation || chatConversation.type !== 'ticket') return;
  const id = chatConversation.id;
  fetch(CHAT_TICKET_DETAIL_URL_TPL.replace('/0', '/' + id))
    .then(r => r.json())
    .then(data => {
      if (!chatConversation || chatConversation.id !== id || chatConversation.type !== 'ticket') return;
      const titleEl = document.querySelector('#chat-drawer-body .chat-conv-title');
      const subEl = document.querySelector('#chat-drawer-body .chat-conv-subtitle');
      const extraEl = document.getElementById('chat-ticket-header-extra');
      if (titleEl) titleEl.textContent = data.ticket.title;
      if (subEl) subEl.innerHTML = `Zgłosił(a): ${chatEscapeHtml(data.ticket.reporter_username)}`;
      if (extraEl) {
        extraEl.innerHTML = `<div style="margin-top:6px">${chatTicketStatusControlHtml(data.ticket)}</div>`;
      }
      const list = document.getElementById('chat-conv-messages');
      if (!list) return;
      const wasAtBottom = list.scrollTop + list.clientHeight >= list.scrollHeight - 30;
      list.innerHTML = '';
      chatConversation.lastDayKey = null;
      chatAppendWithDividers(list, data.entries, chatTicketEntryBubbleHtml);
      if (initial || wasAtBottom) list.scrollTop = list.scrollHeight;
      const input = document.getElementById('chat-conv-text');
      if (input) input.placeholder = data.ticket.status === 'closed' ? 'Napisz, aby wznowić zgłoszenie…' : 'Napisz odpowiedź…';
    })
    .catch(() => {});
}

function chatTicketEntryBubbleHtml(entry) {
  const mine = entry.mine;
  const staffTag = entry.is_staff ? ' <span class="badge badge-primary" style="font-size:.6rem;padding:1px 5px">obsługa</span>' : '';
  const author = !mine ? `<div class="chat-bubble-author">${chatEscapeHtml(entry.author.username)}${staffTag}</div>` : '';
  return `<div class="chat-bubble-row ${mine ? 'mine' : ''}">
    <div class="chat-bubble">
      ${author}
      <div class="chat-bubble-text">${chatEscapeHtml(entry.content)}</div>
      <div class="chat-bubble-time">${chatEscapeHtml(entry.created_at)}</div>
    </div>
  </div>`;
}

function chatChangeTicketStatus(ticketId, status) {
  fetch(CHAT_TICKET_STATUS_URL_TPL.replace('/0', '/' + ticketId), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  })
    .then(r => r.json())
    .then(() => {
      if (chatConversation && chatConversation.type === 'ticket' && chatConversation.id === ticketId) {
        chatFetchTicketThread(false);
      }
      chatUpdateUnreadBadge();
    });
}

// ---------------------------------------------------------------------
// Wysyłka wiadomości — wspólny formularz dla przedmiotu / DM / ticketu
// ---------------------------------------------------------------------
function chatSendCurrent(evt) {
  if (evt) evt.preventDefault();
  const input = document.getElementById('chat-conv-text');
  const content = (input.value || '').trim();
  if (!content || !chatConversation) return false;
  const c = chatConversation;
  let url;
  if (c.type === 'subject') url = CHAT_SUBJECT_MESSAGES_URL_TPL.replace('/0', '/' + c.id);
  else if (c.type === 'dm') url = CHAT_DM_MESSAGES_URL_TPL.replace('/0', '/' + c.id);
  else url = CHAT_TICKET_REPLIES_URL_TPL.replace('/0', '/' + c.id);

  input.value = '';
  input.disabled = true;
  fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
    .then(r => r.json())
    .then(data => {
      input.disabled = false;
      input.focus();
      const list = document.getElementById('chat-conv-messages');
      if (!list || !chatConversation || chatConversation.id !== c.id || chatConversation.type !== c.type) return;
      if (c.type === 'ticket') {
        if (data.entry) {
          chatAppendWithDividers(list, [data.entry], chatTicketEntryBubbleHtml);
          list.scrollTop = list.scrollHeight;
          if (data.ticket) {
            const extraEl = document.getElementById('chat-ticket-header-extra');
            if (extraEl) extraEl.innerHTML = `<div style="margin-top:6px">${chatTicketStatusControlHtml(data.ticket)}</div>`;
          }
        }
      } else if (data.message) {
        chatAppendWithDividers(list, [data.message], m => chatMessageBubbleHtml(m, c.type));
        chatConversation.lastId = Math.max(chatConversation.lastId, data.message.id);
        list.scrollTop = list.scrollHeight;
      }
    })
    .catch(() => {
      input.disabled = false;
      input.value = content;
    });
  return false;
}

function chatStartPolling() {
  chatStopPolling();
  chatPollTimer = setInterval(() => {
    if (!chatConversation) return;
    if (chatConversation.type === 'ticket') chatFetchTicketThread(false);
    else chatFetchMessages(false);
  }, 3500);
}

function chatStopPolling() {
  if (chatPollTimer) {
    clearInterval(chatPollTimer);
    chatPollTimer = null;
  }
}

// ---------------------------------------------------------------------
// Odznaki: nieprzeczytane wiadomości prywatne (sidebar, topbar, zakładka)
// oraz otwarte zgłoszenia widoczne tylko adminom.
// ---------------------------------------------------------------------
function chatUpdateBadges(dmCount, ticketCount) {
  document.querySelectorAll('.chat-unread-count').forEach(el => {
    if (el.id === 'chat-nav-badge' || el.id === 'chat-topbar-badge') {
      el.textContent = dmCount > 0 ? dmCount : '';
      el.style.display = dmCount > 0 ? 'flex' : 'none';
    }
  });
  const dmTabBadge = document.getElementById('chat-dm-tab-badge');
  if (dmTabBadge) {
    dmTabBadge.textContent = dmCount > 0 ? dmCount : '';
    dmTabBadge.style.display = dmCount > 0 ? 'inline-flex' : 'none';
  }
  const ticketsTabBadge = document.getElementById('chat-tickets-tab-badge');
  if (ticketsTabBadge) {
    const n = ticketCount || 0;
    ticketsTabBadge.textContent = n > 0 ? n : '';
    ticketsTabBadge.style.display = n > 0 ? 'inline-flex' : 'none';
  }
}

function chatUpdateUnreadBadge() {
  fetch(CHAT_PANEL_DATA_URL.replace('/panel-data', '/unread-count'))
    .then(r => r.json())
    .then(data => chatUpdateBadges(data.unread_total, data.open_tickets_admin))
    .catch(() => {});
}

// Pierwsze wczytanie odznaki (bez otwierania panelu), żeby liczba
// nieprzeczytanych na sidebarze/topbarze była aktualna od razu po wejściu.
if (typeof CHAT_CURRENT_USER_ID !== 'undefined' && CHAT_CURRENT_USER_ID) {
  chatUpdateUnreadBadge();
}
