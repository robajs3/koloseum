FROM python:3.12-slim

# Zależności systemowe potrzebne do zbudowania psycopg2-binary / Pillow
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq-dev gcc curl \
    && rm -rf /var/lib/apt/lists/*

# Opcjonalnie: LibreOffice do podglądu starych formatów (DOC/XLS/PPT/ODG…) — konwersja do PDF.
# Domyślnie wyłączone (~500 MB); build: docker compose build --build-arg INSTALL_LIBREOFFICE=1
# Nowe formaty (DOCX/XLSX/PPTX/ODF/CSV/IPYNB/RTF) działają bez niego.
ARG INSTALL_LIBREOFFICE=0
RUN if [ "$INSTALL_LIBREOFFICE" = "1" ]; then \
      apt-get update \
      && apt-get install -y --no-install-recommends libreoffice-writer libreoffice-calc libreoffice-impress fonts-liberation \
      && rm -rf /var/lib/apt/lists/*; \
    fi

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_ENV=production

EXPOSE 5000

# Skrypt startowy poczeka na bazę i utworzy tabele/foldery przed uruchomieniem gunicorna
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["gunicorn", "-b", "0.0.0.0:5000", "--workers", "3", "--access-logfile", "-", "wsgi:application"]
