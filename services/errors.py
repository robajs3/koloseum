"""errors.py — wyjątki wspólne dla modułów podsumowań AI."""


class AIError(Exception):
    """Błąd, którego komunikat można bezpiecznie pokazać użytkownikowi."""


class AIBackendError(AIError):
    """Problem po stronie serwera AI (Ollama niedostępna, timeout, brak modelu).
    Dotyczy całego zadania, nie jednego pliku — przy przeglądzie folderu nie ma
    sensu próbować kolejnych plików, więc przerywamy całość."""
