// ===== KOLOSEUM — Main JS =====

// Theme management
const ThemeManager = {
  init() {
    const saved = localStorage.getItem('theme') ||
      document.documentElement.getAttribute('data-theme') || 'light';
    this.apply(saved);
  },
  apply(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
  },
  toggle() {
    const current = document.documentElement.getAttribute('data-theme') || 'light';
    const next = current === 'light' ? 'dark' : 'light';
    this.apply(next);
    // Persist via server
    fetch(window.PREFIX + '/profile/theme', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: `theme=${next}`,
    });
  },
};

// Notification badge updater
const NotificationManager = {
  async updateBadge() {
    try {
      const r = await fetch(window.PREFIX + '/notifications/unread-count');
      const data = await r.json();
      const badges = document.querySelectorAll('.notif-count');
      badges.forEach(b => {
        b.textContent = data.count || '';
        b.style.display = data.count > 0 ? 'flex' : 'none';
      });
    } catch {}
  },
  async markAllRead() {
    await fetch(window.PREFIX + '/notifications/read-all', { method: 'POST' });
    this.updateBadge();
  },
};

// Calendar
const Calendar = {
  year: new Date().getFullYear(),
  month: new Date().getMonth() + 1,
  events: {},

  async loadEvents() {
    try {
      const r = await fetch(`${window.PREFIX}/calendar/events?year=${this.year}&month=${this.month}`);
      const data = await r.json();
      this.events = {};
      data.forEach(ev => {
        if (!this.events[ev.date]) this.events[ev.date] = [];
        this.events[ev.date].push(ev);
      });
    } catch {}
  },

  async render() {
    await this.loadEvents();
    const grid = document.getElementById('cal-grid');
    const title = document.getElementById('cal-title');
    if (!grid) return;

    const months = ['Styczeń','Luty','Marzec','Kwiecień','Maj','Czerwiec',
                    'Lipiec','Sierpień','Wrzesień','Październik','Listopad','Grudzień'];
    title.textContent = `${months[this.month-1]} ${this.year}`;

    const firstDay = new Date(this.year, this.month - 1, 1).getDay();
    const offset = (firstDay + 6) % 7; // Mon start
    const daysInMonth = new Date(this.year, this.month, 0).getDate();
    const today = new Date();

    let html = '';
    for (let i = 0; i < offset; i++) {
      const prevDay = new Date(this.year, this.month - 1, -offset + i + 1).getDate();
      html += `<div class="cal-day other-month"><div class="cal-day-num">${prevDay}</div></div>`;
    }

    for (let d = 1; d <= daysInMonth; d++) {
      const dateStr = `${this.year}-${String(this.month).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
      const isToday = d === today.getDate() && this.month === today.getMonth()+1 && this.year === today.getFullYear();
      const dayEvents = this.events[dateStr] || [];

      let evHtml = '';
      dayEvents.slice(0, 3).forEach(ev => {
        const cls = `exam-${ev.type}`;
        evHtml += `<div class="cal-event-dot ${cls}" title="${ev.subject}: ${ev.title} ${ev.time}">${ev.title}</div>`;
      });
      if (dayEvents.length > 3) {
        evHtml += `<div class="cal-more">+${dayEvents.length - 3} więcej</div>`;
      }

      html += `
        <div class="cal-day ${isToday ? 'today' : ''}" onclick="Calendar.showDayEvents('${dateStr}')">
          <div class="cal-day-num">${d}</div>
          ${evHtml}
        </div>`;
    }

    grid.innerHTML = html;
  },

  showDayEvents(dateStr) {
    const evs = this.events[dateStr] || [];
    if (!evs.length) return;
    const [y, m, d] = dateStr.split('-');
    let html = `<strong>📅 ${d}.${m}.${y}</strong><br><br>`;
    evs.forEach(ev => {
      html += `<div style="margin-bottom:8px; padding:8px; background:var(--bg); border-radius:6px; border-left:3px solid ${ev.subject_color}">
        <div style="font-weight:600;font-size:.85rem">${ev.title}</div>
        <div style="font-size:.75rem;color:var(--text-muted)">${ev.subject} • ${ev.time}${ev.location ? ' • ' + ev.location : ''}</div>
      </div>`;
    });
    showModal('Terminy', html);
  },

  prev() {
    this.month--;
    if (this.month < 1) { this.month = 12; this.year--; }
    this.render();
  },
  next() {
    this.month++;
    if (this.month > 12) { this.month = 1; this.year++; }
    this.render();
  },
};

// Simple modal
function showModal(title, content) {
  let modal = document.getElementById('app-modal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'app-modal';
    modal.style.cssText = `
      position:fixed;inset:0;z-index:9000;display:flex;align-items:center;justify-content:center;
      background:rgba(0,0,0,.4);padding:20px;
    `;
    modal.innerHTML = `
      <div style="background:var(--bg-card);border-radius:12px;box-shadow:var(--shadow-md);
                  max-width:480px;width:100%;padding:24px;max-height:80vh;overflow-y:auto;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
          <span id="modal-title" style="font-weight:700;font-size:1rem"></span>
          <button onclick="closeModal()" style="background:none;border:none;cursor:pointer;
            font-size:1.2rem;color:var(--text-muted)">✕</button>
        </div>
        <div id="modal-body" style="font-size:.875rem;line-height:1.6"></div>
      </div>`;
    document.body.appendChild(modal);
    modal.addEventListener('click', e => { if (e.target === modal) closeModal(); });
  }
  document.getElementById('modal-title').textContent = title;
  document.getElementById('modal-body').innerHTML = content;
  modal.style.display = 'flex';
}

function closeModal() {
  const m = document.getElementById('app-modal');
  if (m) m.style.display = 'none';
}

// Mobile sidebar toggle
function toggleSidebar() {
  document.querySelector('.sidebar')?.classList.toggle('open');
}

// Auto-scroll chat to bottom
function scrollChatToBottom() {
  const msgs = document.querySelector('.chat-messages');
  if (msgs) msgs.scrollTop = msgs.scrollHeight;
}

// Flash auto-dismiss
function initFlashDismiss() {
  document.querySelectorAll('.alert[data-autodismiss]').forEach(el => {
    setTimeout(() => {
      el.style.transition = 'opacity .4s';
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 400);
    }, 4000);
  });
}

// Push notification setup
async function setupPushNotifications() {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
  try {
    const r = await fetch(window.PREFIX + '/profile/vapid-public-key');
    const { publicKey } = await r.json();
    if (!publicKey) return;

    const reg = await navigator.serviceWorker.register(window.PREFIX + '/static/js/sw.js');
    const perm = await Notification.requestPermission();
    if (perm !== 'granted') return;

    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(publicKey),
    });

    await fetch(window.PREFIX + '/profile/push-subscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(sub),
    });
  } catch (e) {
    console.warn('Push setup failed:', e);
  }
}

function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - base64String.length % 4) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
}

// Toast notifications
function showToast(message, type = 'info') {
  let container = document.querySelector('.toast-container');
  if (!container) {
    container = document.createElement('div');
    container.className = 'toast-container';
    document.body.appendChild(container);
  }
  const icons = { success: '✅', danger: '❌', warning: '⚠️', info: 'ℹ️' };
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.innerHTML = `<span>${icons[type] || 'ℹ️'}</span><span>${message}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.transition = 'opacity .4s';
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 400);
  }, 4000);
}

// Init
document.addEventListener('DOMContentLoaded', () => {
  ThemeManager.init();
  initFlashDismiss();
  scrollChatToBottom();
  NotificationManager.updateBadge();
  setInterval(() => NotificationManager.updateBadge(), 30000);

  if (document.getElementById('cal-grid')) {
    Calendar.render();
  }

  // Sidebar overlay close on mobile
  document.addEventListener('click', e => {
    const sidebar = document.querySelector('.sidebar');
    if (sidebar?.classList.contains('open') && !sidebar.contains(e.target) &&
        !e.target.closest('.hamburger')) {
      sidebar.classList.remove('open');
    }
  });
});