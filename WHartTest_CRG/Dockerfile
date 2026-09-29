ARG PYTHON_BASE_IMAGE=python:3.11-alpine
FROM ${PYTHON_BASE_IMAGE} AS builder

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG ALPINE_MIRROR=https://dl-cdn.alpinelinux.org/alpine
WORKDIR /app
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.txt /app/requirements.txt
RUN sed -i "s#https://dl-cdn.alpinelinux.org/alpine#${ALPINE_MIRROR}#g" /etc/apk/repositories && \
    apk add --no-cache build-base linux-headers && \
    python -m venv /opt/venv && \
    /opt/venv/bin/python -m pip install --no-cache-dir -i "${PIP_INDEX_URL}" -r /app/requirements.txt

FROM ${PYTHON_BASE_IMAGE}

ARG ALPINE_MIRROR=https://dl-cdn.alpinelinux.org/alpine
ARG OCI_REVISION=unknown
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH=/opt/venv/bin:$PATH \
    CRG_REPOSITORY_ROOT=/repositories \
    CRG_GRAPH_ROOT=/graphs \
    CRG_BUILD_TIMEOUT=600 \
    CRG_QUERY_TIMEOUT=120

RUN sed -i "s#https://dl-cdn.alpinelinux.org/alpine#${ALPINE_MIRROR}#g" /etc/apk/repositories && \
    apk add --no-cache git libstdc++ libgcc

COPY --from=builder /opt/venv /opt/venv
RUN code-review-graph --version

COPY app.py worker.py /app/
RUN addgroup -g 10001 crg && \
    adduser -D -u 10001 -G crg crg && \
    mkdir -p /repositories /graphs && \
    chown -R crg:crg /app /graphs

USER crg
LABEL org.opencontainers.image.revision="${OCI_REVISION}"
EXPOSE 8080
HEALTHCHECK --interval=20s --timeout=5s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')"
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
