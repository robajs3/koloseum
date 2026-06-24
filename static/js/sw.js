// Koloseum Service Worker
self.addEventListener('push', function(event) {
  let data = { title: 'Koloseum', body: 'Masz nowe powiadomienie.', link: '/koloseum/' };
  try { data = JSON.parse(event.data.text()); } catch {}
  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: '/koloseum/static/img/icon-192.png',
      badge: '/koloseum/static/img/icon-72.png',
      data: { link: data.link },
    })
  );
});

self.addEventListener('notificationclick', function(event) {
  event.notification.close();
  const link = event.notification.data?.link || '/koloseum/';
  event.waitUntil(clients.openWindow(link));
});