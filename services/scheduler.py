"""
Lekki wątek w tle, który co REMINDER_CHECK_INTERVAL sekund sprawdza, czy
komuś trzeba wysłać przypomnienie o zbliżającym się terminie ("godzinę
przed" / "X dni przed" — patrz NotificationService.check_and_send_exam_reminders).

UWAGA o wielu workerach gunicorna (patrz Dockerfile: --workers 3): każdy
worker uruchamia własną kopię tego wątku, więc `check_and_send_exam_reminders`
będzie wołane kilka razy naraz z różnych procesów. To jest bezpieczne —
NotificationService zapisuje wysłane przypomnienia w ExamReminderLog z
unikalnym constraintem (exam_id, user_id, kind), więc tylko pierwszy
insert się powiedzie, a reszta po prostu nic nie wyśle. Nie ma tu
osobnego mechanizmu blokującego (np. Redisa) — nie jest potrzebny przy
tak prostym zadaniu, a dodawałby zależność operacyjną bez realnej
korzyści.
"""
import threading
import time


def start_reminder_scheduler(app) -> None:
    interval = app.config.get("REMINDER_CHECK_INTERVAL", 300)

    def _loop():
        # Krótkie opóźnienie na starcie, żeby appka zdążyła się w pełni
        # podnieść (tabele utworzone, itd.) zanim pierwszy tick strzeli.
        time.sleep(10)
        while True:
            try:
                with app.app_context():
                    from services.notification_service import NotificationService
                    NotificationService.check_and_send_exam_reminders()
            except Exception as e:
                app.logger.warning(f"Reminder scheduler tick failed: {e}")
            time.sleep(interval)

    thread = threading.Thread(target=_loop, name="exam-reminder-scheduler", daemon=True)
    thread.start()
