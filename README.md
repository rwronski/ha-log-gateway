# HA Log Gateway add-on repository

Dodaj to repo jako niestandardowe w HA i zainstaluj add-on `log_gateway`.

Repo URL: `https://github.com/rwronski/ha-log-gateway`

Wymagania: Home Assistant OS/Supervised, dostęp do Supervisora (`hassio_api`).

Konfiguracja add-onu: ustaw `api_token` (Bearer), opcjonalnie `z2m_slug` (domyslnie `45df7312_zigbee2mqtt`), `mosquitto_slug` (domyslnie `core_mosquitto`), `lines_default/max` (1000).
