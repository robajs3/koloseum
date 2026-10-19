import os
from flask import Blueprint, jsonify, render_template, request
from flask_login import login_required, current_user
from services.search_service import (
    SearchService, CATEGORY_LABELS, FV_CATEGORY_LABELS, MIN_QUERY_LEN, normalize_query,
)

search_bp = Blueprint("search", __name__, url_prefix="/search")

SSO_COOKIE_NAME = os.environ.get("SSO_COOKIE_NAME", "sso_session")
ALL_LABELS = {**CATEGORY_LABELS, **FV_CATEGORY_LABELS}


def _limit(default: int, maximum: int = 50) -> int:
    return max(1, min(request.args.get("limit", default, type=int) or default, maximum))


@search_bp.route("/api")
@login_required
def api():
    """Lokalne dane aplikacji (pokoje, przedmioty, terminy, materiały,
    załączniki i wiadomości czatów, użytkownicy)."""
    q = normalize_query(request.args.get("q"))
    results = SearchService.search(current_user, q, limit=_limit(6))
    return jsonify({
        "q": q,
        "results": results,
        "labels": {k: CATEGORY_LABELS[k] for k in results},
    })


@search_bp.route("/api/filevault")
@login_required
def api_filevault():
    """Pliki/foldery z FileVault — osobno, bo to zdalne API."""
    q = normalize_query(request.args.get("q"))
    results, err = SearchService.search_filevault(
        current_user, q, request.cookies.get(SSO_COOKIE_NAME), limit=_limit(6))
    if err:
        return jsonify({"q": q, "error": err, "results": {}, "labels": {}}), (400 if err == "not_linked" else 502)
    return jsonify({
        "q": q,
        "results": results,
        "labels": {k: FV_CATEGORY_LABELS[k] for k in results},
    })


@search_bp.route("")
@login_required
def page():
    """Pełna strona wyników (Enter w pasku / link „Pokaż wszystkie”).
    Wyniki lokalne renderowane po stronie serwera, FileVault dociągany
    przez JS (patrz static/js/search.js)."""
    q = normalize_query(request.args.get("q"))
    results = SearchService.search(current_user, q, limit=30) if len(q) >= MIN_QUERY_LEN else {}
    return render_template(
        "search.html", q=q, results=results, labels=CATEGORY_LABELS,
        too_short=bool(q) and len(q) < MIN_QUERY_LEN,
    )
