# Módulo de obligaciones y cumplimiento

Llena la sección **Obligaciones** con un mapa vivo de lo que la empresa debe
cumplir: qué le aplica, si lo cumple, quién responde y qué lo prueba. Regla de
oro: **los modelos existentes son la fuente de verdad; este módulo solo los
interpreta.** No re-guarda facturas, trabajadores ni declaraciones.

App: `obligations/` (código en inglés, PK UUID `core.BaseModel`, scoping por
`account_ruc` vía `OrganizationAPIView`). API bajo `/api/obligations/` (el
prefijo `/api/compliance/` ya lo usa `compliance_profile`).

## Modelo

Catálogo global (versionado, mismo para todas las empresas):
- `ComplianceDomain` — áreas (TAX, LABOR, CORPORATE, DATA_PROTECTION, MUNICIPAL,
  CONSUMER).
- `ComplianceRule` — cada obligación/control. `applicability` es JSON declarativo
  (`{"all"|"any": [{field, operator, value}]}`, sin código en BD); la lógica dura
  vive tras un `evaluator_key` en un registro controlado (`services/evaluators.py`).

Por empresa (`account_ruc`):
- `CompanyObligation` — la regla aterrizada. Estados en **ejes separados**:
  `applicability` / `compliance` / `workflow` / `verification` / `severity`.
  OVERDUE/DUE_SOON/UPCOMING **se calculan** de `due_date`, no se guardan.
  UniqueConstraint(account_ruc, rule).
- `ObligationAssessment` — historial explicable (guarda `input_snapshot`).
- `ObligationEvidence` — puntero a datos que ya existen (o URL/label). No duplica archivos.
- `ComplianceAction` — tareas (no había modelo de tareas genérico).
- `ComplianceSnapshot` — foto diaria para la tendencia.

## Motor (`services/`)

`context.build_context(org)` arma una foto de solo lectura desde los modelos
existentes: `Organization.tax_regime`, `RucSnapshot` (worker_count, señales de
riesgo, actividades), conteo `Colaborador.is_active`, `DeclaredSummary`,
`ConsistencyScore`, `ComplianceRating` (categoría A–E). Un hecho que la
plataforma no conoce entra al contexto como `None`, nunca como `""`/`0`.

**La aplicabilidad es ternaria** (idea central tomada de la matriz de
responsabilidades, `matriz_responsabilidades_empresariales_peru.md`):
`evaluate_applicability` devuelve True/False/None con lógica de Kleene
(`false AND unknown = false`, `true OR unknown = true`). Ausencia de dato →
`ApplicabilityStatus.UNKNOWN` («Por determinar») más la pregunta pendiente
(`FIELD_QUESTION`); **nunca** un «no te aplica» concluido del silencio. Solo un
hecho explícito produce NOT_APPLICABLE. Hechos declarados: los tri-estados del
`BusinessProfile` (`sells_to_consumers`, `has_premises`, `sells_online`) y
`worker_count` (planilla propia → ficha RUC → perfil completado; sin ninguna
fuente es desconocido). `company.is_juridical` sale del prefijo del RUC (20…).

`engine.evaluate_company(org)` recorre el catálogo, decide aplicabilidad, corre
el evaluador, superpone evidencia y decisión humana (precedencia creciente),
hace upsert y anexa un assessment solo cuando el veredicto cambia. Cuando no hay
datos, el veredicto es UNKNOWN/UNVERIFIED con una razón honesta — nunca un check
que no se puede sustentar, nunca una brecha presentada como evasión.

Evaluadores automáticos hoy: `tax_monthly_declaration` (¿está el periodo en tus
`DeclaredSummary`?), `consistency_control` (lee `ConsistencyScore`),
`risk_signals_clear` (ficha RUC), `payroll_registration`. El resto queda
manual/por-evidencia.

**Scoring** (`scoring.py`): dos números aparte.
- `compliance_score` = peso de lo aplicable-cumplido / peso aplicable × 100
  (NOT_APPLICABLE excluido; pesos desde `ComplianceRule.weight`/severidad). Se
  devuelve con `calculation` (compliant_weight, applicable_weight, method).
- `priority_score` = severidad × incumplimiento × atraso × brecha de evidencia.
  **Solo ordena**; nunca se muestra como % de cumplimiento.

**Recalcular**: no en cada GET. `overview` reevalúa solo si está viejo
(>6 h) o `?force=1`. Además `POST /recalculate`, y tareas Celery
(`obligations.recalculate_company`, `obligations.snapshot_all`) — encólalas con
`transaction.on_commit()` desde la app que cambia el dato.

## API

- `GET /api/obligations/overview/` — toda la pantalla en un payload (summary+score,
  alerta ejecutiva, distribución, dominios, prioridades, tendencia, próximos
  vencimientos). `?force=1` fuerza recálculo.
- `GET /api/obligations/` — lista filtrable (`domain, compliance_status,
  workflow_status, verification_status, severity, owner, due_before/after,
  has_evidence, state, search`).
- `GET/PATCH /api/obligations/<id>/` — detalle; PATCH set `workflow_status`/`owner_email`.
- `POST /api/obligations/<id>/evidence/`, `POST /api/obligations/<id>/actions/`,
  `PATCH /api/obligations/actions/<id>/`.
- `POST /api/obligations/recalculate/`.

## Front

`/obligaciones` con `?tab=` (Resumen · Todas las obligaciones), charts en recharts
(hex que reflejan los tokens), barra segmentada por tokens, y drawer de detalle
(primitiva compartida) con estados, «qué hacer», evidencia y plan de acción.
Enlaza a `/calendario` (vencimientos SUNAT) y a `/compliance/sunat` (categoría A–E)
en vez de duplicarlos.

## Qué falta / siguiente

- Vencimientos por regla desde el cronograma de `sensor_sunat` (hoy `due_date` lo
  fija el evaluador o queda vacío).
- Más evaluadores automáticos (SIRE, PLAME) y explicación con IA (opcional).
- Catálogo revisado por contador/abogado y ampliable desde el admin.
- Enganchar `recalculate_company` en `on_commit` de las apps fuente y una beat
  diaria para `snapshot_all`.

El catálogo (`migrations/0002_seed_catalog.py` + `0005_matrix_catalog.py`) trae
26 obligaciones PYME-PE en 6 dominios — umbrales laborales (RIT >100, cuota de
discapacidad >50), contribuciones por sector (SENCICO, SENATI), bancarización y
el Libro de Reclamaciones físico/virtual —, no el universo completo. Tras tocar
el catálogo, regenera `fixtures/datos_generales.json` con el mismo `dumpdata`.
