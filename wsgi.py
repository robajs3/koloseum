"""
Punkt wejścia dla serwera produkcyjnego (Gunicorn).

Użycie:
    gunicorn -b 0.0.0.0:5000 wsgi:application
"""

from app import create_app, init_db

application = create_app()
init_db(application)

# Alias, bo część narzędzi (np. `flask run`) szuka "app"
app = application
