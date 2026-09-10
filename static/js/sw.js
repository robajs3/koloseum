// Koloseum Service Worker
//
// UWAGA (Tailscale Serve --set-path /koloseum): ten plik jest zwykłym
// statycznym JS-em, NIE przechodzi przez Jinja, więc nie ma dostępu do
// window.PREFIX. Wcześniej ścieżki do ikon i link fallback były zahardkodowane
// jako "/static/..." i "/koloseum/" — to działało lokalnie (serwer stoi na
// roocie /), ale psuło się, gdy appka wisiała pod Tailscale z prefiksem, bo
// Tailscale przepuszcza do backendu tylko to, co jest pod /koloseum, a
// bezwzględna ścieżka "/static/..." (bez prefiksu) nigdy nie trafiała do
// backendu (404) — więc `showNotification` się wywalał / ikony nie ładowały.
//
// Naprawa: liczymy bazowy URL z self.registration.scope (to jest URL, spod
// którego przeglądarka faktycznie zarejestrowała ten SW, więc ZAWSZE
// zawiera właściwy prefiks) i budujemy z niego wszystkie ścieżki.
function scopeUrl(path) {
  const base = self.registration.scope; // np. https://host/koloseum/
  return new URL(path, base).href;
}

self.addEventListener('push', function (event) {
  let data = { title: 'Koloseum', body: 'Masz nowe powiadomienie.', link: self.registration.scope };
  try {
    const parsed = event.data.json();
    data = { ...data, ...parsed };
  } catch {
    try { data.body = event.data.text(); } catch {}
  }
  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: scopeUrl('static/img/icon-192.png'),
      badge: scopeUrl('static/img/icon-72.png'),
      data: { link: data.link || self.registration.scope },
    })
  );
});

self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  const link = event.notification.data?.link || self.registration.scope;
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url === link && 'focus' in client) return client.focus();
      }
      if (clients.openWindow) return clients.openWindow(link);
    })
  );
});

// Minimalny fetch handler — bez tego niektóre przeglądarki (Chrome przed
// dodaniem PWA na Androidzie) nie uznają appki za instalowalny PWA.
self.addEventListener('fetch', function (event) {
  // Nie robimy własnego cache'owania — po prostu przepuszczamy request,
  // to daje "installability" bez ryzyka serwowania nieaktualnych stron.
});

self.addEventListener('install', function (event) {
  self.skipWaiting();
});

self.addEventListener('activate', function (event) {
  event.waitUntil(self.clients.claim());
});
