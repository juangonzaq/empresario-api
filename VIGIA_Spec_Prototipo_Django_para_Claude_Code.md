# VIGÍA — Sensor Tributario SUNAT · PROTOTIPO DJANGO
## Especificación para Claude Code — una sola app, sobre proyecto existente, sin tests

> **Instrucción para Claude Code:** este documento reemplaza cualquier especificación anterior. Contexto real:
> - El proyecto Django **ya existe**. NO crear proyecto nuevo, NO tocar apps existentes, NO cambiar configuración global salvo lo mínimo indicado en §1.
> - Todo el sensor vive en **UNA sola app**: `sensor_sunat`.
> - Es un **prototipo**: sin tests, sin CI, sin Celery, sin Redis, sin S3, sin multi-tenant real. Prioridad absoluta: **ver datos reales de SUNAT en Django admin lo antes posible.**
> - Los manuales PDF en `docs/` son la **fuente de verdad** de URLs y parámetros:
>   - `docs/Manual_SIRE_Ventas_v30.pdf` (RVIE)
>   - `docs/Manual_SIRE_Compras_v22.pdf` (RCE)
>   - `docs/Manual_Consulta_Integrada_v2.pdf` (validarcomprobante) — opcional, hito P4
> - Donde diga `[EXTRAER DEL MANUAL §X.Y]`: abrir el PDF, ir a esa sección, copiar la URL/parámetros exactos. **Prohibido inventar rutas.** Si la realidad difiere del manual, anotarlo en `docs/DESVIACIONES.md` y seguir.
> - **Prohibido llamar endpoints de escritura de SUNAT** (aceptar propuesta, registrar preliminar, cargar archivos, eliminar). Solo lectura. Implementar la lista blanca de §3.

---

# 0. QUÉ SE ESTÁ CONSTRUYENDO (en una frase)

Comandos de Django que se conectan a las APIs oficiales de SUNAT (SIRE Ventas y Compras) con las credenciales de PATTERN GROUP S.A.C. (RUC `20604442533`), descargan lo que SUNAT sabe de la empresa (períodos, ventas, compras, inconsistencias, casillas, cumplimiento), lo guardan en modelos, cruzan proveedores contra la lista negra SSCO, generan alertas — y todo se ve en **Django admin**, que es el dashboard del prototipo.

---

# 1. INSTALACIÓN EN EL PROYECTO EXISTENTE (cambios mínimos permitidos)

```bash
python manage.py startapp sensor_sunat
pip install httpx python-dotenv    # si el proyecto no los tiene; nada más
```

Únicos cambios fuera de la app:
1. `INSTALLED_APPS += ["sensor_sunat"]`
2. En `settings.py`, leer de variables de entorno (o `.env` si el proyecto ya usa dotenv):

```python
SUNAT = {
    "RUC": os.getenv("SUNAT_RUC", "20604442533"),
    "SOL_USER": os.getenv("SUNAT_SOL_USER"),          # usuario SECUNDARIO de Clave SOL
    "SOL_PASS": os.getenv("SUNAT_SOL_PASS"),
    "CLIENT_ID": os.getenv("SUNAT_CLIENT_ID"),
    "CLIENT_SECRET": os.getenv("SUNAT_CLIENT_SECRET"),
    "TOKEN_URL": "https://api-seguridad.sunat.gob.pe/v1/clientessol/{client_id}/oauth2/token/",
    "SCOPE": "https://api-sire.sunat.gob.pe",
    "BASE": "https://api-sire.sunat.gob.pe/v1/contribuyente/migeigv/libros",
    "COD_LIBRO_RVIE": "140000",
    "COD_LIBRO_RCE": "080000",
}
MEDIA_SUNAT_DIR = BASE_DIR / "media" / "sunat_raw"   # ZIPs y TXT crudos en disco local
```

Advertencia visible en README de la app: las credenciales van en `.env` **fuera de git**; nunca imprimirlas en logs ni en admin. (Cifrado en BD queda para producción, no para el prototipo — pero el `.env` no se commitea jamás.)

---

# 2. ESTRUCTURA DE LA APP (todo aquí, nada afuera)

```
sensor_sunat/
├── models.py
├── admin.py
├── sunat_client.py        # auth + HTTP + patrón ticket (un solo archivo)
├── parsers.py             # TXT de propuestas → dicts
├── rules.py               # motor de alertas (funciones puras)
├── management/commands/
│   ├── sunat_smoke.py         # P0: token + períodos
│   ├── sunat_sync_periodos.py
│   ├── sunat_sync_rvie.py     # --periodo 202606
│   ├── sunat_sync_rce.py      # --periodo 202606
│   ├── sunat_sync_ssco.py
│   ├── sunat_check_padron.py  # verifica estado/condición de proveedores
│   ├── sunat_run_rules.py     # evalúa reglas → crea Alertas
│   └── sunat_validar_cpe.py   # P4, opcional
└── migrations/
```

Sin vistas, sin urls, sin templates: **el admin es la UI**.

---

# 3. CLIENTE SUNAT (`sunat_client.py`) — contrato

```python
ALLOWED_PATH_FRAGMENTS = [
    "/oauth2/token/", "/periodos", "/exporta", "/consulta", "/descarga",
    "/casillas", "/inconsistencias", "/cumplimiento", "/resumen",
    "/validarcomprobante",  # P4
    # + añadir aquí cada ruta de LECTURA extraída del manual, explícitamente
]
# Cualquier URL cuyo path no contenga uno de estos fragmentos → raise ForbiddenEndpoint.
# Palabras vetadas en cualquier URL: "aceptar", "registra", "upload", "elimina", "grab", "importar"
```

Funciones mínimas:
- `get_token()` → POST al TOKEN_URL con `grant_type=password, scope, client_id, client_secret, username=f"{RUC} {SOL_USER}"` `[VERIFICAR EN MANUAL §5.1: con o sin espacio entre RUC y usuario — el manual muestra "{RUC} {USUARIO}"]`, `password=SOL_PASS`, `Content-Type: application/x-www-form-urlencoded`. Cachear en memoria de proceso con expiración (usar `expires_in` de la respuesta, renovar al 80%). Ante 401 en cualquier llamada: renovar una vez y reintentar una vez.
- `get(url, **params)` / `post(url, json=None)` → añade `Authorization: Bearer`, `Accept: application/json`. Timeout 60s. Reintento simple: 3 intentos con espera 5s/15s/45s SOLO en 5xx o timeout; un 422 se lanza tal cual con su JSON (`cod`, `msg`, `errors[]`) — es bug nuestro, no reintentar.
- `fetch_ticket_result(dispatch_fn) -> Path`:
  1. `dispatch_fn()` devuelve `numTicket` (formato `AAAA99999999`).
  2. Poll a **consultar estado de ticket** `[EXTRAER DEL MANUAL — Ventas §5.16 / Compras §5.31]` cada 15s (tope 30 min, backoff hasta 120s). Estados según Anexo III del manual de Ventas.
  3. En `Terminado` → obtener nombre(s) de archivo → **descargar archivo** `[EXTRAER — Ventas §5.17 / Compras §5.32]`. Puede venir ZIP **particionado**: descargar todas las partes, ensamblar, descomprimir.
  4. Guardar crudo en `MEDIA_SUNAT_DIR/<endpoint>/<periodo>/<timestamp>/` y devolver la ruta. **Siempre guardar el crudo antes de parsear.**
- Nunca ejecutar en paralelo dos operaciones contra SUNAT (lock simple con archivo o cache de Django).

---

# 4. MODELOS (`models.py`) — prototipo, un solo tenant implícito

```python
class Periodo(models.Model):
    libro = models.CharField(max_length=4, choices=[("RVIE","RVIE"),("RCE","RCE")])
    per_tributario = models.CharField(max_length=6)          # yyyymm
    estado = models.CharField(max_length=20, default="?")    # texto que devuelva SUNAT
    sincronizado = models.DateTimeField(auto_now=True)
    class Meta: unique_together = ("libro","per_tributario")

class VentaDoc(models.Model):
    per_tributario = models.CharField(max_length=6, db_index=True)
    tipo_cdp = models.CharField(max_length=2); serie = models.CharField(max_length=8)
    numero = models.CharField(max_length=12); fecha_emision = models.DateField(null=True)
    ruc_cliente = models.CharField(max_length=11, blank=True)
    razon_cliente = models.CharField(max_length=200, blank=True)
    base = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    igv = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    total = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    car_sunat = models.CharField(max_length=30, blank=True)
    raw_extra = models.JSONField(default=dict)               # columnas no mapeadas del TXT
    class Meta: unique_together = ("tipo_cdp","serie","numero")

class CompraDoc(models.Model):   # espejo de VentaDoc con ruc_proveedor/razon_proveedor
    ...
    reconocida = models.BooleanField(null=True)              # None = sin revisar (regla R6)

class Proveedor(models.Model):
    ruc = models.CharField(max_length=11, unique=True)
    razon_social = models.CharField(max_length=200, blank=True)
    estado_padron = models.CharField(max_length=20, blank=True)      # ACTIVO / BAJA...
    condicion_padron = models.CharField(max_length=20, blank=True)   # HABIDO / NO HABIDO
    en_ssco = models.BooleanField(default=False)
    total_comprado = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    igv_en_riesgo = models.DecimalField(max_digits=14, decimal_places=2, default=0)

class SscoEntry(models.Model):
    ruc = models.CharField(max_length=11, unique=True)
    razon_social = models.CharField(max_length=200, blank=True)
    detalle = models.JSONField(default=dict); capturado = models.DateTimeField(auto_now_add=True)

class CasillaSnapshot(models.Model):
    libro = models.CharField(max_length=4); per_tributario = models.CharField(max_length=6)
    casilla = models.CharField(max_length=4); monto = models.DecimalField(max_digits=14, decimal_places=2)

class Inconsistencia(models.Model):
    libro = models.CharField(max_length=4); per_tributario = models.CharField(max_length=6)
    tipo = models.CharField(max_length=60); detalle = models.JSONField(default=dict)
    resuelta = models.BooleanField(default=False)

class RawArtifact(models.Model):
    endpoint = models.CharField(max_length=80); params = models.JSONField(default=dict)
    ruta_local = models.CharField(max_length=300); creado = models.DateTimeField(auto_now_add=True)

class Alerta(models.Model):
    SEVERIDAD = [("ROJO","Rojo"),("AMBAR","Ámbar"),("INFO","Info")]
    regla = models.CharField(max_length=6)                   # R1..R12
    severidad = models.CharField(max_length=6, choices=SEVERIDAD)
    titulo = models.CharField(max_length=200); detalle = models.JSONField(default=dict)
    monto_en_riesgo = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    vence = models.DateField(null=True)
    estado = models.CharField(max_length=10, default="OPEN")
    creada = models.DateTimeField(auto_now_add=True)
```

`admin.py`: registrar TODO con `list_display`, `list_filter` (per_tributario, severidad, en_ssco, reconocida) y `search_fields` (ruc, serie+numero). En `Alerta`, acción de admin "marcar resuelta". En `CompraDoc`, acción "marcar reconocida / no reconocida".

---

# 5. ENDPOINTS A INTEGRAR (en este orden exacto)

Mismo catálogo maestro que la spec original, reducido al prototipo. `{base}` = SUNAT["BASE"].

| Orden | Qué | URL | Tipo |
|---|---|---|---|
| 1 | Períodos RVIE | `GET {base}/rvierce/padron/web/omisos/140000/periodos` | SYNC |
| 2 | Períodos RCE | análogo con `080000` `[VERIFICAR Compras §5.33]` | SYNC |
| 3 | Propuesta RVIE | `[EXTRAER Ventas §5.18]` — el layout del TXT de salida está en el propio manual/anexos | TICKET |
| 4 | Propuesta RCE | `[EXTRAER Compras §5.34]` | TICKET |
| 5 | Casillas RVIE | `[EXTRAER Ventas §5.23]` (Anexo V: casillas 100,101,102,103,105,106,109,112) | TICKET |
| 6 | Casillas RCE | `[EXTRAER Compras §5.41]` | TICKET |
| 7 | Inconsistencias por comprobante RVIE | `[EXTRAER Ventas §5.25]` | TICKET |
| 8 | Inconsistencias por comprobante RCE | `[EXTRAER Compras §5.44]` | TICKET |
| 9 | Cumplimiento RVIE | `{base}/rvierce/cumplimiento/web/omisos/{per}/140000/consultaReporteCumplimiento/exportardocumento` | TICKET |
| 10 | Cumplimiento RCE | `[EXTRAER Compras §5.58]` | TICKET |
| 11 | Constancia de recepción RVIE/RCE | `[EXTRAER Ventas §5.26 / Compras §5.49]` (en RCE v22 el PDF llega como **Bytes**) | ver manual |
| 12 | FV0621 | `GET {base}/rce/propuesta/web?periodoSeleccionado={per}&tipoInfo=FV0621` | SYNC |
| 13 | Estadístico clientes RVIE | `[EXTRAER Ventas §5.33]` | TICKET |
| 14 | Estadístico proveedores RCE | `[EXTRAER Compras §5.54]` | TICKET |

**Fuentes públicas (sin token):**
- SSCO: `https://www.sunat.gob.pe/padronesnotificaciones/sujeSinCapacidadOperativa.html` → parsear tabla/archivo publicado a `SscoEntry`.
- Padrón: para el prototipo NO cargar los 11M de filas. `sunat_check_padron` descarga el ZIP diario de `https://www.sunat.gob.pe/descargaPRR/mrc137_padron_reducido.html`, lo **streamea** filtrando solo los RUCs presentes en `Proveedor` + el RUC propio, y actualiza `estado_padron/condicion_padron`. Cero inflado de la BD.

---

# 6. COMANDOS (contratos)

```
python manage.py sunat_smoke
  → obtiene token, llama Períodos RVIE, imprime tabla en consola. Sin BD. ÉXITO = ver períodos reales.

python manage.py sunat_sync_periodos
  → endpoints 1 y 2 → upsert Periodo.

python manage.py sunat_sync_rvie --periodo 202606
  → propuesta (3) + casillas (5) + inconsistencias (7) + cumplimiento (9)
  → VentaDoc / CasillaSnapshot / Inconsistencia / RawArtifact. Upsert por unique_together; nunca duplicar.

python manage.py sunat_sync_rce --periodo 202606
  → análogo (4,6,8,10,12) + reconstruir Proveedor (agregado por ruc: total_comprado, Σ igv).

python manage.py sunat_sync_ssco
  → actualiza SscoEntry y marca Proveedor.en_ssco + calcula igv_en_riesgo.

python manage.py sunat_check_padron
  → actualiza estado/condición de cada Proveedor y del RUC propio.

python manage.py sunat_run_rules
  → evalúa reglas (§7) sobre lo que haya en BD y crea Alertas (sin duplicar: una alerta OPEN por regla+objeto).
```

Todos los comandos: idempotentes (correrlos dos veces no duplica nada), verbosos por consola, y ante error de SUNAT imprimen el JSON de error completo y terminan con exit code ≠ 0.

---

# 7. REGLAS DEL PROTOTIPO (`rules.py` — funciones puras sobre el ORM)

| Regla | Condición | Severidad |
|---|---|---|
| R1 | Periodo con estado que indique pendiente/omiso | ROJO |
| R2 | Inconsistencia sin resolver en el último período | AMBAR (ROJO si el detalle trae monto > 5,000) |
| R3 | Proveedor.en_ssco == True | ROJO · monto_en_riesgo = igv_en_riesgo |
| R4 | Proveedor.condicion_padron == "NO HABIDO" o estado ≠ "ACTIVO" | AMBAR |
| R6 | CompraDoc.reconocida is None con más de 7 días en BD | AMBAR ("revisa estas compras") |
| R7 | Un ruc_cliente concentra > 30% del total de VentaDoc del trimestre | INFO |
| R8 | Σ VentaDoc.total del año ≥ 85% de 1'650,000 (300 UIT 2026) | AMBAR |
| R9 | Σ ≥ 80% de 9'350,000 (1,700 UIT) | AMBAR |
| R11 | No existe constancia de recepción del período vencido | ROJO ("¿tu contador registró?") |

(R5, R10, R12 quedan fuera del prototipo.) Constantes UIT/umbrales al inicio de `rules.py` con comentario "actualizar cada enero".

---

# 8. HITOS (parar y mostrar al usuario al final de cada uno)

- **P0 — Humo (medio día):** app instalada, `sunat_smoke` devuelve los períodos reales de 20604442533. *Si esto funciona, todo lo demás es plomería.*
- **P1 — Ventas (1-2 días):** `sunat_sync_rvie --periodo <último vencido>` puebla VentaDoc + casillas + inconsistencias; se navega en admin.
- **P2 — Compras + SSCO (1-2 días):** `sunat_sync_rce`, `sunat_sync_ssco`, `sunat_check_padron`; en admin se ve la lista de proveedores con su semáforo.
- **P3 — Alertas (1 día):** `sunat_run_rules` genera Alertas visibles/gestionables en admin. **Fin del prototipo.**
- **P4 — (opcional, pedir aprobación):** `sunat_validar_cpe` con el manual de Consulta Integrada (token por `clientesextranet`, endpoint `POST https://api.sunat.gob.pe/v1/contribuyente/contribuyentes/{RUC}/validarcomprobante`).

# 9. RECORDATORIOS FINALES PARA EL AGENTE

1. No hay sandbox de SUNAT: todo es producción real. Por eso: solo lectura, lista blanca, y probar cada endpoint primero con un script suelto antes de cablearlo al comando.
2. Guardar SIEMPRE el crudo antes de parsear; si el parser falla, el dato no se pierde.
3. El layout de los TXT (posiciones/columnas de la propuesta) está en los anexos de los manuales — leerlo de ahí, no adivinar separadores.
4. Ante cualquier ambigüedad manual-vs-realidad: registrar en `docs/DESVIACIONES.md` (sección, esperado, obtenido, fecha) y continuar con lo observado.
5. Nada de credenciales en logs, en admin, ni en commits. `.env` en `.gitignore` desde el primer commit.
