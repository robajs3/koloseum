"""
Konwersja czasu: UTC (przechowywane w bazie) <-> czas lokalny (wyświetlany
userowi / wpisywany przez usera w formularzach).

DLACZEGO TO ISTNIEJE (naprawa buga z datami):
Wszystkie kolumny DateTime w bazie są naiwne (bez strefy) i zapisywane
przez `datetime.utcnow()` — czyli trzymają czas UTC. Problem polegał na
tym, że w szablonach renderowaliśmy je surowym `.strftime(...)` bez
konwersji z powrotem na czas lokalny (Europe/Warsaw), więc np. wiadomość
wysłana o 12:00 czasu polskiego (latem UTC+2) w bazie lądowała jako 10:00
UTC i dokładnie "10:00" wyskakiwało na czacie — różnica dokładnie o
przesunięcie strefy. To samo dotyczyło powiadomień (wyglądały, jakby
przyszły 2h "później" niż faktycznie, bo ich created_at też był surowym
UTC) oraz dat wpisywanych ręcznie w formularzach (`datetime-local`),
które trzeba najpierw zamienić na UTC PRZED zapisem do bazy, żeby
porównania z `datetime.utcnow()` (np. "czy termin jest w przyszłości")
się zgadzały.

Zasada w całej appce:
- W BAZIE zawsze trzymamy czas w UTC (naiwny datetime, tak jak dotychczas).
- Przy WYŚWIETLANIU jakiejkolwiek daty z bazy używamy filtra Jinja
  `|localtime`, który konwertuje ją na czas lokalny tuż przed `.strftime`.
- Przy ZAPISIE daty wpisanej przez usera w <input type="datetime-local">
  (ta wartość to zawsze czas lokalny przeglądarki) konwertujemy ją
  funkcją `to_utc()` zanim trafi do bazy.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

APP_TIMEZONE_NAME = "Europe/Warsaw"
APP_TZ = ZoneInfo(APP_TIMEZONE_NAME)
_UTC = ZoneInfo("UTC")


def utc_now() -> datetime:
    """Aktualny czas UTC (naiwny) — do zapisu/porównań w bazie."""
    return datetime.utcnow()


def to_local(dt: datetime | None) -> datetime | None:
    """Zamienia naiwny datetime UTC (z bazy) na naiwny datetime w czasie
    lokalnym (Europe/Warsaw), gotowy do wyświetlenia userowi."""
    if dt is None:
        return None
    aware_utc = dt.replace(tzinfo=_UTC)
    return aware_utc.astimezone(APP_TZ).replace(tzinfo=None)


def to_utc(dt: datetime | None) -> datetime | None:
    """Zamienia naiwny datetime lokalny (np. wpisany przez usera w
    formularzu datetime-local) na naiwny datetime UTC, do zapisu w bazie."""
    if dt is None:
        return None
    aware_local = dt.replace(tzinfo=APP_TZ)
    return aware_local.astimezone(_UTC).replace(tzinfo=None)
