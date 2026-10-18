FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv/jana
COPY requirements.txt /srv/jana/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && useradd -r -u 10001 appuser
COPY app /srv/jana/app
COPY data/gazetteer.json data/demo_input.json /srv/jana/data/
COPY data/geo /srv/jana/data/geo
COPY NOTICE.md /srv/jana/NOTICE.md
COPY docs/geo-provenance /srv/jana/docs/geo-provenance
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2)" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
