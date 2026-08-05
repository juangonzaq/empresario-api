# sensor_sunat — VIGÍA prototype (SIRE tax sensor)

Read-only Django commands against SUNAT's **production** SIRE APIs (there is no
sandbox). Django admin is the dashboard. Spec:
`VIGIA_Spec_Prototipo_Django_para_Claude_Code.md`. Manual-vs-reality notes:
`docs/DESVIACIONES.md`.

## Credentials

Set in `.env` (never commit; `.env` is git-ignored). **Never print these in
logs or expose them in admin**:

```
SUNAT_RUC=20604442533
SUNAT_SOL_USER=...        # SECONDARY Clave SOL user (falls back to SUNAT_USER)
SUNAT_SOL_PASS=...        # (falls back to SUNAT_PASS)
SUNAT_CLIENT_ID=...       # SOL > Credenciales de API SUNAT
SUNAT_CLIENT_SECRET=...
```

## Commands (spec name → implemented name, code in English)

| Spec | Command |
|---|---|
| `sunat_smoke` | `python manage.py sunat_smoke` — P0: token + real RVIE periods |
| `sunat_sync_periodos` | `python manage.py sunat_sync_periods` |
| `sunat_sync_rvie` | `python manage.py sunat_sync_rvie --periodo 202606` |
| `sunat_sync_rce` | `python manage.py sunat_sync_rce --periodo 202606` |
| `sunat_sync_ssco` | `python manage.py sunat_sync_ssco` |
| `sunat_check_padron` | `python manage.py sunat_check_padron` |
| `sunat_run_rules` | `python manage.py sunat_run_rules` |

All commands are idempotent and exit non-zero printing SUNAT's full error JSON
when something fails. Raw downloads are always saved under `media/sunat_raw/`
before parsing (RawArtifact rows point at them).

## Safety rails

* URL whitelist + vetoed words (`aceptar`, `registra`, `upload`, `elimina`,
  `grab`, `importar`) in `sunat_client.py` — write endpoints cannot be called.
* One SUNAT operation at a time (file lock in `media/sunat_raw/.sunat.lock`).
