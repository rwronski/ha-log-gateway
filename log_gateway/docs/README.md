# Log Gateway add-on (skeleton)

- Port: `8099/tcp`
- Auth: `Authorization: Bearer <api_token>`
- Snapshot only (no follow). Default/max lines: 1000.
- Endpoints:
  - `GET /healthz`
  - `GET /logs/system` (Supervisor `/host/logs`)
  - `GET /logs/z2m` (Supervisor `/addons/<z2m_slug>/logs`, domyślnie `45df7312_zigbee2mqtt`)

Configure `api_token` in add-on options; add-on uses `SUPERVISOR_TOKEN` provided by HA Supervisor.
