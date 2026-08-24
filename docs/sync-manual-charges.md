# Traer comprobantes: tope diario, cargo por exceso e historial

Reemplaza la campana de notificaciones (era un placeholder) por el control
**«Traer comprobantes»** en la barra superior (`SyncDrawer`): checklist de
fuentes, sincronización manual con barra de progreso, cuota del día e historial.

## Automáticas (ya existía)

Corren solas **por empresa, cada madrugada**: `sync.daily` (04:00 America/Lima)
→ `sync_all(JobKind.DAILY)` hace fan-out sobre las empresas conectadas.
Programadas en la BD (django_celery_beat, migración `sync/0003_tenant_schedules`).
No se tocó.

## Manuales: tope y cargo

- **Tope diario por empresa**: `SYNC_MANUAL_DAILY_LIMIT` (default **2**), overridable
  por empresa desde el admin (`Organization.manual_sync_daily_limit`). Solo cuentan
  las sincronizaciones **completas** manuales (`SyncJob kind=MANUAL`), no el
  «traer nuevos» por-fuente (`run_source`), que sigue libre.
- Pasado el tope, **se permite** pero genera un **cargo de S/5**
  (`SYNC_EXTRA_MANUAL_PRICE`) que se **registra** (no cobra al instante) como
  `billing.UsageCharge` y se lista en **Suscripción › Cargos por uso**. El dueño
  del sistema lo liquida/marca cobrado desde el admin.
- El flujo: `POST /api/sync/start/` con `{only?, accept_charge?}`. Bajo el tope →
  arranca gratis. Sobre el tope y sin `accept_charge` → **409** `code:
  "sync_charge_required"` con la cuota (no 402: el front intercepta 402 como
  «suscripción vencida»). Con `accept_charge` → arranca y registra el cargo.
  Un trabajo ya en marcha se devuelve sin contar ni cobrar (una sesión SOL).

Lógica en `sync/services.py`: `manual_quota(org)`, `start_manual_sync(org, user,
only, accept_charge)`, `SyncLimitReached`. Cargo en `billing/services.py`:
`charge_extra_manual_sync`, `record_usage_charge`, `usage_charges`.

## Checklist (subconjunto de fuentes)

`start_sync(..., only=[keys])` limita los pasos; `execute()` corre las fuentes
presentes en `job.steps` (que por defecto son las de la cadencia, pero pueden ser
el subconjunto elegido). El catálogo de fuentes sale de `sync/sources.py::SOURCES`.

## Historial

`GET /api/sync/history/` → `{quota, sources, jobs}` (últimos 15). Distingue
manual vs automática por `kind`, resalta fallidas (`failed_steps`), y alimenta el
punto rojo del ícono cuando la última terminó `fallido`/`parcial`.

## Endpoints nuevos

- `GET /api/sync/history/`
- `POST /api/sync/start/` (ahora con `only`/`accept_charge`, respuesta con
  `charged`/`quota`)
- `GET /api/billing/charges/`
