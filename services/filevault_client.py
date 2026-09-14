"""
filevault_client.py — cienki klient do wewnętrznego API FileVault.

Koloseum i FileVault stoją na tym samym serwerze i są połączone tym samym
LoginHub (SSO), ale to dwie osobne appki Flask z osobnymi sesjami — sesja
przeglądarki z jednej appki nie jest widoczna dla drugiej po stronie
backendu. Żeby Koloseum mogło w imieniu zalogowanego usera zapytać FileVault
o jego pliki, user musi raz wkleić w swoim profilu token API wygenerowany
w panelu FileVault (Profil -> Token API). Ten moduł używa tego tokenu do
wywołań server-to-server (nagłówek X-API-Token), więc żadne hasło nigdy nie
przechodzi przez Koloseum.

Każda funkcja zwraca None przy błędzie sieciowym/HTTP, żeby wywołujący kod
mógł to potraktować jako "FileVault chwilowo niedostępny" i nie wywalić się.
"""
import requests
from flask import current_app

TIMEOUT = 4


def _api_base() -> str:
    return current_app.config.get("FILEVAULT_INTERNAL_URL", "http://127.0.0.1:5000").rstrip("/") + "/filevault/api"


def verify_token(token: str) -> dict | None:
    """Sprawdza, czy token jest ważny. Zwraca {"username": ..., "email": ...} albo None."""
    if not token:
        return None
    try:
        resp = requests.get(f"{_api_base()}/verify", headers={"X-API-Token": token}, timeout=TIMEOUT)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    return resp.json()


def browse(token: str) -> dict | None:
    """Zwraca {"recent_files": [...], "shared_folders": [...], "all_folders": [...]}."""
    if not token:
        return None
    try:
        resp = requests.get(f"{_api_base()}/browse", headers={"X-API-Token": token}, timeout=TIMEOUT)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    return resp.json()


def quick_share_file(token: str, file_id: int) -> str | None:
    """Tworzy (albo zwraca istniejący) link do pliku. Zwraca share_url albo None."""
    if not token:
        return None
    try:
        resp = requests.post(
            f"{_api_base()}/files/{file_id}/quick-share",
            headers={"X-API-Token": token},
            timeout=TIMEOUT,
        )
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    return resp.json().get("share_url")


def quick_share_folder(token: str, folder_id: int) -> str | None:
    """Tworzy (albo zwraca istniejący) link do folderu. Zwraca share_url albo None."""
    if not token:
        return None
    try:
        resp = requests.post(
            f"{_api_base()}/folders/{folder_id}/quick-share",
            headers={"X-API-Token": token},
            timeout=TIMEOUT,
        )
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    return resp.json().get("share_url")
