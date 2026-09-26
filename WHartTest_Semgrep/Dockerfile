ARG PYTHON_BASE_IMAGE=python:3.11-slim
FROM ${PYTHON_BASE_IMAGE}

ARG PIP_INDEX_URL=https://pypi.org/simple
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 SEMGREP_SEND_METRICS=off

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -i "${PIP_INDEX_URL}" -r /app/requirements.txt && \
    semgrep --version

COPY app.py /app/app.py
COPY rules/ /app/rules/
RUN useradd --create-home --uid 10001 scanner && chown -R scanner:scanner /app
USER scanner
EXPOSE 8080
HEALTHCHECK --interval=20s --timeout=5s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')"
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
