import requests
from flask import current_app

class FileVaultService:
    @staticmethod
    def _base():
        return current_app.config["FILEVAULT_BASE_URL"]

    @staticmethod
    def _headers():
        return {"X-API-Token": current_app.config["FILEVAULT_API_TOKEN"]}

    @staticmethod
    def verify():
        """Zwraca dane usera lub None przy złym tokenie."""
        try:
            r = requests.get(
                FileVaultService._base() + "/api/verify",
                headers=FileVaultService._headers(),
                timeout=5,
            )
            return r.json() if r.ok else None
        except requests.RequestException:
            return None