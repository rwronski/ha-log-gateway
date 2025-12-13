ARG BUILD_FROM
FROM ${BUILD_FROM}

ENV \
  PYTHONUNBUFFERED=1 \
  PYTHONDONTWRITEBYTECODE=1

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install runtime dependencies and bashio for config handling
RUN \
  apk add --no-cache \
    bash \
    bashio \
    ca-certificates \
    curl \
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
COPY run.sh /run.sh

RUN chmod a+x /run.sh

ENV PATH="/venv/bin:${PATH}"

CMD ["/run.sh"]
