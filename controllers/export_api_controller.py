"""
API server-to-server dla innych appek spiętych przez LoginHub (na razie:
planowiec), żeby mogły pobrać "plany" (egzaminy) użytkownika z koloseum
i zaimportować je u siebie.

Autoryzacja: nagłówek X-SSO-Api-Key musi się zgadzać z SSO_SECRET (ta sama
wartość, która już jest współdzielona między appkami do podpisywania
ciasteczka SSO — używamy jej też jako klucza API między appkami, tak jak
LoginHub robi to na /api/resolve).

Ten endpoint NIE używa sesji/ciasteczek usera — appka wołająca (planowiec)
sama najpierw ustala lokalny user_id w koloseum (przez LoginHub /api/resolve,
patrz sso_client.resolve_local_user_id) i przekazuje go tutaj jako parametr.
"""
import os

from flask import Blueprint, jsonify, request, abort

from services.exam_service import ExamService
from utils.timezone import to_local

export_api_bp = Blueprint("export_api", __name__)


def _check_api_key():
    expected = os.environ.get("SSO_SECRET")
    provided = request.headers.get("X-SSO-Api-Key")
    if not expected or not provided or provided != expected:
        abort(401)


@export_api_bp.route("/api/my-exams")
def my_exams():
    _check_api_key()

    user_id = request.args.get("user_id", type=int)
    if not user_id:
        abort(400)

    exams = ExamService.get_all_user_exams(user_id)
    return jsonify([
        {
            "id": e.id,
            "title": e.title,
            "description": e.description or "",
            "exam_date": to_local(e.exam_date).isoformat(),
            "location": e.location or "",
            "exam_type": e.exam_type,
            "subject": e.subject.name if e.subject else "",
        }
        for e in exams
    ])
