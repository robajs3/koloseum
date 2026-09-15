"""
filevault_panel_controller.py — globalne endpointy AJAX dla wysuwanego
panelu "FileVault" (skrót "Dysk" w sidebarze + przycisk "Wybierz z
FileVault" na stronie przedmiotu). Nie są przypisane do konkretnego
przedmiotu/pokoju — to po prostu "podejrzyj/wybierz coś z MOJEGO konta
FileVault", więc jedyne co sprawdzamy to czy user jest zalogowany."""
import os
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from services import filevault_client

filevault_panel_bp = Blueprint("filevault_panel", __name__)

SSO_COOKIE_NAME = os.environ.get("SSO_COOKIE_NAME", "sso_session")


def _sso_cookie() -> str | None:
    return request.cookies.get(SSO_COOKIE_NAME)


@filevault_panel_bp.route("/filevault-panel/browse")
@login_required
def browse():
    sso_cookie = _sso_cookie()
    if not sso_cookie and not current_user.filevault_api_token:
        return jsonify({"error": "not_linked"}), 400
    data = filevault_client.browse(sso_cookie_value=sso_cookie, token=current_user.filevault_api_token)
    if data is None:
        return jsonify({"error": "unavailable"}), 502
    return jsonify(data)


@filevault_panel_bp.route("/filevault-panel/quick-share-file/<int:file_id>", methods=["POST"])
@login_required
def quick_share_file(file_id: int):
    sso_cookie = _sso_cookie()
    if not sso_cookie and not current_user.filevault_api_token:
        return jsonify({"error": "not_linked"}), 400
    share_url = filevault_client.quick_share_file(
        file_id, sso_cookie_value=sso_cookie, token=current_user.filevault_api_token
    )
    if not share_url:
        return jsonify({"error": "failed"}), 502
    return jsonify({"share_url": share_url})


@filevault_panel_bp.route("/filevault-panel/quick-share-folder/<int:folder_id>", methods=["POST"])
@login_required
def quick_share_folder(folder_id: int):
    sso_cookie = _sso_cookie()
    if not sso_cookie and not current_user.filevault_api_token:
        return jsonify({"error": "not_linked"}), 400
    share_url = filevault_client.quick_share_folder(
        folder_id, sso_cookie_value=sso_cookie, token=current_user.filevault_api_token
    )
    if not share_url:
        return jsonify({"error": "failed"}), 502
    return jsonify({"share_url": share_url})
