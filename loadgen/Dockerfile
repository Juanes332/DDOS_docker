FROM python:3.12-alpine

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /lab

COPY http_flood.py slowloris.py conn_exhaust.py ./

RUN adduser -D -u 10002 loadgen
USER loadgen

# Sin privilegios, sin sockets crudos, sin NET_ADMIN.
ENTRYPOINT ["python"]
CMD ["http_flood.py", "--help"]
