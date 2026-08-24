# Motor de conciliación tributaria y financiera

Cruza CPE, SIRE (RVIE/RCE), lo declarado, el ITF y los movimientos bancarios de una
empresa y periodo, para detectar **diferencias que requieren revisión** — nunca para
concluir omisiones. Código en `reconciliation/`; la IA solo redacta sobre resultados ya
calculados.

## Arquitectura

```
reconciliation/
  models.py            ReconciliationRun · DocumentReconciliation · DeclaredSummary
                       BankMovement · InvoiceSettlement(+Line) · ConsistencyScore
  engine/
    normalization.py   CPE y SIRE → NormalizedDoc (clave tipo-serie-número)
    cpe_sire.py        cruce documento a documento, niveles ok/warning/review/critical
    declarations.py    totales SIRE vs DeclaredSummary (621)
    itf_analysis.py    ITF como contraste (ratios), con su advertencia fija
    matching.py        facturas ↔ abonos (1→N, N→1, parciales; evidencia y confianza)
    banking.py         clasificación por reglas de movimientos; lo del usuario manda
    alerts.py          hallazgos → FinanceAlert (dedup `recon:<tipo>:<periodo>`)
    score.py           0–100 con desglose; lo justificado/corregido no descuenta
    run.py             orquestador: run_reconciliation(ruc, periodo)
    ai.py              explicación con IA (OpenAI structured output), vocabulario acotado
  views.py / urls.py   /api/reconciliation/{summary,run,documents,movements,explain}
```

Los estados de alerta ganaron `justificada` y `corregida` (spec); se gestionan en la
pantalla de Alertas existente. Front: Finanzas › **Conciliación**.

## Fuentes y sus huecos conocidos

| Fuente | Modelo | Estado |
| --- | --- | --- |
| CPE emitidos/recibidos | `sunat_cpe.ElectronicInvoice` | ✅ sincroniza por empresa |
| SIRE RVIE/RCE | `sensor_sunat.SalesDoc/PurchaseDoc` | ⚠️ ver abajo |
| Declaración 621 | `reconciliation.DeclaredSummary` | manual/import; SIRE cuando esté |
| ITF | `sunat_itf.ItfRecord` | ✅ |
| Bancos | `reconciliation.BankMovement` | manual/import (sin feed bancario aún) |

**SIRE**: las credenciales de API en `.env` se generaron **sin el alcance de SIRE** — el
token sale con `aud` solo de `api-cpe` y la API responde 401. Corrección: SOL → Empresas →
*Credenciales de API SUNAT* → generar credenciales marcando también las API de **SIRE**, y
actualizar `SUNAT_CLIENT_ID/SECRET`. Además `sensor_sunat` es single-tenant (sin columna
RUC): sus filas pertenecen a `settings.SUNAT["RUC"]`; para otras empresas el motor declara
«SIRE sin sincronizar» y no penaliza.

## Reglas de oro

1. Determinístico primero; la IA no calcula ni concluye (schema y prompt lo prohíben).
2. Ninguna diferencia se presenta como evasión/omisión; siempre «inconsistencia»,
   «pendiente de clasificar», «requiere revisión», con causas posibles.
3. Facturación ≠ cobranza: `billing_period` y `collection_period` viven separados.
4. Decisión humana manda: clasificación `user` y alertas justificadas sobreviven a
   cualquier re-ejecución; el score las perdona.

## Umbrales (env)

`RECON_AMOUNT_TOLERANCE=1.00` · `RECON_DATE_TOLERANCE_DAYS=3` ·
`RECON_CRITICAL_AMOUNT=1000` · `RECON_DECLARATION_TOLERANCE=5.00` ·
`RECON_DECLARATION_CRITICAL_GAP=1000` · `RECON_ITF_RATIO_WARNING=2.0` ·
`RECON_ITF_RATIO_REVIEW=4.0` · matching: `RECON_MATCH_*`.
