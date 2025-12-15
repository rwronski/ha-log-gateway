# Log Gateway add-on (skeleton)

- Port: `8099/tcp`
- Auth: `Authorization: Bearer <api_token>`
- Snapshot only (no follow). Default/max lines: 1000.
- Endpoints:
  - `GET /healthz`
  - `GET /files/z2m` (lista dozwolonych plików konfiguracyjnych Z2M)
    - (działa też `GET /files/z2m/`)
  - `GET /files/z2m/<name>` (np. `configuration.yaml`, `devices.yaml`, `groups.yaml`, `coordinator_backup.json`)
  - `GET /files/z2m/external_converters` (lista `*.js`)
    - (działa też `GET /files/z2m/external_converters/`)
  - `GET /files/z2m/external_converters/<name>` (np. `esp-air-sensor.js`)
  - `GET /logs/system` (Supervisor `/host/logs`)
  - `GET /logs/core` (merged: `/core/logs` + `/config/home-assistant.log*`)
  - `GET /logs/supervisor` (Supervisor `/supervisor/logs`)
  - `GET /logs/z2m` (Supervisor `/addons/<z2m_slug>/logs`, domyślnie `45df7312_zigbee2mqtt`)
    - Domyślnie zwraca ostatnie 1000 linii *bez* `debug:` (nadpobiera i filtruje).
    - Dodaj `?include_debug=true`, aby dostać raw `debug:`.

Configure `api_token` in add-on options; add-on uses `SUPERVISOR_TOKEN` provided by HA Supervisor. `init: false` is required for s6-overlay.
If `/logs/*` returns 502 with 403 upstream, ensure add-on config includes `hassio_role: manager` (required for host/add-on logs).

Z2M files are searched in both `/config/zigbee2mqtt` and `/all_addon_configs/<z2m_slug>`; the response includes `X-LogGateway-Path` with the chosen source.
