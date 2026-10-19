"""
filevault_fetch.py — pobieranie plików i list plików z linków udostępniania FileVault
na potrzeby podsumowań AI.

Rozpoznawane linki (te same warianty co w deriveFilevaultPreviewUrl w templates/):
  plik:    /filevault/s/<token>
           /filevault/sf|sr|srf/<token>/file/<id>
  folder:  /filevault/sf|sr|srf|shared-folder/<token>[/...]

BEZPIECZEŃSTWO (SSRF): link wpisuje użytkownik, więc serwer NIE może pobierać go
jako dowolnego URL-a. Z linku bierzemy wyłącznie ŚCIEŻKĘ, zweryfikowaną ścisłym
regexem, i doklejamy ją do FILEVAULT_INTERNAL_URL z konfiguracji. Host z linku jest
ignorowany; przekierowania nie są śledzone. Ciasteczko SSO użytkownika (jeśli jest)
trafia wyłącznie do tego wewnętrznego adresu.

ZAŁOŻENIA co do FileVault (kod FileVault nie jest częścią tego repo):
  1. GET na link pojedynczego pliku zwraca bajty pliku (z Content-Disposition:
     attachment) — to wynika z komentarza przy deriveFilevaultPreviewUrl w szablonie.
  2. Strona udostępnionego folderu jest renderowana po stronie serwera i zawiera
     <a href> do plików w postaci /filevault/<sf|sr|srf>/<token>/file/<id>.
     Jeśli FileVault wystawi do tego JSON-owe API, wystarczy podmienić
     list_folder_files() — reszta kodu nie zależy od sposobu listowania.
"""
import contextlib
import html
import logging
import os
import posixpath
import re
import tempfile
from urllib.parse import unquote, urljoin, urlsplit

import requests
from flask import current_app

from services.errors import AIError, NotAccepted

logger = logging.getLogger(__name__)

_TOKEN = r"[A-Za-z0-9_\-~]+"
_FILE_RE = re.compile(
    rf"(/filevault/(?:s/{_TOKEN}|(?:sf|sr|srf)(?:/{_TOKEN})+/file/\d+))(?:/(?:preview|download))?/?$"
)
_FOLDER_RE = re.compile(
    rf"(/filevault/(?:sf|sr|srf|shared-folder)(?:/{_TOKEN}){{1,4}})/?$"
)
MAX_LISTING_BYTES = 2 * 1024 * 1024
MAX_LISTED_FILES = 200


def _sso_cookie_name() -> str:
    return os.environ.get("SSO_COOKIE_NAME", "sso_session")


def parse_link(link: str | None) -> tuple[str, str] | None:
    """Zwraca ('file'|'folder', znormalizowana_ścieżka) albo None, jeśli to nie link
    FileVault w znanym formacie."""
    if not link or len(link) > 512 or re.search(r"\s", link.strip()):
        return None
    parts = urlsplit(link.strip())
    if parts.scheme not in ("", "http", "https"):
        return None
    path = parts.path
    m = _FILE_RE.search(path)
    if m:
        return "file", m.group(1)
    if "/file/" not in path:
        m = _FOLDER_RE.search(path)
        if m:
            return "folder", m.group(1)
    return None


def link_kind(link: str | None) -> str | None:
    parsed = parse_link(link)
    return parsed[0] if parsed else None


def _open(path: str, sso_cookie: str | None):
    """GET na wewnętrzny FileVault (stream). Zwraca response — wywołujący go zamyka."""
    cfg = current_app.config
    base = cfg.get("FILEVAULT_INTERNAL_URL", "http://127.0.0.1:5000").rstrip("/")
    cookies = {_sso_cookie_name(): sso_cookie} if sso_cookie else None
    try:
        resp = requests.get(
            base + path, cookies=cookies, stream=True, allow_redirects=False,
            timeout=(5, cfg.get("AI_FETCH_TIMEOUT", 60)),
            headers={"User-Agent": "Koloseum-AI/1.0"},
        )
    except requests.ConnectionError:
        logger.exception("FileVault unreachable at %s", base)
        raise AIError("Nie można połączyć się z FileVault.")
    except requests.Timeout:
        raise AIError("FileVault nie odpowiedział na czas.")
    except requests.RequestException:
        logger.exception("FileVault request failed")
        raise AIError("Nie udało się pobrać pliku z FileVault.")

    code = resp.status_code
    if code in (404, 410):
        resp.close()
        raise AIError("Nie znaleziono pliku pod tym linkiem (usunięty albo link wyłączony).")
    if code in (401, 403) or 300 <= code < 400:
        resp.close()
        raise AIError("Brak dostępu do pliku (link wymaga logowania lub hasła albo wygasł).")
    if code != 200:
        resp.close()
        logger.error("FileVault HTTP %s for %s", code, path)
        raise AIError(f"FileVault zwrócił błąd (HTTP {code}).")
    return resp


def _filename(content_disposition: str | None) -> str:
    """Nazwa pliku z Content-Disposition (obsługa filename*=UTF-8''… i filename="…")."""
    if not content_disposition:
        return ""
    m = re.search(r"filename\*\s*=\s*[^']*'[^']*'([^;]+)", content_disposition, re.I)
    if m:
        name = unquote(m.group(1).strip().strip('"'))
    else:
        m = re.search(r'filename\s*=\s*"?([^";]+)"?', content_disposition, re.I)
        name = m.group(1).strip() if m else ""
    return os.path.basename(name.replace("\\", "/"))[:200]


@contextlib.contextmanager
def fetched_file(path: str, sso_cookie: str | None = None, accept=None):
    """Pobiera plik do pliku tymczasowego i go po sobie sprząta.

        with fetched_file(path, cookie) as (tmp_path, filename): ...

    Limit rozmiaru (AI_FETCH_MAX_MB) egzekwowany na faktycznie pobranych bajtach.
    `accept(nazwa_pliku) -> bool` pozwala odrzucić plik (NotAccepted) po samych
    nagłówkach, bez pobierania treści — np. podgląd interesują tylko dokumenty Office."""
    max_mb = current_app.config.get("AI_FETCH_MAX_MB", 25)
    max_bytes = max_mb * 1024 * 1024
    resp = _open(path, sso_cookie)
    tmp_path = None
    try:
        disp = resp.headers.get("Content-Disposition", "")
        name = _filename(disp)
        ctype = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if "attachment" not in disp.lower() and not name \
                and ctype in ("text/html", "application/xhtml+xml"):
            # strona zamiast pliku: formularz hasła, ekran logowania, komunikat o błędzie
            raise AIError("Link nie zwrócił pliku (wymaga hasła, wygasł albo prowadzi do strony).")

        if accept is not None and not accept(name):
            raise NotAccepted("Format nieobsługiwany przez ten podgląd.")

        too_big = AIError(f"Plik jest za duży do podsumowania (limit {max_mb} MB).")
        length = resp.headers.get("Content-Length", "")
        if length.isdigit() and int(length) > max_bytes:
            raise too_big

        fd, tmp_path = tempfile.mkstemp(prefix="kolfv_")
        size = 0
        try:
            with os.fdopen(fd, "wb") as out:
                for chunk in resp.iter_content(64 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise too_big
                    out.write(chunk)
        except requests.RequestException:
            raise AIError("Pobieranie pliku z FileVault zostało przerwane.")
        yield tmp_path, name
    finally:
        resp.close()
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


def list_folder_files(path: str, sso_cookie: str | None = None) -> list[str]:
    """Ścieżki plików z podanego folderu FileVault (tylko pierwszy poziom, w kolejności
    ze strony). Patrz 'ZAŁOŻENIA' w docstringu modułu — listę czytamy ze strony HTML."""
    resp = _open(path, sso_cookie)
    try:
        body, size = [], 0
        try:
            for chunk in resp.iter_content(64 * 1024):
                size += len(chunk)
                body.append(chunk)
                if size > MAX_LISTING_BYTES:
                    break
        except requests.RequestException:
            raise AIError("Pobieranie listy plików z FileVault zostało przerwane.")
        page = b"".join(body).decode(resp.encoding or "utf-8", errors="replace")
    finally:
        resp.close()

    found, seen = [], set()
    for href in re.findall(r"""href\s*=\s*["']([^"']+)["']""", page, re.I):
        # urljoin rozwiązuje linki względne; zostawiamy tylko ścieżkę (host odrzucony)
        target = urlsplit(urljoin(path + "/", html.unescape(href))).path
        m = _FILE_RE.search(posixpath.normpath(target))
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            found.append(m.group(1))
            if len(found) >= MAX_LISTED_FILES:
                break
    return found
