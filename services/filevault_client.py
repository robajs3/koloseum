"""
filevault_client.py — cienki klient do wewnętrznego API FileVault.

Koloseum i FileVault stoją na tym samym serwerze i są połączone tym samym
LoginHub (SSO). Sesja przeglądarki jednej appki nie jest bezpośrednio
widoczna dla drugiej po stronie backendu — ALE obie appki czytają to samo
ciasteczko LoginHub (SSO_COOKIE_NAME, domyślnie "sso_session") i mają
identyczny mechanizm autologinu (`sso_client._sso_autologin` w
`before_request`, patrz filevault/app.py).

Dlatego zamiast prosić usera o ręczne wklejanie tokenu API, Koloseum po
prostu PRZEKAZUJE DALEJ to samo ciasteczko SSO przy wywołaniu FileVault.
FileVault, dostając żądanie z tym ciasteczkiem, sam odpala swój zwykły
autologin przez LoginHub (i jeśli konta są połączone — a w tej appce
zawsze są, bo `_sso_autologin` automatycznie zakłada konto przy pierwszej
wizycie) — więc żadnej dodatkowej konfiguracji po stronie usera nie trzeba.

Token API (`user.filevault_api_token`, ustawiany w /profile) zostaje jako
fallback na wypadek, gdyby ktoś logował się w Koloseum lokalnym hasłem
(bez SSO) i nie miał w ogóle ciasteczka LoginHub w przeglądarce.

Każda funkcja zwraca None przy błędzie sieciowym/HTTP/braku autoryzacji,
żeby wywołujący kod mógł to potraktować jako "nie da się teraz" i nie
wywalić się.
"""
import os
import requests
from flask import current_app

TIMEOUT = 4


def _api_base() -> str:
    return current_app.config.get("FILEVAULT_INTERNAL_URL", "http://127.0.0.1:5000").rstrip("/") + "/filevault/api"


def _sso_cookie_name() -> str:
    # MUSI być ta sama wartość co SSO_COOKIE_NAME w .env LoginHub/Koloseum/FileVault.
    return os.environ.get("SSO_COOKIE_NAME", "sso_session")


def _auth_kwargs(sso_cookie_value: str | None, token: str | None) -> dict:
    """Preferuje przekazanie ciasteczka SSO (zero configu, działa od razu dla
    każdego, kto loguje się przez LoginHub). Token API jako fallback."""
    kwargs = {}
    if sso_cookie_value:
        kwargs["cookies"] = {_sso_cookie_name(): sso_cookie_value}
    if token:
        kwargs["headers"] = {"X-API-Token": token}
    return kwargs


def verify_token(token: str) -> dict | None:
    """Sprawdza, czy ręcznie wklejony token jest ważny. Zwraca
    {"username": ..., "email": ...} albo None. (Używane tylko przy
    ręcznym łączeniu konta w profilu — patrz docstring modułu.)"""
    if not token:
        return None
    try:
        resp = requests.get(f"{_api_base()}/verify", headers={"X-API-Token": token}, timeout=TIMEOUT)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    return resp.json()


def browse(sso_cookie_value: str | None = None, token: str | None = None) -> dict | None:
    """Zwraca {"recent_files": [...], "shared_folders": [...], "all_folders": [...]}."""
    if not sso_cookie_value and not token:
        return None
    try:
        resp = requests.get(f"{_api_base()}/browse", timeout=TIMEOUT,
                             **_auth_kwargs(sso_cookie_value, token))
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    return resp.json()


def quick_share_file(file_id: int, sso_cookie_value: str | None = None, token: str | None = None) -> str | None:
    """Tworzy (albo zwraca istniejący) link do pliku. Zwraca share_url albo None."""
    if not sso_cookie_value and not token:
        return None
    try:
        resp = requests.post(
            f"{_api_base()}/files/{file_id}/quick-share",
            timeout=TIMEOUT,
            **_auth_kwargs(sso_cookie_value, token),
        )
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    return resp.json().get("share_url")


def quick_share_folder(folder_id: int, sso_cookie_value: str | None = None, token: str | None = None) -> str | None:
    """Tworzy (albo zwraca istniejący) link do folderu. Zwraca share_url albo None."""
    if not sso_cookie_value and not token:
        return None
    try:
        resp = requests.post(
            f"{_api_base()}/folders/{folder_id}/quick-share",
            timeout=TIMEOUT,
            **_auth_kwargs(sso_cookie_value, token),
        )
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    return resp.json().get("share_url")
