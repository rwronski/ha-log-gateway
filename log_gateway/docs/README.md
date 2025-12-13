# Log Gateway add-on (skeleton)

- Port: `8099/tcp`
- Auth: `Authorization: Bearer <api_token>`
- Snapshot only (no follow). Default/max lines: 1000.
- Endpoints (placeholders until Supervisor wiring is added):
  - `GET /healthz`
  - `GET /logs/system` (Supervisor `/host/logs`)
  - `GET /logs/z2m` (Supervisor `/addons/<z2m_slug>/logs`, domyślnie `45df7312_zigbee2mqtt`)

Configure `api_token` in add-on options. Next steps: wire `/host/logs` and `/addons/<slug>/logs` via Supervisor with `SUPERVISOR_TOKEN`, enforce allowlist and rate limiting.
