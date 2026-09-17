// ===== KOLOSEUM — Panel czatu "z boku" (pokoje/przedmioty + wiadomości prywatne) =====
// Wzorowane na panelu FileVault (patrz templates/base.html: #fv-drawer) —
// ta sama konwencja wysuwanego z lewej panelu, dostępnego z każdej strony.
// Odświeżanie wiadomości działa przez zwykłe pollowanie (fetch co kilka
// sekund), bez WebSocketów — spójnie z resztą appki (patrz NotificationManager
// w app.js), i bezpiecznie względem obecnej konfiguracji Gunicorn/Tailscale.

let chatOpen = false;
let chatActiveTab = 'rooms';
let chatPanelData = null;      // wynik /chat/panel-data: {rooms, contacts, unread_total}
let chatConversation = null;   // {type: 'subject'|'dm', id, name, lastId}
let chatPollTimer = null;
let chatBadgePollTimer = null;

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

// ---------------------------------------------------------------------
// Ładowanie danych panelu (drzewo pokój->przedmioty + kontakty do DM)
// ---------------------------------------------------------------------
function chatLoadPanelData() {
  const body = document.getElementById('chat-drawer-body');
  document.getElementById('chat-back-btn').style.display = 'none';
  document.getElementById('chat-drawer-tabs').style.display = 'flex';
  body.innerHTML = '<div class="fv-loading"><i class="fas fa-spinner fa-spin"></i> Ładowanie…</div>';

  fetch(CHAT_PANEL_DATA_URL)
    .then(r => r.json())
    .then(data => {
      chatPanelData = data;
      chatUpdateBadges(data.unread_total);
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
  chatRenderList();
}

function chatRenderList() {
  if (!chatPanelData) return;
  const body = document.getElementById('chat-drawer-body');
  if (chatActiveTab === 'rooms') {
    body.innerHTML = chatRoomsListHtml(chatPanelData.rooms);
  } else {
    body.innerHTML = chatContactsListHtml(chatPanelData.contacts);
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
        <div class="fv-item" onclick="openSubjectConversation(${s.id}, '${chatEscapeHtml(s.name).replace(/'/g, "\\'")}')">
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
    <div class="fv-item" onclick="openDmConversation(${c.id}, '${chatEscapeHtml(c.username).replace(/'/g, "\\'")}')">
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
// Widok rozmowy (przedmiot albo DM) — wspólny szkielet, różni się tylko
// endpointem i podpisem autora wiadomości.
// ---------------------------------------------------------------------
function chatShowList() {
  chatStopPolling();
  chatConversation = null;
  document.getElementById('chat-back-btn').style.display = 'none';
  document.getElementById('chat-drawer-tabs').style.display = 'flex';
  chatRenderList();
  // Odśwież listę kontaktów/nieprzeczytanych po ewentualnej rozmowie DM.
  chatLoadPanelData();
}

function chatConversationSkeleton(title, subtitle) {
  document.getElementById('chat-back-btn').style.display = 'flex';
  document.getElementById('chat-drawer-tabs').style.display = 'none';
  const body = document.getElementById('chat-drawer-body');
  body.style.padding = '0';
  body.innerHTML = `
    <div class="chat-conv">
      <div class="chat-conv-header">
        <div class="chat-conv-title">${chatEscapeHtml(title)}</div>
        ${subtitle ? `<div class="chat-conv-subtitle">${chatEscapeHtml(subtitle)}</div>` : ''}
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

function openSubjectConversation(subjectId, name) {
  chatConversation = { type: 'subject', id: subjectId, name: name, lastId: 0 };
  chatConversationSkeleton(name, 'Czat grupowy przedmiotu');
  chatFetchMessages(true);
  chatStartPolling();
}

function openDmConversation(userId, username) {
  chatConversation = { type: 'dm', id: userId, name: username, lastId: 0 };
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
      if (initial) list.innerHTML = '';
      if (!data.messages || !data.messages.length) {
        if (initial) list.innerHTML = `<div class="fv-empty" style="padding:30px 10px">
          <i class="fas fa-comment-slash"></i><p>Brak wiadomości. Napisz pierwszą!</p></div>`;
        return;
      }
      const wasAtBottom = list.scrollTop + list.clientHeight >= list.scrollHeight - 30;
      data.messages.forEach(m => {
        list.insertAdjacentHTML('beforeend', chatMessageBubbleHtml(m, c.type));
        chatConversation.lastId = Math.max(chatConversation.lastId, m.id);
      });
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

function chatSendCurrent(evt) {
  if (evt) evt.preventDefault();
  const input = document.getElementById('chat-conv-text');
  const content = (input.value || '').trim();
  if (!content || !chatConversation) return false;
  const c = chatConversation;
  const url = c.type === 'subject'
    ? CHAT_SUBJECT_MESSAGES_URL_TPL.replace('/0', '/' + c.id)
    : CHAT_DM_MESSAGES_URL_TPL.replace('/0', '/' + c.id);
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
      if (data.message) {
        const list = document.getElementById('chat-conv-messages');
        if (list) {
          list.insertAdjacentHTML('beforeend', chatMessageBubbleHtml(data.message, c.type));
          chatConversation.lastId = Math.max(chatConversation.lastId, data.message.id);
          list.scrollTop = list.scrollHeight;
        }
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
  chatPollTimer = setInterval(() => chatFetchMessages(false), 3500);
}

function chatStopPolling() {
  if (chatPollTimer) {
    clearInterval(chatPollTimer);
    chatPollTimer = null;
  }
}

// ---------------------------------------------------------------------
// Odznaki z liczbą nieprzeczytanych wiadomości prywatnych (sidebar, topbar)
// ---------------------------------------------------------------------
function chatUpdateBadges(count) {
  const els = document.querySelectorAll('.chat-unread-count');
  els.forEach(el => {
    if (el.id === 'chat-nav-badge' || el.id === 'chat-topbar-badge') {
      el.textContent = count > 0 ? count : '';
      el.style.display = count > 0 ? 'flex' : 'none';
    }
  });
  const tabBadge = document.getElementById('chat-dm-tab-badge');
  if (tabBadge) {
    tabBadge.textContent = count > 0 ? count : '';
    tabBadge.style.display = count > 0 ? 'inline-flex' : 'none';
  }
}

function chatUpdateUnreadBadge() {
  fetch(CHAT_PANEL_DATA_URL.replace('/panel-data', '/unread-count'))
    .then(r => r.json())
    .then(data => chatUpdateBadges(data.unread_total))
    .catch(() => {});
}

// Pierwsze wczytanie odznaki (bez otwierania panelu), żeby liczba
// nieprzeczytanych na sidebarze/topbarze była aktualna od razu po wejściu.
if (typeof CHAT_CURRENT_USER_ID !== 'undefined' && CHAT_CURRENT_USER_ID) {
  chatUpdateUnreadBadge();
}
