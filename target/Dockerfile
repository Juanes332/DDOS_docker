FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /srv

COPY app.py /srv/app.py

# Usuario sin privilegios: la victima no necesita root para nada.
RUN useradd --create-home --uid 10001 lab && chown -R lab:lab /srv
USER lab

EXPOSE 8000

HEALTHCHECK --interval=5s --timeout=2s --start-period=5s --retries=6 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=1)"

CMD ["python", "/srv/app.py"]
