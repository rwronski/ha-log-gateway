ARG BUILD_FROM
FROM ${BUILD_FROM}

ENV \
  PYTHONUNBUFFERED=1 \
  PYTHONDONTWRITEBYTECODE=1

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install runtime dependencies (no bashio; options parsed via jq)
RUN \
  apk add --no-cache \
    bash \
    ca-certificates \
    jq \
    python3 \
    py3-pip \
  && \
  python3 -m venv /venv && \
  /venv/bin/pip install --no-cache-dir --upgrade pip

WORKDIR /app

COPY requirements.txt /tmp/requirements.txt
RUN /venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt

COPY app /app
COPY rootfs /

RUN chmod a+x /etc/services.d/log_gateway/run

ENV PATH="/venv/bin:${PATH}"
