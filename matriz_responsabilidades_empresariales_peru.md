# Matriz maestra de responsabilidades empresariales y motor de aplicabilidad — Perú

```yaml
document_id: PE-BUSINESS-RESPONSIBILITY-MATRIX
version: 1.0.0
jurisdiction: PE
language: es-PE
as_of_date: 2026-08-24
intended_use:
  - diseño de base de datos
  - cuestionario de perfil empresarial
  - motor de reglas de aplicabilidad
  - generación de calendario y evidencias de cumplimiento
status: implementation_baseline
legal_review_required: true
```

## 1. Propósito, respuesta corta y límites

Sí: las responsabilidades de una empresa dependen de una combinación de su forma jurídica, régimen tributario, actividades reales, sector, establecimientos y ubicaciones, número y características de sus trabajadores, clientes y canales de venta, tratamiento de datos, activos, operaciones especiales y relaciones contractuales. No basta con conocer el RUC o el CIIU principal.

Este documento convierte esa idea en una especificación utilizable para:

1. pedir al cliente los datos correctos;
2. normalizar sus respuestas como hechos empresariales;
3. contrastar esos hechos con reglas versionadas;
4. obtener responsabilidades aplicables, no aplicables, recomendadas o pendientes de revisión;
5. generar vencimientos, evidencias y trazabilidad.

La matriz cubre la base transversal de empresas privadas que operan en Perú y crea rutas de revisión para actividades reguladas. No pretende enumerar cada permiso de cada municipalidad ni sustituye un dictamen legal, tributario, laboral, ambiental o sectorial. Una regla sectorial debe validarse contra la norma especial, el TUPA y la autoridad competente vigentes para el caso concreto.

### 1.1. Lo que no debe hacer el sistema

- No asumir `false` cuando falta información.
- No decidir por el nombre comercial o por un único CIIU.
- No confundir forma jurídica, régimen tributario y régimen laboral MYPE.
- No afirmar que una licencia municipal reemplaza una autorización sectorial.
- No tratar una recomendación de gestión como obligación legal.
- No usar una fecha o umbral anual sin versión y fuente.
- No ocultar que una regla requiere interpretación profesional.

### 1.2. La distinción correcta no es “institución pública o privada”

La fuente de una responsabilidad puede ser:

| `duty_nature` | Significado | Ejemplo |
|---|---|---|
| `STATUTORY` | Impuesta por ley o reglamento | declaración tributaria, planilla, Libro de Reclamaciones |
| `PERMIT_CONDITION` | Condición de una licencia, registro, autorización o IGA | obligaciones de una autorización sanitaria o ambiental |
| `CONTRACTUAL` | Nace de un contrato privado o público | covenant bancario, SLA, póliza, contrato con el Estado |
| `VOLUNTARY_RISK_CONTROL` | Control recomendado para proteger valor y reducir riesgo | seguro patrimonial, continuidad, modelo de prevención |
| `GROWTH_ENABLER` | Habilitador voluntario de crecimiento | registro de marca, CITE, ProInnóvate, certificación de calidad |

Una AFP o aseguradora puede ser privada y, sin embargo, administrar una obligación establecida por ley. Por eso la columna decisiva es `duty_nature`, no la naturaleza pública o privada de la entidad receptora.

## 2. Arquitectura del matching

```text
respuestas del cliente
  -> hechos normalizados y fechados
  -> reglas de aplicabilidad versionadas
  -> resultado por responsabilidad
  -> ocurrencias y fechas límite
  -> evidencias, estado y auditoría
```

### 2.1. Resultado permitido

| `match_status` | Uso obligatorio |
|---|---|
| `APPLIES` | Todos los hechos necesarios prueban que la responsabilidad aplica. |
| `DOES_NOT_APPLY` | Hechos explícitos prueban que no aplica; nunca se obtiene solo por ausencia de datos. |
| `UNKNOWN` | Falta al menos un dato bloqueante. Debe generar preguntas, no una conclusión. |
| `REVIEW_REQUIRED` | Hay actividad regulada, excepción, conflicto de fuentes o concepto jurídico que requiere evaluación humana. |
| `RECOMMENDED` | Es un control de protección o crecimiento, no una exigencia legal general. |

### 2.2. Unidad de evaluación

La responsabilidad puede evaluarse en distintos alcances:

| `scope_type` | Ejemplo |
|---|---|
| `COMPANY` | declaración anual de renta, junta obligatoria anual |
| `ESTABLISHMENT` | licencia de funcionamiento, ITSE, lactario |
| `WORKER` | alta en T-Registro, seguro Vida Ley, SCTR |
| `PERSON` | titular de datos, beneficiario, cliente individual |
| `PRODUCT` | registro sanitario, rotulado, homologación |
| `PROJECT` | certificación ambiental, licencia de edificación |
| `VEHICLE` | SOAT/CAT, inspección técnica |
| `REAL_ESTATE` | predial, arbitrios, obra o cambio de uso |
| `TRANSACTION` | detracción, bancarización, guía de remisión |
| `SHIPMENT` | despacho, documento de control, origen y transporte |
| `CONTRACT` | garantía, SLA, cláusula anticorrupción |
| `DATA_BANK` | inscripción y actualización ante la ANPD |
| `DATA_FLOW` | transferencia o flujo transfronterizo |
| `DIGITAL_SYSTEM` | CCTV, biometría, plataforma, sistema crítico |
| `CHANNEL` | tienda, web, app, teléfono o marketplace |
| `CAMPAIGN` | publicidad y prospección comercial |
| `INCIDENT` | accidente laboral, brecha de datos, emergencia ambiental |
| `CASE_OR_PROCEDURE` | reclamo, fiscalización, licitación o trámite |
| `COUNTERPARTY` | proveedor, cliente, banco, encargado u otro tercero |
| `AUTHORIZATION` | renovación y condiciones del título habilitante |

La columna humana “Alcance / ciclo” del catálogo puede mostrar más de un alcance para explicar la operación. En el seed normalizado, cada registro debe usar un único `scope_type`; si una obligación se instancia tanto por empresa como por local, el motor genera registros de alcance vinculados y no guarda `C/E` como un enum inválido.

### 2.3. Fórmula conceptual

```text
perfil aplicable =
  forma jurídica
  + régimen tributario y padrones
  + actividades reales y CIIU secundarios
  + productos, servicios y proyectos
  + establecimientos y jurisdicciones
  + trabajadores, umbrales y exposiciones
  + consumidores y canales
  + datos personales y tecnología
  + activos e impacto ambiental
  + comercio exterior y contratación pública
  + autorizaciones y contratos
```

## 3. Diseño del cuestionario del cliente

### 3.1. Reglas de captura

Cada respuesta debe conservar como mínimo:

```json
{
  "question_code": "TAX-001",
  "field_name": "tax_regime",
  "value": "RMT",
  "as_of_date": "2026-08-24",
  "answered_by": "REPRESENTATIVE",
  "answer_status": "DECLARED",
  "evidence_document_id": null,
  "confidence": "MEDIUM",
  "notes": null
}
```

Valores recomendados para `answer_status`: `DECLARED`, `EVIDENCE_VERIFIED`, `EXTERNAL_SOURCE_VERIFIED`, `UNKNOWN`, `DISPUTED`, `EXPIRED`.

Para preguntas binarias se debe usar `true`, `false` o `null`. `null` significa desconocido y jamás debe convertirse automáticamente en `false`.

### 3.2. Datos mínimos que bloquean el primer match

Estos campos forman el onboarding mínimo. Después se abren preguntas condicionales.

| Campo | Pregunta al cliente | Tipo |
|---|---|---|
| `profile_as_of_date` | ¿A qué fecha describe la empresa? | `date` |
| `ruc` | ¿Cuál es el RUC? | `char(11)` |
| `legal_name` | ¿Cuál es la razón social o nombre registrado? | `text` |
| `taxpayer_type` | ¿Opera como persona natural, persona jurídica, sucursal u otro ente? | `enum` |
| `legal_form` | ¿Cuál es la forma jurídica? | `enum` |
| `incorporation_date` | ¿Cuándo se constituyó? | `date/null` |
| `operations_start_date` | ¿Cuándo inició actividades? | `date` |
| `operates_in_peru` | ¿Realiza actividades, ventas, servicios o tiene presencia en Perú? | `tri_state` |
| `ruc_status` | ¿El RUC está activo? | `enum` |
| `tax_domicile_condition` | ¿Tiene condición habido/no habido? | `enum` |
| `tax_regime` | ¿NRUS, RER, RMT, Régimen General u otro? | `enum` |
| `primary_ciiu` | ¿Cuál es el CIIU principal registrado? | `code` |
| `actual_activities` | ¿Qué vende, fabrica, transforma, importa, distribuye o presta realmente? | `array<object>` |
| `annual_net_revenue_pen` | ¿Ingresos netos del último ejercicio? | `decimal/null` |
| `net_assets_pen_previous_year_end` | ¿Activos netos al cierre del ejercicio anterior? | `decimal/null` |
| `establishments` | Liste cada oficina, tienda, planta, almacén, taller u operación. | `array<object>` |
| `worker_count_total` | ¿Cuántos trabajadores dependientes tiene? | `integer` |
| `service_provider_count` | ¿Cuántos prestadores independientes o personal de terceros utiliza? | `integer` |
| `sells_to_end_consumers` | ¿Vende bienes o servicios a consumidores finales? | `tri_state` |
| `sales_channels` | ¿Presencial, web, app, marketplace, teléfono u otros? | `set` |
| `processes_personal_data` | ¿Trata datos de clientes, trabajadores, prospectos, cámaras o usuarios? | `tri_state` |
| `imports_goods` | ¿Importa bienes? | `tri_state` |
| `exports_goods_or_services` | ¿Exporta bienes o servicios? | `tri_state` |
| `contracts_with_state` | ¿Cotiza o contrata con entidades públicas? | `tri_state` |
| `owns_real_estate` | ¿Es propietaria de predios? | `tri_state` |
| `owns_or_operates_vehicles` | ¿Posee u opera vehículos? | `tri_state` |
| `regulated_activity_flags` | Marque las familias de actividad regulada aplicables. | `set` |
| `has_sector_authorizations` | ¿Cuenta con autorizaciones, registros o concesiones sectoriales? | `tri_state` |
| `unknown_material_facts` | ¿Qué información importante no pudo confirmar? | `array` |

## 4. Diccionario completo de hechos empresariales

### 4.1. Identidad, constitución y gobierno

| `field_name` | Tipo | Qué preguntar o verificar | Por qué importa |
|---|---|---|---|
| `country_of_incorporation` | `char(2)` | País de constitución. | Sucursal, no domiciliado y grupo extranjero. |
| `ruc` | `char(11)` | RUC. | Llave de verificación SUNAT/UIF/RNP. |
| `legal_name` | `text` | Razón social. | Identidad legal. |
| `trade_names` | `text[]` | Nombres comerciales usados. | Consumidor, marca y licencias locales. |
| `taxpayer_type` | `enum` | Persona natural, jurídica, sucursal, consorcio u otro. | Regímenes disponibles y responsabilidad. |
| `legal_form` | `enum` | EIRL, SAC, SA, SAA, SRL, SACS, sucursal, cooperativa, etc. | Gobierno, libros y acuerdos societarios. |
| `registry_office` | `text/null` | Oficina registral y partida SUNARP. | Verificación societaria. |
| `registry_entry` | `text/null` | Número de partida. | Poderes y actos inscritos. |
| `incorporation_date` | `date/null` | Constitución. | Antigüedad y gobierno. |
| `operations_start_date` | `date` | Inicio efectivo. | Impuestos, permisos y planilla. |
| `operates_in_peru` | `tri_state` | Si realiza operaciones o atiende mercado peruano. | Jurisdicción y alcance territorial. |
| `fiscal_year_end` | `month-day` | Cierre del ejercicio. | Junta y estados financieros. |
| `ruc_status` | `enum` | Activo, baja, suspensión, etc. | Capacidad operativa tributaria/aduanera. |
| `tax_domicile_condition` | `enum` | Habido/no habido/pendiente. | Riesgo tributario y aduanero. |
| `legal_representatives` | `array<object>` | Identidad, cargo, poder y vigencia. | Firmas y trámites. |
| `directors` | `array<object>` | Miembros y vigencia. | Gobierno y conflictos. |
| `shareholders_or_owners` | `array<object>` | Titulares, porcentaje y país. | Gobierno, beneficiario final, vinculadas. |
| `beneficial_owners` | `array<object>` | Persona natural, criterio de control y porcentaje. | Declaración de beneficiario final/LAFT. |
| `belongs_to_economic_group` | `tri_state` | Grupo local o multinacional. | Precios de transferencia, AML y consolidación. |
| `parent_company_country` | `char(2)/null` | País de matriz. | No domiciliados, datos, vinculadas. |
| `listed_or_public_offering` | `tri_state` | Valores inscritos/oferta pública. | Reglas SMV y gobierno reforzado. |
| `corporate_books_status` | `enum` | Libros societarios existentes, legalizados y al día. | Evidencia de acuerdos. |
| `last_annual_meeting_date` | `date/null` | Última junta obligatoria anual. | Cumplimiento societario. |
| `last_financial_statements_approved_date` | `date/null` | Aprobación de EE. FF. | Gobierno y distribución. |
| `material_unregistered_changes` | `array` | Gerente, poderes, capital, domicilio, estatuto pendientes. | Regularización registral. |

### 4.2. Perfil tributario y financiero

| `field_name` | Tipo | Qué preguntar o verificar | Disparadores principales |
|---|---|---|---|
| `tax_regime` | `enum` | Régimen actual y fecha de vigencia. | Declaraciones, libros, límites. |
| `tax_regime_history` | `array<object>` | Cambios por periodo. | Evaluación histórica. |
| `taxes_registered` | `set` | IGV, renta, ISC, planilla, etc. | Obligaciones declarativas. |
| `ruc_last_digit` | `smallint` | Último dígito. | Cronogramas. |
| `is_prico` | `tri_state` | Principal contribuyente. | Cronogramas/SIRE/controles. |
| `is_good_taxpayer` | `tri_state` | Padrón. | Cronogramas. |
| `is_igv_withholding_agent` | `tri_state` | Designación SUNAT. | Retenciones. |
| `is_igv_perception_agent` | `tri_state` | Designación SUNAT. | Percepciones. |
| `electronic_issuer_status` | `enum` | Obligado/voluntario/no incorporado. | CPE. |
| `sire_status` | `enum` | Incorporado y fecha, o pendiente. | RVIE/RCE. |
| `annual_net_revenue_pen` | `decimal/null` | Último ejercicio. | Régimen, libros, DAOT, beneficiario final. |
| `annual_revenue_uit` | `decimal/null` | Calculado con UIT del ejercicio. | Umbrales versionados. |
| `annual_purchases_pen` | `decimal/null` | Compras del ejercicio. | Límites NRUS/RER. |
| `monthly_max_revenue_pen` | `decimal/null` | Mayor mes. | NRUS. |
| `monthly_max_purchases_pen` | `decimal/null` | Mayor mes. | NRUS. |
| `net_assets_pen_previous_year_end` | `decimal/null` | Activos netos al 31 de diciembre anterior. | ITAN. |
| `fixed_assets_pen_exclusions_applied` | `decimal/null` | Activos computables para NRUS. | Elegibilidad NRUS. |
| `rer_fixed_assets_pen_exclusions_applied` | `decimal/null` | Activos fijos afectados a la actividad, sin predios ni vehículos. | Límite RER. |
| `max_workers_per_shift` | `integer/null` | Mayor número de personas afectadas a la actividad en un turno. | Límite RER. |
| `has_tax_regime_excluded_activity` | `tri_state` | Actividad expresamente excluida del régimen elegido. | Elegibilidad NRUS/RER/RMT. |
| `keeps_accounting_books` | `set` | Libros y modalidad. | Brecha contable. |
| `has_related_party_transactions` | `tri_state` | Operaciones con vinculadas. | Precios de transferencia. |
| `has_tax_haven_transactions` | `tri_state` | País/territorio no cooperante o preferencial. | Precios de transferencia. |
| `multinational_group_revenue` | `decimal/null` | Ingreso consolidado. | Reporte maestro/país por país. |
| `payments_to_nonresidents` | `array<object>` | Tipo, país, monto, CDI. | Retenciones/no domiciliados. |
| `performs_spot_transactions` | `tri_state` | Bienes/servicios sujetos a detracción. | SPOT. |
| `cash_or_nonbanked_payments` | `array<object>` | Pagos sobre umbral sin medio financiero. | Bancarización. |
| `senati_activity` | `tri_state` | Actividad industrial alcanzada y trabajadores. | Contribución SENATI. |
| `sencico_activity` | `tri_state` | Contratos/actividades de construcción. | Contribución SENCICO. |
| `tax_audit_or_debt_open` | `tri_state` | Fiscalización, deuda, fraccionamiento. | Riesgo y calendario. |

### 4.3. Actividades, productos, proyectos y autorizaciones

Cada actividad debe ser un registro, no una cadena libre única.

| `field_name` | Tipo | Contenido |
|---|---|---|
| `activity_id` | `uuid` | Identificador. |
| `description_plain` | `text` | Descripción operacional: qué hace realmente. |
| `ciiu_code` | `varchar(8)/null` | CIIU Rev. 4 asociado. |
| `is_primary` | `boolean` | Actividad principal registral. |
| `is_current` | `boolean` | Se realiza a la fecha. |
| `activity_role` | `enum` | `MANUFACTURE`, `IMPORT`, `EXPORT`, `DISTRIBUTE`, `RETAIL`, `SERVICE`, `OPERATE`, `STORE`, `TRANSPORT`, `CONSTRUCT`. |
| `product_or_service_categories` | `text[]` | Categorías concretas. |
| `customer_types` | `set` | B2C, B2B, Estado, exterior. |
| `performed_at_establishment_ids` | `uuid[]` | Dónde se ejecuta. |
| `regulated_activity_flags` | `set` | Rutas sectoriales que abre. |
| `requires_professional_in_charge` | `tri_state` | Director técnico/colegiado/responsable. |
| `sector_authority_codes` | `text[]` | Autoridades conocidas. |

Por cada autorización:

| `field_name` | Tipo | Contenido |
|---|---|---|
| `authorization_type` | `text` | Licencia, registro, concesión, certificado, IGA, habilitación. |
| `authority_code` | `text` | Autoridad emisora. |
| `authorization_number` | `text/null` | Número. |
| `scope_reference` | `text` | Empresa, local, producto, proyecto o vehículo. |
| `issue_date` | `date/null` | Emisión. |
| `valid_from` | `date/null` | Inicio. |
| `expires_at` | `date/null` | Vencimiento. |
| `status` | `enum` | Solicitada, vigente, suspendida, vencida, revocada. |
| `conditions` | `array<object>` | Obligaciones particulares del título. |
| `renewal_lead_days` | `integer/null` | Anticipación operativa. |
| `evidence_document_id` | `uuid/null` | Resolución/certificado. |

### 4.4. Establecimientos y ubicación

Repetir por cada domicilio fiscal, oficina, tienda, planta, almacén, taller, centro de atención, obra o punto operativo.

| `field_name` | Tipo | Pregunta/disparador |
|---|---|---|
| `establishment_type` | `enum` | Oficina, tienda, planta, almacén, taller, obra, remoto, otro. |
| `address` | `text` | Dirección completa. |
| `ubigeo` | `char(6)` | Departamento/provincia/distrito. |
| `municipality_code` | `text` | Municipalidad competente. |
| `is_declared_in_ruc` | `tri_state` | ¿Figura como establecimiento anexo? |
| `ownership_type` | `enum` | Propio, arrendado, cedido, coworking, domicilio virtual. |
| `zoning_or_compatibility_status` | `enum` | Compatible, pendiente, desconocido. |
| `public_access` | `boolean/null` | Atiende público. |
| `floor_area_m2` | `decimal/null` | Área. |
| `floors_used` | `integer/null` | Pisos/áreas utilizadas. |
| `capacity_people` | `integer/null` | Aforo. |
| `stores_hazardous_materials` | `tri_state` | Combustibles, químicos, explosivos u otros. |
| `prepares_or_stores_food` | `tri_state` | Alimentos. |
| `has_advertising_signs` | `tri_state` | Anuncios exteriores. |
| `has_construction_or_remodeling` | `tri_state` | Obra o cambio de uso. |
| `municipal_risk_level` | `enum` | Bajo, medio, alto, muy alto, pendiente. |
| `operating_license_status` | `enum` | Vigente, trámite, no tiene, desconocido. |
| `itse_status` | `enum` | Vigente, trámite, no tiene, desconocido. |
| `workers_at_site` | `integer` | Trabajadores del local. |
| `women_age_15_49_at_site` | `integer/null` | Umbral de lactario por centro. |
| `third_party_workers_at_site` | `integer` | Coordinación SST. |
| `has_cctv` | `tri_state` | Datos personales y seguridad. |
| `generates_nonmunicipal_waste` | `tri_state` | Ambiental. |
| `is_project_site` | `boolean` | Permisos de proyecto/obra. |

### 4.5. Personal, relaciones laborales y SST

| `field_name` | Tipo | Qué captura |
|---|---|---|
| `worker_count_total` | `integer` | Dependientes activos. |
| `average_worker_count_year` | `decimal/null` | Promedio anual; cuotas/utilidades. |
| `worker_count_by_month` | `array<object>` | Conteo sustentado por mes para promedios y reglas anuales. |
| `labor_regime_flags` | `set` | Régimen general, REMYPE y regímenes especiales presentes; el derecho puede variar por trabajador y fecha de ingreso. |
| `service_provider_count` | `integer` | Independientes. |
| `training_modality_count` | `integer` | Practicantes/modalidades formativas. |
| `must_withhold_fourth_or_fifth_income` | `tri_state` | Si paga rentas sujetas a retención o concurre otro supuesto de Planilla Electrónica. |
| `third_party_worker_count` | `integer` | Tercerización/intermediación. |
| `foreign_worker_count` | `integer` | Personal extranjero. |
| `adolescent_worker_count` | `integer` | Trabajadores de 14 a 17 años. |
| `remote_worker_count` | `integer` | Teletrabajo. |
| `women_age_15_49_total` | `integer/null` | Orientación; la regla final es por local. |
| `workers_with_disability_count` | `integer/null` | Cuota y ajustes. |
| `union_count` | `integer` | Relaciones colectivas. |
| `remype_status` | `enum` | No inscrita, micro, pequeña, desconocida. |
| `remype_registration_date` | `date/null` | Derechos aplicables por periodo. |
| `payroll_status` | `enum` | T-Registro/PLAME implementado. |
| `pension_systems_used` | `set` | ONP, AFP. |
| `eps_used` | `tri_state` | EPS. |
| `vida_ley_coverage` | `enum` | Completa, parcial, no, desconocida. |
| `high_risk_activity` | `tri_state` | Actividad o exposición SCTR. |
| `sctr_health_status` | `enum` | Cobertura. |
| `sctr_pension_status` | `enum` | Cobertura. |
| `sst_committee_or_supervisor` | `enum` | Comité, supervisor, no implementado. |
| `sst_system_status` | `enum` | Implementado, parcial, no, desconocido. |
| `occupational_exam_status` | `enum` | Vigencia y cobertura. |
| `sexual_harassment_body` | `enum` | Comité, delegado, no implementado. |
| `internal_work_rules_status` | `enum` | RIT aprobado/pendiente/no requerido. |
| `salary_category_table_status` | `enum` | Cuadro de categorías y funciones. |
| `salary_policy_status` | `enum` | Política remunerativa comunicada. |
| `labor_intermediation_provider` | `tri_state` | Si presta intermediación, revisar RENEEIL. |
| `work_accidents_last_12m` | `integer` | Registros y notificaciones. |
| `fatal_or_dangerous_incident_open` | `tri_state` | Notificación 24 h/investigación. |

### 4.6. Clientes, consumidores y canales

| `field_name` | Tipo | Qué captura |
|---|---|---|
| `sells_to_end_consumers` | `tri_state` | Relación de consumo. |
| `sales_channels` | `set` | Tienda, web, app, marketplace, teléfono, social commerce. |
| `websites_and_apps` | `array<object>` | Dominio/app, titular y mercado. |
| `has_physical_customer_service` | `tri_state` | Libro físico y aviso. |
| `has_online_sales` | `tri_state` | Libro virtual, términos y evidencia. |
| `claims_book_status` | `enum` | Implementado físico/virtual/ambos/no. |
| `claims_response_process` | `enum` | Flujo y plazo. |
| `advertises_prices` | `tri_state` | Precio total y condiciones. |
| `offers_credit_or_subscriptions` | `tri_state` | Información, cancelación y sector financiero. |
| `uses_standard_form_contracts` | `tri_state` | Cláusulas abusivas/transparencia. |
| `performs_direct_marketing` | `tri_state` | Consentimiento y oposición. |
| `markets_to_children` | `tri_state` | Protección reforzada. |
| `warranty_or_after_sales` | `tri_state` | Idoneidad y garantías ofrecidas. |
| `consumer_complaints_last_12m` | `integer/null` | Riesgo y evidencia. |

### 4.7. Datos personales, tecnología y seguridad

| `field_name` | Tipo | Qué captura |
|---|---|---|
| `processes_personal_data` | `tri_state` | Cualquier tratamiento, incluso sin banco formal. |
| `data_subject_categories` | `set` | Clientes, empleados, postulantes, proveedores, usuarios, visitantes. |
| `personal_data_categories` | `set` | Identificación, contacto, financiero, geolocalización, biométrico, salud, etc. |
| `processes_sensitive_data` | `tri_state` | Datos sensibles. |
| `processes_children_data` | `tri_state` | Menores. |
| `large_scale_or_many_subjects` | `tri_state` | Criterio cualitativo del reglamento. |
| `high_harm_if_breached` | `tri_state` | Riesgo evidente para derechos/libertades. |
| `personal_data_banks` | `array<object>` | Nombre, finalidad, registro y actualización. |
| `privacy_notices_status` | `enum` | Información al titular. |
| `consent_records_status` | `enum` | Prueba de consentimiento cuando corresponde. |
| `data_subject_rights_channel` | `enum` | Acceso, rectificación, cancelación, oposición y otros. |
| `data_processors` | `array<object>` | Encargados, contrato, subencargados. |
| `cloud_providers_and_countries` | `array<object>` | Destino y acceso remoto. |
| `cross_border_data_flows` | `array<object>` | País, receptor, base y registro. |
| `data_retention_schedule_status` | `enum` | Plazos y eliminación. |
| `security_document_status` | `enum` | Medidas organizativas/técnicas. |
| `incident_response_plan_status` | `enum` | Detección, evaluación y 48 h condicionales. |
| `personal_data_incidents_last_24m` | `integer` | Registro y notificación. |
| `data_officer_status` | `enum` | Designado, en evaluación, no. |
| `uses_profiling_or_automated_decisions` | `tri_state` | Transparencia/riesgo. |
| `uses_cookies_or_adtech` | `tri_state` | Datos y marketing. |
| `has_cctv_or_biometrics` | `tri_state` | Base, aviso, proporcionalidad y seguridad. |

### 4.8. Ambiente, recursos, activos y operaciones especiales

| `field_name` | Tipo | Qué captura |
|---|---|---|
| `project_subject_to_seia` | `tri_state` | Proyecto incluido o con impacto significativo. |
| `environmental_instrument_type` | `text/null` | DIA, EIA-sd, EIA-d, ITS, PAMA u otro. |
| `environmental_instrument_status` | `enum` | Aprobado, trámite, no, desconocido. |
| `environmental_commitments` | `array<object>` | Medidas, reportes, monitoreos. |
| `generates_hazardous_waste` | `tri_state` | Residuos peligrosos. |
| `sigersol_obligation_status` | `enum` | Obligado/cumplido/pendiente/revisión. |
| `uses_waste_operator` | `tri_state` | EO-RS y manifiestos. |
| `has_emissions_effluents_or_discharges` | `tri_state` | Permisos/monitoreos. |
| `extracts_or_uses_water_resource` | `tri_state` | Títulos ANA. |
| `operates_near_protected_or_sensitive_area` | `tri_state` | Opiniones y restricciones. |
| `environmental_emergencies_last_24m` | `integer` | Reporte y remediación. |
| `owns_real_estate` | `tri_state` | Predial/arbitrios. |
| `real_estate_assets` | `array<object>` | Ubigeo, uso, titularidad, autoavalúo. |
| `owns_or_operates_vehicles` | `tri_state` | SOAT/CAT, CITV, tributo y habilitación. |
| `vehicles` | `array<object>` | Placa, clase, uso, año, titular, coberturas. |
| `transports_people_or_goods` | `tri_state` | Autorización MTC/GORE y SUTRAN. |
| `uses_controlled_chemicals` | `tri_state` | IQBF/SUCAMEC/sector. |
| `uses_arms_explosives_or_pyrotechnics` | `tri_state` | SUCAMEC. |

### 4.9. Comercio exterior, Estado y contratos privados

| `field_name` | Tipo | Qué captura |
|---|---|---|
| `imports_goods` | `tri_state` | Operaciones de importación. |
| `exports_goods` | `tri_state` | Operaciones de exportación. |
| `exports_services` | `tri_state` | Reglas IGV y prueba de uso exterior. |
| `customs_regimes_used` | `set` | Importación, exportación, temporal, drawback, etc. |
| `restricted_goods_subheadings` | `array` | Subpartidas y documento de control. |
| `uses_customs_agent` | `tri_state` | Despacho y mandato. |
| `is_customs_trade_operator` | `tri_state` | Autorización OCE. |
| `origin_certificates_used` | `tri_state` | Preferencias y trazabilidad. |
| `contracts_with_state` | `tri_state` | Proveedor público. |
| `rnp_categories` | `set` | Bienes, servicios, consultoría/ejecución de obras. |
| `rnp_status` | `enum` | Vigente, vencido, no inscrito, no requerido. |
| `seace_user_status` | `enum` | Acceso operativo. |
| `public_procurement_impediment` | `tri_state` | Impedimento o vínculo relevante. |
| `public_contracts` | `array<object>` | Entidad, objeto, hitos, garantías y penalidades. |
| `private_material_contracts` | `array<object>` | Clientes, proveedores, alquileres, créditos, licencias. |
| `loan_covenants` | `array<object>` | Ratios, reportes, garantías. |
| `insurance_policies` | `array<object>` | Cobertura, límite, vencimiento y exclusiones. |
| `certifications_required_by_contract` | `array<object>` | ISO, HACCP, homologación u otra. |
| `intellectual_property_assets` | `array<object>` | Marcas, software, patentes, diseños, secretos. |
| `ip_assignment_contracts_status` | `enum` | Cesiones de empleados/proveedores. |

## 5. Enumeraciones canónicas

```yaml
tri_state: [true, false, null]
taxpayer_type:
  - NATURAL_PERSON_BUSINESS
  - LEGAL_ENTITY
  - FOREIGN_BRANCH
  - CONSORTIUM
  - OTHER_ENTITY
legal_form:
  - NATURAL_PERSON_BUSINESS
  - EIRL
  - SAC
  - SA
  - SAA
  - SRL
  - SACS
  - FOREIGN_BRANCH
  - COOPERATIVE
  - ASSOCIATION
  - FOUNDATION
  - CONSORTIUM
  - OTHER
tax_regime:
  - NRUS
  - RER
  - RMT
  - GENERAL
  - SPECIAL_SECTOR
  - EXEMPT_OR_UNAFFECTED
  - UNKNOWN
remype_status:
  - NOT_REGISTERED
  - MICRO
  - SMALL
  - UNKNOWN
labor_regime:
  - PRIVATE_GENERAL
  - MICRO_REMYPE
  - SMALL_REMYPE
  - AGRARIAN
  - CIVIL_CONSTRUCTION
  - MINING
  - FISHERY
  - PORT
  - OTHER_SPECIAL
  - UNKNOWN
duty_nature:
  - STATUTORY
  - PERMIT_CONDITION
  - CONTRACTUAL
  - VOLUNTARY_RISK_CONTROL
  - GROWTH_ENABLER
match_status:
  - APPLIES
  - DOES_NOT_APPLY
  - UNKNOWN
  - REVIEW_REQUIRED
  - RECOMMENDED
scope_type:
  - COMPANY
  - ESTABLISHMENT
  - WORKER
  - PERSON
  - PRODUCT
  - PROJECT
  - VEHICLE
  - REAL_ESTATE
  - TRANSACTION
  - SHIPMENT
  - CONTRACT
  - DATA_BANK
  - DATA_FLOW
  - DIGITAL_SYSTEM
  - CHANNEL
  - CAMPAIGN
  - INCIDENT
  - CASE_OR_PROCEDURE
  - COUNTERPARTY
  - AUTHORIZATION
risk_level: [LOW, MEDIUM, HIGH, CRITICAL]
answer_status:
  - DECLARED
  - EVIDENCE_VERIFIED
  - EXTERNAL_SOURCE_VERIFIED
  - UNKNOWN
  - DISPUTED
  - EXPIRED
frequency_type:
  - ONE_TIME
  - EVENT_DRIVEN
  - MONTHLY
  - QUARTERLY
  - SEMIANNUAL
  - ANNUAL
  - CONTINUOUS
  - PER_TRANSACTION
  - BEFORE_OPERATION
  - BEFORE_EXPIRY
```

## 6. Parámetros versionados: no codificarlos dentro de las reglas

| `parameter_code` | Valor vigente al corte | Operador/límite | `valid_from` | Uso | Fuente |
|---|---:|---|---:|---|---|
| `UIT_PEN` | 5,500 | referencia | 2026-01-01 | Umbrales tributarios/laborales/sanciones. | `SRC-TAX-UIT` |
| `RMV_GENERAL_PEN` | 1,130 | `gte` cuando aplica | 2025-01-01 | Remuneración mínima general; revisar regímenes especiales. | `SRC-LAB-RMV` |
| `BANKARIZATION_PEN` | 2,000 | `gte` | vigente al corte | Medio de pago obligatorio. | `SRC-TAX-BANK` |
| `BANKARIZATION_USD` | 500 | `gte` | vigente al corte | Medio de pago obligatorio. | `SRC-TAX-BANK` |
| `NRUS_ANNUAL_REVENUE_PEN_MAX` | 96,000 | `lte` | vigente al corte | Elegibilidad NRUS. | `SRC-TAX-NRUS` |
| `NRUS_ANNUAL_PURCHASES_PEN_MAX` | 96,000 | `lte` | vigente al corte | Elegibilidad NRUS. | `SRC-TAX-NRUS` |
| `NRUS_MONTHLY_REVENUE_PEN_MAX` | 8,000 | `lte` | vigente al corte | Elegibilidad NRUS. | `SRC-TAX-NRUS` |
| `NRUS_MONTHLY_PURCHASES_PEN_MAX` | 8,000 | `lte` | vigente al corte | Elegibilidad NRUS. | `SRC-TAX-NRUS` |
| `NRUS_FIXED_ASSETS_PEN_MAX` | 70,000 | `lte` | vigente al corte | Excluye predios y vehículos según regla. | `SRC-TAX-NRUS` |
| `RER_ANNUAL_REVENUE_PEN_MAX` | 525,000 | `lte` | vigente al corte | Elegibilidad RER. | `SRC-TAX-RER` |
| `RER_ANNUAL_PURCHASES_PEN_MAX` | 525,000 | `lte` | vigente al corte | Elegibilidad RER. | `SRC-TAX-RER` |
| `RER_FIXED_ASSETS_PEN_MAX` | 126,000 | `lte` | vigente al corte | Excluye predios y vehículos. | `SRC-TAX-RER` |
| `RER_WORKERS_PER_SHIFT_MAX` | 10 | `lte` | vigente al corte | Personal afectado a la actividad por turno. | `SRC-TAX-RER` |
| `RMT_ANNUAL_REVENUE_UIT_MAX` | 1,700 | `lte` | vigente al corte | Elegibilidad RMT. | `SRC-TAX-RMT` |
| `ITAN_NET_ASSETS_PEN_TRIGGER` | 1,000,000 | `gt` | vigente al corte | ITAN, con demás condiciones. | `SRC-TAX-ITAN` |
| `SST_COMMITTEE_WORKERS_MIN` | 20 | `gte` | vigente al corte | Comité; por debajo, supervisor. | `SRC-SST-COMMITTEE` |
| `RISST_WORKERS_MIN` | 20 | `gte` | vigente al corte | Reglamento Interno de SST. | `SRC-SST-LAW` |
| `WORK_RULES_WORKERS_TRIGGER` | 100 | `gt` | vigente al corte | RIT cuando se ocupa más de 100 trabajadores. | `SRC-LAB-RIT` |
| `PROFIT_SHARE_WORKERS_TRIGGER` | 20 | `gt` | vigente al corte | Más de 20 y demás requisitos/exclusiones. | `SRC-LAB-PROFIT` |
| `DISABILITY_QUOTA_WORKERS_TRIGGER` | 50 | `gt` | vigente al corte | Más de 50, promedio anual. | `SRC-LAB-DISABILITY` |
| `DISABILITY_QUOTA_PRIVATE_PERCENT` | 3 | porcentaje | vigente al corte | Cuota del empleador privado alcanzado. | `SRC-LAB-DISABILITY` |
| `LACTATION_ROOM_WOMEN_15_49_MIN` | 20 | `gte` | vigente al corte | Por centro de trabajo. | `SRC-LAB-LACTATION` |
| `DATA_INCIDENT_NOTIFY_HOURS` | 48 | plazo máximo | 2025-03-31 | Solo en supuestos reglamentarios; documentar todo incidente. | `SRC-DPA-REG` |

`valid_from: vigente al corte` significa que la vigencia fue confirmada para esta edición, pero la fecha histórica de inicio aún debe ser completada desde la norma base antes de importar el registro a producción. No se debe convertir esa frase en una fecha ficticia.

### 6.1. Cronograma 2026 del beneficiario final que debe cargarse como datos

Para los tramos publicados sobre ingresos netos de 2024:

| `schedule_code` | Condición | Periodo de presentación | Estado al 2026-08-24 |
|---|---|---|---|
| `BF_2026_T3` | Más de 25 UIT hasta 50 UIT | 2026-07 | periodo transcurrido |
| `BF_2026_T4` | Más de 10 UIT hasta 25 UIT | 2026-09 | próximo |
| `BF_2026_T5` | Hasta 10 UIT | 2026-11 | próximo |

Fuente: `SRC-TAX-BENEFICIAL-2026`. El día exacto se resuelve con el cronograma mensual por último dígito del RUC y categoría del contribuyente; no debe almacenarse como una fecha universal.

## 7. Modelo relacional recomendado

### 7.1. Tablas de perfil

| Tabla | Finalidad | Claves y columnas principales |
|---|---|---|
| `companies` | Identidad estable. | `id`, `ruc`, `legal_name`, `legal_form`, `country_code`, `created_at` |
| `company_profiles` | Foto versionada de hechos. | `id`, `company_id`, `as_of_date`, `valid_from`, `valid_to`, `status`, `declared_by` |
| `company_legal_parties` | Accionistas, beneficiarios, directores, representantes. | `profile_id`, `party_type`, `person_id`, `ownership_pct`, `control_basis`, `power_expiry` |
| `company_tax_profiles` | Régimen, padrones, magnitudes y estado SUNAT. | `profile_id`, `tax_regime`, `ruc_status`, `domicile_condition`, `revenue_pen`, `revenue_uit`, `net_assets_pen_previous_year_end`, flags |
| `company_activities` | Actividades reales, CIIU y roles. | `id`, `profile_id`, `ciiu_code`, `description_plain`, `activity_role`, `is_current`, `is_primary` |
| `company_activity_flags` | Señales sectoriales normalizadas. | `activity_id`, `flag_code`, `value`, `evidence_id` |
| `company_establishments` | Locales y jurisdicción. | `id`, `profile_id`, `ubigeo`, `municipality_code`, `type`, `risk_level`, flags |
| `company_workforce_snapshots` | Umbrales laborales por fecha. | `id`, `profile_id`, `as_of_date`, conteos globales |
| `establishment_workforce_snapshots` | Umbrales por centro. | `establishment_id`, `as_of_date`, `workers`, `women_age_15_49`, `third_party_workers` |
| `company_data_profiles` | Tratamiento de datos. | `profile_id`, flags de sensibilidad, escala, menores, flujos, DPO |
| `company_data_banks` | Bancos de datos. | `id`, `profile_id`, `name`, `purpose`, `registry_code`, `status` |
| `company_assets` | Predios, vehículos y otros activos regulados. | `id`, `profile_id`, `asset_type`, `identifier`, `location`, `status` |
| `company_authorizations` | Títulos habilitantes. | `id`, `profile_id`, `authority_id`, `type`, `number`, `scope_type`, `scope_id`, `expires_at`, `status` |
| `company_contracts` | Contratos que crean obligaciones. | `id`, `profile_id`, `contract_type`, `counterparty`, `valid_from`, `valid_to`, `risk_level` |

### 7.2. Tablas del conocimiento normativo

| Tabla | Finalidad | Columnas principales |
|---|---|---|
| `authorities` | Entidades públicas o privadas receptoras/supervisoras. | `id`, `code`, `name`, `authority_type`, `jurisdiction_level`, `url` |
| `legal_sources` | Fuente oficial versionada. | `id`, `source_code`, `title`, `url`, `issuer`, `publication_date`, `effective_from`, `effective_to`, `retrieved_at`, `content_hash` |
| `responsibilities` | Concepto estable de responsabilidad. | `id`, `code`, `name`, `domain`, `duty_nature`, `scope_type`, `risk_level`, `active` |
| `responsibility_versions` | Texto operativo por vigencia. | `id`, `responsibility_id`, `version`, `valid_from`, `valid_to`, `description`, `authority_id`, `frequency_type`, `deadline_formula` |
| `responsibility_sources` | Relación N:M con sustento. | `responsibility_version_id`, `legal_source_id`, `source_role`, `article_or_section`, `notes` |
| `responsibility_rules` | Regla ejecutable. | `id`, `responsibility_version_id`, `priority`, `expression_json`, `missing_data_policy`, `human_review_policy` |
| `rule_required_facts` | Dependencias y preguntas faltantes. | `rule_id`, `field_path`, `required_for_status`, `question_code` |
| `responsibility_evidence_types` | Evidencias esperadas. | `responsibility_version_id`, `evidence_code`, `name`, `mandatory`, `retention_formula` |
| `parameter_definitions` | Nombre y unidad del parámetro. | `id`, `code`, `data_type`, `unit` |
| `parameter_values` | Valor por periodo y jurisdicción. | `parameter_id`, `value_json`, `valid_from`, `valid_to`, `legal_source_id` |
| `sector_routes` | Puerta de revisión sectorial. | `code`, `flag_code`, `authority_id`, `initial_check`, `output_policy` |
| `calendar_schedules` | Cronogramas externos. | `code`, `year`, `category`, `ruc_digit`, `period`, `due_date`, `legal_source_id` |

### 7.3. Tablas de ejecución y auditoría

| Tabla | Finalidad | Columnas principales |
|---|---|---|
| `questionnaire_versions` | Congelar el formulario aplicado. | `id`, `version`, `valid_from`, `valid_to` |
| `question_definitions` | Preguntas y mapeo a hechos. | `id`, `questionnaire_version_id`, `code`, `field_path`, `type`, `required_expression` |
| `company_answers` | Respuesta original. | `id`, `company_profile_id`, `question_id`, `value_json`, `answer_status`, `as_of_date`, `evidence_id` |
| `match_runs` | Una evaluación reproducible. | `id`, `company_profile_id`, `ruleset_version`, `started_at`, `completed_at`, `engine_version` |
| `company_responsibilities` | Resultado explicado. | `id`, `match_run_id`, `responsibility_version_id`, `scope_type`, `scope_id`, `match_status`, `explanation`, `trigger_snapshot_json` |
| `responsibility_occurrences` | Instancia calendarizable. | `id`, `company_responsibility_id`, `period_start`, `period_end`, `due_at`, `status`, `amount_json` |
| `evidence_documents` | Metadatos de archivo/evidencia. | `id`, `company_id`, `document_type`, `issued_at`, `expires_at`, `hash`, `uri`, `verification_status` |
| `responsibility_evidence` | Evidencia contra ocurrencia. | `occurrence_id`, `evidence_document_id`, `evidence_code`, `accepted_at`, `reviewer_id` |
| `compliance_assessments` | Revisión y hallazgos. | `id`, `occurrence_id`, `assessment_status`, `finding`, `risk`, `remediation_due_at` |
| `rule_review_queue` | Casos `UNKNOWN`/`REVIEW_REQUIRED`. | `id`, `company_responsibility_id`, `reason_code`, `assigned_to`, `status` |
| `audit_events` | Trazabilidad inmutable. | `id`, `entity_type`, `entity_id`, `event_type`, `actor`, `occurred_at`, `before_json`, `after_json` |

### 7.4. DDL mínimo ilustrativo (PostgreSQL)

```sql
create type match_status as enum (
  'APPLIES', 'DOES_NOT_APPLY', 'UNKNOWN',
  'REVIEW_REQUIRED', 'RECOMMENDED'
);

create table responsibilities (
  id uuid primary key,
  code text not null unique,
  name text not null,
  domain text not null,
  duty_nature text not null,
  scope_type text not null,
  risk_level text not null,
  active boolean not null default true
);

create table responsibility_versions (
  id uuid primary key,
  responsibility_id uuid not null references responsibilities(id),
  version integer not null,
  valid_from date not null,
  valid_to date,
  description text not null,
  authority_id uuid,
  frequency_type text not null,
  deadline_formula jsonb,
  unique (responsibility_id, version),
  exclude using gist (
    responsibility_id with =,
    daterange(valid_from, coalesce(valid_to, 'infinity'::date), '[]') with &&
  )
);

create table responsibility_rules (
  id uuid primary key,
  responsibility_version_id uuid not null references responsibility_versions(id),
  priority integer not null default 100,
  expression_json jsonb not null,
  missing_data_policy text not null default 'UNKNOWN',
  human_review_policy text not null default 'NONE'
);

create table match_runs (
  id uuid primary key,
  company_profile_id uuid not null,
  ruleset_version text not null,
  engine_version text not null,
  started_at timestamptz not null,
  completed_at timestamptz
);

create table company_responsibilities (
  id uuid primary key,
  match_run_id uuid not null references match_runs(id),
  responsibility_version_id uuid not null references responsibility_versions(id),
  scope_type text not null,
  scope_id uuid,
  match_status match_status not null,
  explanation text not null,
  trigger_snapshot_json jsonb not null,
  unique nulls not distinct (
    match_run_id, responsibility_version_id, scope_type, scope_id
  )
);

create index company_responsibilities_status_idx
  on company_responsibilities(match_run_id, match_status);
```

El `exclude` requiere la extensión `btree_gist` y `UNIQUE NULLS NOT DISTINCT` requiere PostgreSQL 15 o superior. En versiones anteriores debe usarse un índice por expresión; si no se usa PostgreSQL, la aplicación debe impedir vigencias superpuestas y resultados duplicados mediante una garantía equivalente.

## 8. Lenguaje y semántica de reglas

### 8.1. Operadores mínimos

```yaml
logical: [all, any, not]
comparison: [eq, neq, gt, gte, lt, lte, in, not_in]
collection: [contains, overlaps, count, any_item, all_items]
temporal: [on_date, before, after, during, start_of_year]
existence: [known, unknown, exists]
derived: [parameter, sum, average, divide]
```

### 8.2. Política de datos faltantes

Una comparación con `null` no es `false`; es `UNKNOWN`. El evaluador debe usar lógica ternaria:

| Expresión | Resultado |
|---|---|
| `true AND unknown` | `unknown` |
| `false AND unknown` | `false` |
| `true OR unknown` | `true` |
| `false OR unknown` | `unknown` |
| `NOT unknown` | `unknown` |

Solo se emite `DOES_NOT_APPLY` si la expresión queda inequívocamente en `false` con hechos conocidos.

### 8.3. Ejemplos ejecutables

#### Planilla electrónica

```json
{
  "any": [
    {"gt": [{"fact": "workforce.worker_count_total"}, 0]},
    {"gt": [{"fact": "workforce.service_provider_count"}, 0]},
    {"gt": [{"fact": "workforce.training_modality_count"}, 0]},
    {"eq": [{"fact": "workforce.must_withhold_fourth_or_fifth_income"}, true]}
  ]
}
```

#### Comité de SST

```json
{
  "gte": [
    {"fact": "workforce.worker_count_total"},
    {"parameter": "SST_COMMITTEE_WORKERS_MIN", "on": {"fact": "profile.as_of_date"}}
  ]
}
```

#### Supervisor de SST

```json
{
  "all": [
    {"gt": [{"fact": "workforce.worker_count_total"}, 0]},
    {"lt": [
      {"fact": "workforce.worker_count_total"},
      {"parameter": "SST_COMMITTEE_WORKERS_MIN", "on": {"fact": "profile.as_of_date"}}
    ]}
  ]
}
```

#### Lactario por establecimiento

```json
{
  "gte": [
    {"fact": "establishment.women_age_15_49_at_site"},
    {"parameter": "LACTATION_ROOM_WOMEN_15_49_MIN", "on": {"fact": "profile.as_of_date"}}
  ]
}
```

#### ITAN

```json
{
  "all": [
    {"in": [{"fact": "tax.tax_regime"}, ["RMT", "GENERAL"]]},
    {"before": [{"fact": "company.operations_start_date"}, {"start_of_year": {"fact": "profile.as_of_date"}}]},
    {"gt": [
      {"fact": "tax.net_assets_pen_previous_year_end"},
      {"parameter": "ITAN_NET_ASSETS_PEN_TRIGGER", "on": {"fact": "profile.as_of_date"}}
    ]}
  ]
}
```

#### Libro de Reclamaciones

```json
{
  "all": [
    {"eq": [{"fact": "consumer.sells_to_end_consumers"}, true]},
    {"eq": [{"fact": "company.operates_in_peru"}, true]}
  ]
}
```

#### Ruta sectorial, no conclusión automática

```json
{
  "if": {
    "contains": [
      {"fact": "activities.regulated_activity_flags"},
      "HEALTH_SERVICE_PROVIDER"
    ]
  },
  "then": {
    "status": "REVIEW_REQUIRED",
    "route": "SECTOR-HEALTH-IPRESS"
  }
}
```

### 8.4. Explicación almacenada

Cada match debe conservar una explicación entendible y el hecho que lo activó:

```json
{
  "responsibility_code": "LAB-SST-COMMITTEE",
  "match_status": "APPLIES",
  "explanation": "La empresa declaró 27 trabajadores; el umbral vigente para comité es 20.",
  "rule_version": 3,
  "trigger_snapshot": {
    "worker_count_total": 27,
    "threshold": 20,
    "profile_as_of_date": "2026-08-24"
  },
  "source_codes": ["SRC-SST-COMMITTEE"]
}
```

## 9. Algoritmo de evaluación

```pseudo
function run_match(company_profile, ruleset, evaluation_date):
    assert company_profile.as_of_date <= evaluation_date
    facts = normalize(company_profile)
    facts = derive_values(facts, parameters_at(evaluation_date))
    results = []

    for responsibility_version in ruleset.active_on(evaluation_date):
        scopes = expand_scopes(responsibility_version.scope_type, facts)

        for scope in scopes:
            required = responsibility_version.required_facts(scope)
            missing = required.where(fact_is_unknown)

            if missing is not empty:
                results.add(UNKNOWN, missing_questions=missing.question_codes)
                continue

            value = evaluate_ternary(responsibility_version.rule, facts, scope)

            if responsibility_version.requires_human_review(facts, scope):
                status = REVIEW_REQUIRED
            else if responsibility_version.duty_nature in
                    [VOLUNTARY_RISK_CONTROL, GROWTH_ENABLER]:
                status = RECOMMENDED if value == true else DOES_NOT_APPLY
            else:
                status = APPLIES if value == true else DOES_NOT_APPLY

            explanation = explain(rule, facts_used, parameters_used, sources)
            results.add(status, explanation, immutable_trigger_snapshot)

    create_occurrences(results.where(status in [APPLIES, RECOMMENDED]))
    enqueue_review(results.where(status in [UNKNOWN, REVIEW_REQUIRED]))
    return immutable_match_run(results)
```

### 9.1. Precedencia

1. Excepción legal expresa y vigente.
2. Regla especial sectorial.
3. Regla general.
4. Condición contractual adicional.
5. Control voluntario.

Las capas no deben borrar otras obligaciones. Por ejemplo, una autorización sanitaria no elimina licencia municipal, planilla, impuestos, datos o consumidor.

## 10. Catálogo maestro de responsabilidades

Convenciones:

- `C`: compañía; `E`: establecimiento; `W`: trabajador; `P`: producto/proyecto; `T`: transacción; `V`: vehículo; `DB`: banco de datos; `A`: autorización.
- “Evento” significa al ocurrir el alta, cambio, operación, incidente o vencimiento.
- La evidencia indicada es mínima; la norma, fiscalización o contrato puede exigir más.
- Las condiciones resumidas son activadores de preclasificación. Si la fila dice “revisión”, no debe resolverse sin validar la norma especial.
- Cada fila tiene una sola naturaleza primaria para facilitar la carga. Si el mismo resultado nace también de un contrato o permiso, se crea otra responsabilidad vinculada en vez de guardar dos valores en un campo escalar.

### 10.1. Constitución, RUC y gobierno corporativo

| Código | Responsabilidad | Naturaleza | Activador resumido | Alcance / ciclo | Evidencia mínima | Fuentes |
|---|---|---|---|---|---|---|
| `LEG-001` | Inscribirse y mantener RUC activo con información veraz. | `STATUTORY` | Realiza actividad empresarial o supuesto sujeto a RUC. | C / continua | ficha RUC | `SRC-RUC-REGISTER` |
| `LEG-002` | Actualizar domicilio fiscal, actividades, establecimientos, representantes y demás datos del RUC. | `STATUTORY` | Cambio de un dato registrable. | C/E / evento | constancia de modificación | `SRC-RUC-UPDATE` |
| `LEG-003` | Registrar todos los establecimientos anexos. | `STATUTORY` | Opera local adicional al domicilio fiscal. | E / antes o al cambio | ficha RUC por local | `SRC-RUC-UPDATE` |
| `LEG-004` | Mantener CIIU principal y secundarios coherentes con actividades reales. | `STATUTORY` | Inicio, cese o cambio de actividad. | C / evento | ficha RUC + inventario de actividades | `SRC-RUC-UPDATE`, `SRC-RUC-CIIU` |
| `LEG-005` | Llevar y legalizar libros societarios aplicables antes de su uso. | `STATUTORY` | Persona jurídica/EIRL según forma: actas, directorio, matrícula de acciones u otros. | C / continua | apertura/legalización + hojas/actas | `SRC-BOOKS-RULE`, `SRC-LGS` |
| `LEG-006` | Celebrar junta obligatoria anual y pronunciarse sobre gestión, resultados y utilidades. | `STATUTORY` | Sociedad comprendida; cierre de ejercicio. | C / anual dentro de 3 meses del cierre | convocatoria, acta, EE. FF. aprobados | `SRC-LGS` |
| `LEG-007` | Formular, someter y conservar estados financieros y documentación societaria. | `STATUTORY` | Sociedad/empresa con ejercicio cerrado. | C / anual | EE. FF., memoria cuando corresponda, acta | `SRC-LGS` |
| `LEG-008` | Inscribir actos societarios registrables. | `STATUTORY` | Cambio de estatuto, capital, gerente, poderes, reorganización u otro acto inscribible. | C / evento | asiento SUNARP, escritura/parte | `SRC-SUNARP-SOCIETIES` |
| `LEG-009` | Mantener poderes suficientes y vigentes para trámites y actos registrales. | `STATUTORY` | Actuación por representante. | C / continua | vigencia de poder | `SRC-SUNARP-REPRESENTATIVES` |
| `LEG-010` | Identificar y declarar beneficiario final cuando ingrese al cronograma o supuesto aplicable. | `STATUTORY` | Persona/ente obligado según reglas y cronograma SUNAT. | C / evento/cronograma | formulario y constancia | `SRC-TAX-BENEFICIAL`, `SRC-TAX-BENEFICIAL-2026` |
| `LEG-011` | Actualizar/conservar sustento del beneficiario final y cadena de control. | `STATUTORY` | Cambio o requerimiento; revisión periódica. | C / continua/evento | organigrama, declaraciones, documentos de control | `SRC-TAX-BENEFICIAL` |
| `LEG-012` | Conservar libros, registros, documentación y antecedentes durante los plazos aplicables. | `STATUTORY` | Genera documentación societaria/tributaria. | C / continua | política de retención + repositorio | `SRC-TAX-RETENTION`, `SRC-LGS` |
| `LEG-013` | Comunicar pérdida o destrucción de libros/registros tributarios y reconstruirlos. | `STATUTORY` | Pérdida o destrucción. | C / evento | comunicación a SUNAT + plan de reconstrucción | `SRC-TAX-RETENTION` |
| `LEG-014` | Revisar extinción, liquidación o suspensión formal en vez de abandonar operaciones. | `STATUTORY` | Cese temporal o definitivo. | C / evento | acuerdos, baja RUC, asientos y cierres | `SRC-RUC-UPDATE`, `SRC-SUNARP-SOCIETIES` |

### 10.2. Tributación, contabilidad y comprobantes

| Código | Responsabilidad | Naturaleza | Activador resumido | Alcance / ciclo | Evidencia mínima | Fuentes |
|---|---|---|---|---|---|---|
| `TAX-001` | Estar en un régimen tributario permitido por forma, actividad, ingresos, compras, activos y locales. | `STATUTORY` | Toda empresa/contribuyente. | C / continua | ficha RUC + prueba de elegibilidad | `SRC-TAX-REGIMES`, `SRC-TAX-NRUS`, `SRC-TAX-RER`, `SRC-TAX-RMT`, `SRC-TAX-RG` |
| `TAX-002` | Pagar cuota NRUS y respetar límites/restricciones. | `STATUTORY` | Persona natural en NRUS elegible. | C / mensual | constancia de pago + ventas/compras | `SRC-TAX-NRUS` |
| `TAX-003` | Declarar/pagar IGV y renta mensual en RER. | `STATUTORY` | Régimen RER. | C / mensual según RUC | FV/PDT y constancia | `SRC-TAX-RER`, `SRC-TAX-CALENDAR-2026` |
| `TAX-004` | Declarar/pagar IGV y pago a cuenta mensual en RMT. | `STATUTORY` | Régimen RMT. | C / mensual según RUC | FV/PDT y constancia | `SRC-TAX-RMT`, `SRC-TAX-CALENDAR-2026` |
| `TAX-005` | Declarar/pagar IGV y pago a cuenta mensual en Régimen General. | `STATUTORY` | Régimen General. | C / mensual según RUC | FV/PDT y constancia | `SRC-TAX-RG`, `SRC-TAX-CALENDAR-2026` |
| `TAX-006` | Presentar declaración jurada anual de renta empresarial. | `STATUTORY` | RMT/Régimen General y demás supuestos SUNAT. | C / anual según cronograma | DJ anual + papeles de trabajo | `SRC-TAX-ANNUAL-WHO`, `SRC-TAX-ANNUAL-2025` |
| `TAX-007` | Llevar libros y registros contables que correspondan al régimen e ingresos. | `STATUTORY` | Según régimen/umbral/actividad. | C / continua | libros legalizados/electrónicos | `SRC-BOOKS-RULE`, `SRC-TAX-RMT`, `SRC-TAX-RG` |
| `TAX-008` | Incorporarse y operar RVIE/RCE mediante SIRE cuando corresponda. | `STATUTORY` | Fecha de incorporación por régimen/categoría/padrón. | C / mensual | constancias RVIE/RCE | `SRC-TAX-SIRE`, `SRC-TAX-SIRE-CALENDAR` |
| `TAX-009` | Emitir comprobantes de pago electrónicos válidos. | `STATUTORY` | Sujeto obligado; en general RER/RMT/RG desde inscripción y designaciones. | T / por operación | XML/CDR/representación | `SRC-TAX-CPE` |
| `TAX-010` | Entregar boleta/ticket y no factura cuando NRUS solo puede emitir esos comprobantes. | `STATUTORY` | NRUS. | T / por operación | comprobante | `SRC-TAX-NRUS`, `SRC-TAX-CPE` |
| `TAX-011` | Emitir guía de remisión electrónica y sustentar traslado cuando corresponda. | `STATUTORY` | Remitente/transportista/traslado alcanzado. | T/V / por traslado | GRE y documentos de transporte | `SRC-TAX-GRE` |
| `TAX-012` | Aplicar detracción SPOT, depositarla y usar cuenta conforme a norma. | `STATUTORY` | Bien, servicio o contrato de construcción sujeto. | T / por operación | constancia de depósito + comprobante | `SRC-TAX-SPOT` |
| `TAX-013` | Efectuar/soportar retención o percepción de IGV según designación y operación. | `STATUTORY` | Agente designado u operación comprendida. | T / por operación/mensual | certificados, registros, DJ | `SRC-TAX-WITHHOLD-PERCEPTION` |
| `TAX-014` | Usar medio de pago financiero desde el umbral legal y pagar al acreedor o tercero comunicado. | `STATUTORY` | Pago desde S/2,000 o US$500, con reglas/excepciones. | T / por pago | voucher/estado bancario + factura | `SRC-TAX-BANK` |
| `TAX-015` | Determinar, declarar y pagar ITAN. | `STATUTORY` | RMT/RG, inicio anterior al 1 de enero y activos netos sobre S/1 millón, salvo exclusión. | C / anual | DJ ITAN + balance | `SRC-TAX-ITAN`, `SRC-TAX-ITAN-CALENDAR` |
| `TAX-016` | Presentar DAOT cuando corresponda y reportar terceros sobre el umbral del ejercicio. | `STATUTORY` | Supuestos anuales publicados por SUNAT; regla debe versionarse. | C/T / anual | DAOT + conciliación | `SRC-TAX-DAOT` |
| `TAX-017` | Evaluar y presentar Reporte Local de precios de transferencia. | `STATUTORY` | Operaciones vinculadas/no cooperantes/preferenciales y umbrales vigentes. | C / anual | DJ informativa + estudio | `SRC-TAX-TP-LOCAL` |
| `TAX-018` | Evaluar y presentar Reporte Maestro. | `STATUTORY` | Grupo y umbrales vigentes. | C / anual | reporte y constancia | `SRC-TAX-TP-MASTER` |
| `TAX-019` | Evaluar Reporte País por País. | `STATUTORY` | Grupo multinacional y supuestos/umbrales. | C / anual | reporte/comunicación | `SRC-TAX-CBC` |
| `TAX-020` | Retener, declarar y pagar impuesto por rentas de no domiciliados cuando corresponda. | `STATUTORY` | Pago/acreditación a no domiciliado; tipo de renta, CDI y tasa aplicable. | T / evento/mensual | contrato, certificado residencia, retención | `SRC-TAX-NONRESIDENT` |
| `TAX-021` | Retener y declarar rentas de cuarta y quinta categoría. | `STATUTORY` | Pago sujeto a retención. | W/T / mensual | PLAME, recibos, certificados | `SRC-TAX-WITHHOLD-INCOME`, `SRC-LAB-PLANILLA` |
| `TAX-022` | Verificar derecho a crédito fiscal y gasto/costo con comprobante, causalidad y registro. | `STATUTORY` | Usa crédito/deducción. | T / continua | comprobante, pago, contrato, recepción | `SRC-TAX-CREDIT` |
| `TAX-023` | Conciliar buzón y notificaciones SUNAT y atender requerimientos. | `STATUTORY` | Contribuyente con Clave SOL. | C / continua | bitácora y cargos | `SRC-TAX-NOTIFICATIONS` |
| `TAX-024` | Pagar contribución SENATI y DJ anual cuando sea contribuyente. | `STATUTORY` | Actividad industrial comprendida y condiciones aplicables. | C / mensual/anual | pagos + DJ anual | `SRC-TAX-SENATI-RULE` |
| `TAX-025` | Pagar contribución SENCICO y presentar DJ anual. | `STATUTORY` | Persona que desarrolla actividad/contrato de construcción alcanzado. | C/T / mensual/anual | pagos + DJ anual | `SRC-TAX-SENCICO` |
| `TAX-026` | Determinar impuestos sectoriales (ISC, juegos, minería, turismo u otros). | `STATUTORY` | Producto/actividad gravada. | C/T / revisión sectorial | DJ y papeles de trabajo | `SRC-TAX-REGIMES` |
| `TAX-027` | Monitorear el perfil de cumplimiento y remediar las obligaciones subyacentes detectadas. | `VOLUNTARY_RISK_CONTROL` | Contribuyente con calificación/alertas; cada omisión conserva su propia naturaleza legal. | C / continua | reporte y plan de remediación | `SRC-TAX-COMPLIANCE-PROFILE` |
| `TAX-028` | Separar los parámetros anuales (UIT, cronogramas, tasas) de la lógica permanente. | `VOLUNTARY_RISK_CONTROL` | Motor de cumplimiento. | sistema / anual | tabla de parámetros versionada | `SRC-TAX-UIT`, `SRC-TAX-CALENDAR-2026` |

### 10.3. Tributos y obligaciones asociadas a activos

| Código | Responsabilidad | Naturaleza | Activador resumido | Alcance / ciclo | Evidencia mínima | Fuentes |
|---|---|---|---|---|---|---|
| `AST-001` | Declarar y pagar impuesto predial y arbitrios según municipalidad. | `STATUTORY` | Propietario/obligado por predio al 1 de enero. | inmueble / anual | autoavalúo, cuponera, pagos | `SRC-MUN-TAXES` |
| `AST-002` | Declarar cambios que afecten el predio y titularidad. | `STATUTORY` | Adquisición, transferencia, obra o cambio relevante. | inmueble / evento | DJ municipal + título | `SRC-MUN-TAXES` |
| `AST-003` | Declarar y pagar impuesto al patrimonio vehicular durante el periodo legal. | `STATUTORY` | Propietario de vehículo afecto; revisar año/clase y jurisdicción. | V / anual | DJ/pago | `SRC-MUN-VEHICLE-TAX` |
| `AST-004` | Mantener SOAT o CAT vigente. | `STATUTORY` | Todo vehículo automotor que circula. | V / antes de circular/vencimiento | póliza/consulta | `SRC-ASSET-SOAT` |
| `AST-005` | Mantener inspección técnica vehicular vigente cuando corresponda. | `STATUTORY` | Vehículo alcanzado por clase, antigüedad y uso. | V / periódico | certificado CITV | `SRC-ASSET-CITV` |
| `AST-006` | Mantener habilitación/autorización de transporte y documentos de porte. | `PERMIT_CONDITION` | Transporte de personas o mercancías regulado. | C/V/T / continua | autorización, TUC, documentos | `SRC-SECTOR-TRANSPORT` |

### 10.4. Empleo, planilla y derechos laborales

| Código | Responsabilidad | Naturaleza | Activador resumido | Alcance / ciclo | Evidencia mínima | Fuentes |
|---|---|---|---|---|---|---|
| `LAB-001` | Registrar empleador y sujetos en T-Registro dentro de los plazos aplicables. | `STATUTORY` | Uno o más trabajadores, pensionistas, formación, terceros u otros supuestos. | C/W / evento | constancias de alta/modificación/baja | `SRC-LAB-TREG`, `SRC-LAB-PLANILLA` |
| `LAB-002` | Declarar PLAME y pagar aportes/retenciones. | `STATUTORY` | Empleador o supuesto de Planilla Electrónica. | C/W / mensual | PLAME + pagos | `SRC-LAB-PLANILLA`, `SRC-LAB-TREG` |
| `LAB-003` | Entregar constancias de alta/modificación/baja de T-Registro en los plazos. | `STATUTORY` | Registro o cambio del trabajador/prestador comprendido. | W / evento | cargo de entrega | `SRC-LAB-TREG` |
| `LAB-004` | Formalizar por escrito contratos que lo requieren y evitar encubrir subordinación con recibos. | `STATUTORY` | Contrato sujeto a modalidad, tiempo parcial, extranjero, teletrabajo u otro especial. | W / evento | contrato y sustento de modalidad | `SRC-LAB-RIGHTS`, `SRC-LAB-FOREIGN`, `SRC-LAB-TELEWORK` |
| `LAB-005` | Pagar remuneración no inferior a la RMV general aplicable y emitir boleta. | `STATUTORY` | Trabajador sujeto a jornada/ámbito general; revisar régimen especial. | W / mensual | boleta, banco, asistencia | `SRC-LAB-RMV`, `SRC-LAB-OBLIGATIONS` |
| `LAB-006` | Registrar jornada, asistencia y sobretiempo y respetar descansos. | `STATUTORY` | Trabajador sujeto a control de jornada. | W / diaria/mensual | registro de asistencia, autorización y pago | `SRC-LAB-RIGHTS` |
| `LAB-007` | Aportar a EsSalud y declarar derechohabientes. | `STATUTORY` | Trabajador asegurado regular. | W / mensual/evento | PLAME/T-Registro/pago | `SRC-LAB-OBLIGATIONS`, `SRC-LAB-TREG` |
| `LAB-008` | Retener y pagar ONP o AFP sin apropiarse de aportes. | `STATUTORY` | Trabajador afiliado. | W / mensual | PLAME/AFPnet/constancia | `SRC-LAB-PENSION` |
| `LAB-009` | Otorgar descanso vacacional y conservar control. | `STATUTORY` | Trabajador que cumple récord; reglas según régimen. | W / anual | rol, solicitud, boleta | `SRC-LAB-OBLIGATIONS`, `SRC-REMYPE-COMPARE` |
| `LAB-010` | Depositar CTS cuando corresponda. | `STATUTORY` | Trabajador con derecho; régimen general o MYPE según inscripción/fecha. | W / semestral (mayo/noviembre) | liquidación y depósito | `SRC-LAB-CTS`, `SRC-REMYPE-COMPARE` |
| `LAB-011` | Pagar gratificaciones y bonificación extraordinaria cuando corresponda. | `STATUTORY` | Trabajador con derecho; régimen general o MYPE. | W / semestral (julio/diciembre) | liquidación y pago | `SRC-LAB-GRATIFICATION`, `SRC-REMYPE-COMPARE` |
| `LAB-012` | Contratar seguro Vida Ley desde el inicio de la relación laboral. | `STATUTORY` | Trabajador dependiente comprendido en el régimen aplicable. | W / continua | póliza, nómina, declaración de beneficiarios y pago | `SRC-LAB-VIDA-LEY` |
| `LAB-013` | Distribuir utilidades a trabajadores cuando corresponda. | `STATUTORY` | Renta de tercera categoría, más de 20 trabajadores y actividad no excluida; porcentaje sectorial. | C/W / anual | cálculo, liquidaciones, pagos | `SRC-LAB-PROFIT` |
| `LAB-014` | Mantener cuadro de categorías y funciones y política salarial sin discriminación. | `STATUTORY` | Todo empleador privado alcanzado. | C / continua/evento | cuadro, política y cargos de comunicación | `SRC-LAB-EQUAL-PAY` |
| `LAB-015` | Cumplir cuota de empleo de personas con discapacidad. | `STATUTORY` | Empleador privado con más de 50 trabajadores promedio anual. | C / anual/continua | cálculo promedio, planilla, sustento de excepción | `SRC-LAB-DISABILITY` |
| `LAB-016` | Implementar lactario. | `STATUTORY` | Centro de trabajo con 20 o más mujeres de 15 a 49 años. | E / continua | acta, fotos, registro de uso | `SRC-LAB-LACTATION` |
| `LAB-017` | Otorgar permiso por lactancia y demás licencias legales. | `STATUTORY` | Trabajadora/trabajador en supuesto legal. | W / evento | solicitud, control y boleta | `SRC-LAB-LACTATION-LEAVE` |
| `LAB-018` | Elaborar y presentar Reglamento Interno de Trabajo. | `STATUTORY` | Empleador que ocupa más de 100 trabajadores. | C / evento/actualización | resolución/registro, RIT y entrega | `SRC-LAB-RIT` |
| `LAB-019` | Aplicar correctamente el régimen laboral MYPE solo con inscripción y condiciones vigentes. | `STATUTORY` | Micro/pequeña empresa inscrita en REMYPE; derechos dependen de fecha y categoría. | C/W / continua | constancia REMYPE + fecha de ingreso | `SRC-REMYPE`, `SRC-REMYPE-COMPARE` |
| `LAB-020` | Cumplir límites, contrato, aprobación/registro y situación migratoria de personal extranjero. | `STATUTORY` | Contrata extranjero; revisar exoneraciones. | W / antes/evento | contrato SIVICE, calidad migratoria, cálculo límites | `SRC-LAB-FOREIGN` |
| `LAB-021` | Obtener autorización para trabajo adolescente y excluir labores peligrosas. | `STATUTORY` | Contrata persona de 14 a 17 años. | W / antes/continua | autorización, evaluación de puesto, horario | `SRC-LAB-ADOLESCENT` |
| `LAB-022` | Formalizar teletrabajo y cumplir seguridad, desconexión, equipos/compensación aplicable. | `STATUTORY` | Uno o más teletrabajadores. | W / evento/continua | acuerdo, registro, inventario y SST | `SRC-LAB-TELEWORK`, `SRC-LAB-TELEWORK-2026` |
| `LAB-023` | Constituir comité o elegir delegado contra hostigamiento sexual. | `STATUTORY` | 20 o más trabajadores: comité; menos de 20: delegado. | C / periodo/elección | actas, designación/elección | `SRC-LAB-HARASSMENT` |
| `LAB-024` | Implementar política, canal, capacitación, investigación y reporte de hostigamiento sexual. | `STATUTORY` | Empleador con personal. | C / continua/evento | política, registros y constancia de reporte | `SRC-LAB-HARASSMENT`, `SRC-LAB-HARASSMENT-PLATFORM` |
| `LAB-025` | Inscribirse/mantener registro para prestar intermediación laboral. | `PERMIT_CONDITION` | Empresa que destaca personal como intermediación comprendida. | C / antes/renovación | registro RENEEIL y contratos | `SRC-LAB-INTERMEDIATION` |
| `LAB-026` | Registrar personal de terceros/desplazado y coordinar obligaciones. | `STATUTORY` | Recibe o desplaza personal de terceros. | C/E/W / evento | T-Registro, contratos, coordinación SST | `SRC-LAB-THIRD-PARTY` |
| `LAB-027` | Vigilar casilla electrónica SUNAFIL y atender requerimientos. | `STATUTORY` | Empleador sujeto a inspección/notificación. | C / continua | bitácora y cargos | `SRC-LAB-SUNAFIL-MAILBOX` |
| `LAB-028` | Conservar legajos, contratos, boletas, pagos y evidencia laboral. | `STATUTORY` | Tiene o tuvo personal. | W / continua | legajo digital/físico | `SRC-LAB-RIGHTS`, `SRC-SST-LAW` |
| `LAB-029` | Evaluar regímenes sectoriales especiales de personal. | `STATUTORY` | Construcción civil, agrario, minero, pesquero, artístico, portuario u otro. | C/W / revisión | matriz sectorial + planilla | `SRC-LAB-RIGHTS` |

### 10.5. Seguridad y salud en el trabajo (SST)

| Código | Responsabilidad | Naturaleza | Activador resumido | Alcance / ciclo | Evidencia mínima | Fuentes |
|---|---|---|---|---|---|---|
| `SST-001` | Implementar Sistema de Gestión de SST proporcional a riesgos. | `STATUTORY` | Empleador; incluye protección de quienes trabajan en sus instalaciones. | C/E / continua | línea base, política, objetivos, plan | `SRC-SST-LAW`, `SRC-SST-MYPE-GUIDE` |
| `SST-002` | Elaborar y actualizar IPERC y controles por puesto/actividad. | `STATUTORY` | Existe trabajo o cambio de proceso/instalación. | E/W / continua/evento | matriz IPERC y evidencias de control | `SRC-SST-LAW`, `SRC-SST-DOCUMENTS` |
| `SST-003` | Elaborar mapa de riesgos, planificación preventiva y programa anual. | `STATUTORY` | Empleador. | E/C / anual/evento | documentos aprobados y publicados | `SRC-SST-LAW`, `SRC-SST-DOCUMENTS` |
| `SST-004` | Constituir Comité de SST. | `STATUTORY` | 20 o más trabajadores. | C/E según organización / elección | acta electoral, instalación, reuniones | `SRC-SST-LAW`, `SRC-SST-COMMITTEE` |
| `SST-005` | Elegir Supervisor de SST. | `STATUTORY` | 1 a 19 trabajadores. | C/E / elección | acta de elección y seguimiento | `SRC-SST-COMMITTEE` |
| `SST-006` | Elaborar y entregar Reglamento Interno de SST. | `STATUTORY` | 20 o más trabajadores. | C / evento/actualización | RISST y cargos | `SRC-SST-LAW` |
| `SST-007` | Brindar no menos de cuatro capacitaciones de SST al año y capacitación por puesto. | `STATUTORY` | Empleador con trabajadores. | W / anual/evento | plan, asistencia y contenido | `SRC-SST-TRAINING` |
| `SST-008` | Practicar exámenes médicos ocupacionales. | `STATUTORY` | En general cada dos años; alto riesgo antes, durante y al término, según protocolos. | W / periódico/evento | certificado de aptitud y custodia médica | `SRC-SST-EXAMS` |
| `SST-009` | Proporcionar equipos de protección y controles sin costo. | `STATUTORY` | Riesgo que requiere control/EPP. | W / continua | entrega, capacitación, inspección | `SRC-SST-LAW` |
| `SST-010` | Contratar SCTR salud y pensión. | `STATUTORY` | Actividad/exposición de alto riesgo incluida. | W / antes/continua | pólizas y nómina actualizada | `SRC-SST-SCTR`, `SRC-SST-SCTR-ACTIVITIES` |
| `SST-011` | Mantener registros obligatorios de SST y conservarlos por plazo. | `STATUTORY` | Empleador. | C/E / continua | registros de accidentes, enfermedades, monitoreo, inspecciones, capacitación, equipos, estadísticas y auditorías | `SRC-SST-DOCUMENTS`, `SRC-SST-RECORDS` |
| `SST-012` | Investigar accidentes, incidentes y enfermedades ocupacionales. | `STATUTORY` | Ocurre evento. | E/W / evento | investigación, causas y medidas | `SRC-SST-ACCIDENT` |
| `SST-013` | Notificar accidente mortal e incidente peligroso dentro de 24 horas. | `STATUTORY` | Evento mortal o peligroso reportable. | C/E / evento | constancia SAT/MTPE | `SRC-SST-ACCIDENT-24H` |
| `SST-014` | Notificar demás sucesos/enfermedades conforme al responsable y plazo aplicable. | `STATUTORY` | Accidente no mortal o enfermedad reportable. | C/E/W / evento | formulario y constancia | `SRC-SST-ACCIDENT` |
| `SST-015` | Preparar respuesta a emergencias, primeros auxilios, evacuación e inspecciones. | `STATUTORY` | Centro de trabajo. | E / continua/simulacros | plan, brigadas, simulacros, mantenimiento | `SRC-SST-LAW`, `SRC-MUN-ITSE` |
| `SST-016` | Coordinar SST con contratistas y personal de terceros. | `STATUTORY` | Concurrencia de empleadores en un centro/obra. | E/W / continua | inducción, coordinación, permisos de trabajo | `SRC-SST-LAW`, `SRC-LAB-THIRD-PARTY` |
| `SST-017` | Aplicar reglamento SST sectorial adicional. | `STATUTORY` | Sector con norma especial: construcción, minería, electricidad, hidrocarburos, etc. | C/E/P / revisión | matriz legal y evidencia especial | `SRC-SST-SECTOR-RULES` |

### 10.6. Locales, municipalidad y seguridad de edificaciones

| Código | Responsabilidad | Naturaleza | Activador resumido | Alcance / ciclo | Evidencia mínima | Fuentes |
|---|---|---|---|---|---|---|
| `MUN-001` | Obtener licencia de funcionamiento para cada establecimiento alcanzado. | `STATUTORY` | Desarrolla actividad en un local sujeto a licencia. | E / antes de operar | licencia y expediente | `SRC-MUN-LICENSE` |
| `MUN-002` | Validar zonificación/compatibilidad y condiciones del local. | `STATUTORY` | Solicitud/cambio de giro, área o uso. | E / antes/evento | informe/consulta y planos | `SRC-MUN-LICENSE` |
| `MUN-003` | Obtener ITSE de acuerdo con nivel de riesgo. | `STATUTORY` | Local sujeto; alto/muy alto requiere ITSE previa, bajo/medio conforme al procedimiento aplicable. | E / antes/posterior/renovación | certificado ITSE | `SRC-MUN-LICENSE`, `SRC-MUN-ITSE` |
| `MUN-004` | Mantener las condiciones de seguridad que sustentaron licencia/ITSE. | `PERMIT_CONDITION` | Local autorizado. | E / continua | inspecciones, mantenimiento, aforo, certificados | `SRC-MUN-ITSE` |
| `MUN-005` | Tramitar modificación/nueva licencia o ITSE ante cambios materiales. | `STATUTORY` | Cambio de giro, área, titular, riesgo, distribución u otro supuesto local. | E / evento | resolución/certificado actualizado | `SRC-MUN-LICENSE`, TUPA municipal vigente |
| `MUN-006` | Obtener autorización sectorial previa cuando la actividad figura en la lista aplicable. | `STATUTORY` | Actividad regulada que requiere autorización antes de licencia. | E/C / antes | autorización sectorial | `SRC-MUN-SECTOR-PRIOR`, ruta sectorial |
| `MUN-007` | Obtener permiso para anuncios/publicidad exterior. | `STATUTORY` | Instala anuncio sujeto a autorización. | E/anuncio / antes/renovación | autorización y diseño | `SRC-MUN-ADVERTISING`, TUPA municipal vigente |
| `MUN-008` | Obtener licencia de edificación, conformidad u otra habilitación para obras/cambio de uso. | `STATUTORY` | Construye, amplía, remodela o cambia uso en supuesto regulado. | P/E / antes/evento | licencia, planos, conformidad | `SRC-MUN-BUILDING`, TUPA municipal vigente |
| `MUN-009` | Cumplir horarios, ruido, aforo, salubridad y ordenanzas locales aplicables. | `STATUTORY` | Actividad/local en jurisdicción. | E / continua | matriz de ordenanzas + controles | ordenanza y TUPA de la municipalidad |
| `MUN-010` | Administrar vencimientos y fiscalizaciones por municipalidad, no por empresa solamente. | `VOLUNTARY_RISK_CONTROL` | Tiene uno o más locales. | E / continua | calendario por local | `SRC-MUN-LICENSE` |

### 10.7. Protección al consumidor, publicidad y comercio electrónico

| Código | Responsabilidad | Naturaleza | Activador resumido | Alcance / ciclo | Evidencia mínima | Fuentes |
|---|---|---|---|---|---|---|
| `CON-001` | Contar con Libro de Reclamaciones físico o virtual según canal. | `STATUTORY` | Proveedor de bienes/servicios a consumidores. | E/canal / continua | libro operativo y registros | `SRC-CONSUMER-BOOK`, `SRC-CONSUMER-CODE` |
| `CON-002` | Colocar aviso visible de disponibilidad del Libro. | `STATUTORY` | Establecimiento/plataforma alcanzada. | E/canal / continua | foto/captura fechada | `SRC-CONSUMER-BOOK` |
| `CON-003` | Incorporar acceso al Libro en sitio web/plataforma de venta. | `STATUTORY` | Venta por internet. | canal / continua | URL/captura/prueba funcional | `SRC-CONSUMER-BOOK` |
| `CON-004` | Responder reclamos y quejas en máximo 15 días hábiles, sin prórroga. | `STATUTORY` | Recibe reclamo/queja. | caso / evento | respuesta y cargo dentro de plazo | `SRC-CONSUMER-BOOK` |
| `CON-005` | Entregar producto/servicio idóneo y cumplir oferta, garantía y condiciones informadas. | `STATUTORY` | Relación de consumo. | T / continua | oferta, contrato, entrega y atención | `SRC-CONSUMER-CODE` |
| `CON-006` | Informar precio total, restricciones, riesgos y condiciones relevantes de forma clara. | `STATUTORY` | Oferta/publicidad/venta al consumidor. | T/canal / continua | piezas, lista de precios, términos | `SRC-CONSUMER-CODE` |
| `CON-007` | Evitar cláusulas abusivas, métodos comerciales coercitivos y discriminación. | `STATUTORY` | Contrato/atención a consumidores. | contrato/canal / continua | términos revisados, protocolo | `SRC-CONSUMER-CODE` |
| `CON-008` | Sustentar afirmaciones publicitarias y evitar publicidad engañosa. | `STATUTORY` | Difunde publicidad. | campaña / antes/continua | expediente de sustento | `SRC-CONSUMER-CODE` |
| `CON-009` | Obtener consentimiento previo, expreso e informado para llamadas/mensajes promocionales. | `STATUTORY` | Prospección comercial directa. | persona/campaña / antes | registro de consentimiento y oposición | `SRC-CONSUMER-MARKETING`, `SRC-DPA-REG` |
| `CON-010` | Conservar prueba de transacción, aceptación, entrega, cancelación y atención digital. | `VOLUNTARY_RISK_CONTROL` | Comercio electrónico; preserva defensa y prueba del cumplimiento de deberes legales concretos. | T / continua | logs, pedido, términos versionados, entrega | `SRC-CONSUMER-CODE` |
| `CON-011` | Cumplir reglas sectoriales de conducta de mercado. | `STATUTORY` | Financiero, telecom, salud, seguros, transporte, educación u otro regulado. | C/canal / revisión | contratos y reportes sectoriales | ruta sectorial |

### 10.8. Protección de datos personales

El nuevo Reglamento de la Ley 29733 está vigente desde el 31 de marzo de 2025. La ley se aplica al tratamiento de datos personales aunque la empresa no haya identificado formalmente un “banco de datos”. `SRC-DPA-REG`.

| Código | Responsabilidad | Naturaleza | Activador resumido | Alcance / ciclo | Evidencia mínima | Fuentes |
|---|---|---|---|---|---|---|
| `DPA-001` | Inventariar tratamientos, finalidades, titulares, datos, destinatarios y bases habilitantes. | `STATUTORY` | Trata datos personales. | C/DB / continua | registro de actividades/mapa de datos | `SRC-DPA-LAW`, `SRC-DPA-REG` |
| `DPA-002` | Informar al titular antes del tratamiento mediante aviso completo y accesible. | `STATUTORY` | Recaba/trata datos. | canal/flujo / antes | aviso versionado y prueba de entrega | `SRC-DPA-LAW`, `SRC-DPA-REG` |
| `DPA-003` | Obtener y probar consentimiento cuando no existe otra base habilitante. | `STATUTORY` | Tratamiento dependiente de consentimiento. | persona/finalidad / antes | log/firmas, texto y versión | `SRC-DPA-LAW`, `SRC-DPA-REG` |
| `DPA-004` | Respetar finalidad, proporcionalidad, calidad, seguridad y conservación limitada. | `STATUTORY` | Trata datos. | C/DB / continua | políticas, controles y auditoría | `SRC-DPA-LAW`, `SRC-DPA-REG` |
| `DPA-005` | Inscribir, modificar y cancelar bancos de datos personales en el registro nacional. | `STATUTORY` | Es titular de uno o más bancos de datos. | DB / evento | resolución/código registral | `SRC-DPA-BANK-REGISTER` |
| `DPA-006` | Implementar canal y plazos para derechos de los titulares. | `STATUTORY` | Trata datos. | C/canal / continua/evento | procedimiento, solicitudes y respuestas | `SRC-DPA-LAW`, `SRC-DPA-REG` |
| `DPA-007` | Implementar documento de seguridad y medidas organizativas, legales y técnicas. | `STATUTORY` | Trata datos. | C/sistema/DB / continua | documento, matriz de acceso, pruebas | `SRC-DPA-REG` |
| `DPA-008` | Formalizar encargo de tratamiento y controlar subencargados. | `STATUTORY` | Tercero procesa datos por cuenta de la empresa. | contrato/proveedor / antes/continua | DPA/contrato, autorización de subencargo | `SRC-DPA-REG` |
| `DPA-009` | Inscribir/comunicar flujo transfronterizo y aplicar garantías exigibles. | `STATUTORY` | Transfiere o permite tratamiento/acceso desde otro país en supuesto alcanzado. | flujo/DB / antes/evento | inscripción, contrato, evaluación de país | `SRC-DPA-CROSSBORDER`, `SRC-DPA-REG` |
| `DPA-010` | Definir plazos de conservación, bloqueo/eliminación y devolución del encargado. | `STATUTORY` | Conserva o encarga datos. | DB/contrato / continua/evento | calendario y actas de eliminación | `SRC-DPA-REG` |
| `DPA-011` | Registrar, evaluar y contener todo incidente de seguridad de datos. | `STATUTORY` | Ocurre incidente. | incidente / evento | registro, línea de tiempo, impacto y medidas | `SRC-DPA-REG` |
| `DPA-012` | Notificar a ANPD dentro de 48 horas cuando concurran los supuestos reglamentarios. | `STATUTORY` | Gran volumen/muchas personas/datos sensibles/daño evidente u otro supuesto normativo. | incidente / evento | constancia, evaluación y reporte | `SRC-DPA-REG` |
| `DPA-013` | Comunicar al titular dentro de 48 horas cuando el incidente afecte otros derechos. | `STATUTORY` | Supuesto reglamentario de afectación. | persona/incidente / evento | comunicación y cargo | `SRC-DPA-REG` |
| `DPA-014` | Notificar también al Centro Nacional de Seguridad Digital cuando el incidente es digital y corresponda. | `STATUTORY` | Incidente digital sujeto al marco de seguridad digital. | incidente / evento | constancia | `SRC-DPA-REG` |
| `DPA-015` | Designar Oficial de Datos Personales y comunicar/publicar contacto. | `STATUTORY` | Gran volumen/muchas personas/sensibles/daño evidente o actividad principal sensible; requiere revisión cualitativa. | C / evento/continua | designación, publicación y comunicación ANPD | `SRC-DPA-OFFICER`, `SRC-DPA-REG` |
| `DPA-016` | Evaluar impacto en privacidad para tratamientos de alto riesgo. | `VOLUNTARY_RISK_CONTROL` | Especialmente sensibles, perfilado, vulnerables o gran escala. El reglamento la formula como facultativa, no obligación general. | proyecto/flujo / antes | DPIA y plan de tratamiento | `SRC-DPA-REG` |
| `DPA-017` | Aplicar reglas reforzadas a datos de niñas, niños y adolescentes. | `STATUTORY` | Trata datos de menores. | persona/flujo / antes/continua | consentimiento/verificación de edad, aviso adaptado | `SRC-DPA-REG` |
| `DPA-018` | Obtener consentimiento directo para prospección y ofrecer oposición/revocación sencilla. | `STATUTORY` | Marketing directo. | persona/campaña / antes/continua | consentimientos, listas de exclusión | `SRC-DPA-REG`, `SRC-CONSUMER-MARKETING` |
| `DPA-019` | Aplicar las exigencias de datos a CCTV, biometría, geolocalización, cookies y perfilado según finalidad y proporcionalidad. | `STATUTORY` | Usa tecnología indicada; emitir inicialmente `REVIEW_REQUIRED` hasta precisar tratamiento y base. | sistema/E / antes/continua | evaluación, aviso, accesos y retención | `SRC-DPA-REG` |
| `DPA-020` | Mantener inventario de proveedores cloud y países de alojamiento/acceso. | `VOLUNTARY_RISK_CONTROL` | Usa SaaS/cloud/soporte exterior. | C/proveedor / continua | registro, contratos, arquitectura | `SRC-DPA-REG` |

### 10.9. Prevención de LA/FT, integridad y responsabilidad de la persona jurídica

La primera regla es identificar si la persona natural o jurídica es “sujeto obligado” ante la UIF u otro supervisor. No toda empresa lo es, y las exigencias específicas cambian por actividad y organismo supervisor. La SBS ofrece relación y verificación por RUC. `SRC-AML-SUBJECTS`, `SRC-AML-VERIFY`.

| Código | Responsabilidad | Naturaleza | Activador resumido | Alcance / ciclo | Evidencia mínima | Fuentes |
|---|---|---|---|---|---|---|
| `AML-001` | Determinar y documentar si es sujeto obligado y quién lo supervisa. | `STATUTORY` | Realiza actividad incluida en la Ley 29038/DS 020-2017-JUS o norma sectorial. | C / inicio/evento | verificación RUC + dictamen de actividad | `SRC-AML-SUBJECTS`, `SRC-AML-VERIFY` |
| `AML-002` | Implementar sistema de prevención LA/FT según regulación sectorial. | `STATUTORY` | Es sujeto obligado. | C / continua | manual, políticas, matriz de riesgo | `SRC-AML-UIF`, norma del supervisor |
| `AML-003` | Designar y acreditar Oficial de Cumplimiento cuando corresponda. | `STATUTORY` | Sujeto obligado según norma sectorial. | C / antes/continua | designación, autorización/registro | `SRC-AML-OFFICER` |
| `AML-004` | Identificar cliente, beneficiario final, origen/fondos y aplicar debida diligencia. | `STATUTORY` | Sujeto obligado y relación/operación alcanzada. | cliente/T / continua | expediente KYC y evaluación de riesgo | `SRC-AML-UIF`, norma del supervisor |
| `AML-005` | Registrar operaciones y conservar información exigida. | `STATUTORY` | Sujeto obligado; operación bajo regla sectorial. | T / continua | RO y archivo de sustento | `SRC-AML-UIF`, norma del supervisor |
| `AML-006` | Analizar y reportar operaciones sospechosas con confidencialidad. | `STATUTORY` | Sujeto obligado detecta indicios. | T / evento | constancia interna/ROS bajo acceso restringido | `SRC-AML-REPORTS` |
| `AML-007` | Revisar listas de interés y medidas de congelamiento cuando corresponda. | `STATUTORY` | Sujeto obligado según regulación. | cliente/T / continua | screening y gestión de coincidencias | `SRC-AML-LISTS` |
| `AML-008` | Capacitar, auditar y presentar informes periódicos de LA/FT. | `STATUTORY` | Según tipo de sujeto y supervisor. | C / periódico | capacitaciones, auditoría, informe | `SRC-AML-UIF`, norma del supervisor |
| `INT-001` | Evaluar e implementar modelo de prevención de delitos de la Ley 30424. | `VOLUNTARY_RISK_CONTROL` | Persona jurídica expuesta; puede ser exigencia contractual o sectorial. La ley general incentiva, no impone universalmente, el modelo. | C / continua | riesgos, encargado, canal, capacitación, monitoreo | `SRC-INTEGRITY-LAW`, `SRC-INTEGRITY-GUIDE` |
| `INT-002` | Realizar debida diligencia de socios, terceros y operaciones con el Estado. | `VOLUNTARY_RISK_CONTROL` | Intermediarios, licitaciones, permisos, alto riesgo. | tercero/T / antes/continua | screening, declaraciones y aprobación | `SRC-INTEGRITY-GUIDE` |
| `INT-003` | Implementar canal de denuncias, protección y protocolo de investigación. | `VOLUNTARY_RISK_CONTROL` | Recomendado; obligatorio si una norma/contrato especial lo exige. | C / continua/evento | canal, protocolo, casos y acciones | `SRC-INTEGRITY-GUIDE` |
| `INT-004` | Controlar conflictos de interés, regalos, donaciones y pagos de facilitación. | `VOLUNTARY_RISK_CONTROL` | Interacción con funcionarios/terceros. | C/T / continua | política, registro y aprobaciones | `SRC-INTEGRITY-GUIDE` |

### 10.10. Ambiente y residuos

| Código | Responsabilidad | Naturaleza | Activador resumido | Alcance / ciclo | Evidencia mínima | Fuentes |
|---|---|---|---|---|---|---|
| `ENV-001` | Verificar el listado de inclusión del SEIA y obtener certificación ambiental antes de ejecutar el proyecto. | `STATUTORY` | Proyecto incluido o susceptible de impactos negativos significativos. | P / antes de obra/operación | resolución de certificación e IGA | `SRC-ENV-SEIA`, `SRC-ENV-SEIA-CHECK` |
| `ENV-002` | Cumplir obligaciones y medidas del Instrumento de Gestión Ambiental. | `PERMIT_CONDITION` | Cuenta con IGA aprobado. | P/C/E / continua/periódica | monitoreos, reportes y registros | `SRC-ENV-SEIA`, `SRC-ENV-OEFA` |
| `ENV-003` | Tramitar modificación/ITS u otro procedimiento antes de cambiar proyecto cuando corresponda. | `STATUTORY` | Modificación, ampliación o mejora con relevancia ambiental. | P / antes | resolución/informe aprobado | `SRC-ENV-SEIA-CHECK`, norma sectorial |
| `ENV-004` | Segregar, almacenar y entregar residuos a gestor/servicio autorizado según tipo. | `STATUTORY` | Genera residuos municipales o no municipales. | E/P / continua | registros, contratos, constancias | `SRC-ENV-WASTE-LAW` |
| `ENV-005` | Presentar declaración anual y manifiestos en SIGERSOL no municipal. | `STATUTORY` | Generador no municipal obligado a contar con IGA; manifiestos para peligrosos según regla. | C/E / anual/evento | constancias SIGERSOL/manifiestos | `SRC-ENV-SIGERSOL` |
| `ENV-006` | Contratar Empresa Operadora de Residuos Sólidos autorizada cuando corresponda. | `STATUTORY` | Manejo externo de residuos no municipales/peligrosos sujeto. | E/T / continua | registro del operador, contrato y guías | `SRC-ENV-WASTE-LAW` |
| `ENV-007` | Obtener permisos para vertimiento, reúso, uso de agua, emisiones u otros recursos. | `STATUTORY` | Extrae agua, descarga, emite o usa recurso regulado; emitir primero `REVIEW_REQUIRED`. | P/E / antes/renovación | licencia/autorización y monitoreos | ruta `SECTOR-WATER-ANA` y norma sectorial |
| `ENV-008` | Reportar emergencia ambiental al OEFA/autoridad conforme al procedimiento. | `STATUTORY` | Emergencia ambiental reportable y administrado alcanzado. | incidente / evento | reporte preliminar/final y medidas | `SRC-ENV-EMERGENCY` |
| `ENV-009` | Atender supervisión ambiental y conservar evidencia de compromisos. | `STATUTORY` | Actividad bajo OEFA/EFA competente. | C/P/E / continua | matriz IGA, reportes, cargos | `SRC-ENV-OEFA` |
| `ENV-010` | Medir y gestionar huella de carbono. | `GROWTH_ENABLER` | Voluntario, clientes/financiación/mercado lo valoran. | C / anual | inventario GEI/verificación | `SRC-ENV-CARBON` |

### 10.11. Contratación pública

| Código | Responsabilidad | Naturaleza | Activador resumido | Alcance / ciclo | Evidencia mínima | Fuentes |
|---|---|---|---|---|---|---|
| `GOV-001` | Inscribirse y mantener categoría vigente en RNP. | `PERMIT_CONDITION` | Quiere ser proveedor del Estado en supuesto que exige RNP. | C / antes/continua | constancia RNP | `SRC-GOV-RNP`, `SRC-GOV-SUPPLIER` |
| `GOV-002` | Actualizar información legal ante RNP. | `STATUTORY` | Cambio de información registrable. | C / evento | trámite/constancia | `SRC-GOV-RNP-UPDATE` |
| `GOV-003` | Verificar impedimentos e inhabilitaciones antes de participar/contratar. | `STATUTORY` | Participación en contratación pública. | C/partes / antes | declaración y consultas | `SRC-GOV-PARTICIPANTS`, `SRC-GOV-SANCTIONS` |
| `GOV-004` | Acceder y operar SEACE conforme a la etapa/procedimiento. | `PERMIT_CONDITION` | Participa como proveedor. | procedimiento / evento | constancias SEACE | `SRC-GOV-SEACE` |
| `GOV-005` | Presentar información exacta y documentos auténticos. | `STATUTORY` | Participación, RNP o ejecución. | documento/proceso / continua | expediente verificado | `SRC-GOV-LAW-DIRECTIVES`, `SRC-GOV-SANCTIONS` |
| `GOV-006` | Mantener garantías, capacidad y requisitos del contrato. | `CONTRACTUAL` | Adjudicación/contrato; las exigencias legales relacionadas se modelan por separado. | contrato / hito/vencimiento | garantías, pólizas, renovaciones | contrato + `SRC-GOV-LAW-DIRECTIVES` |
| `GOV-007` | Cumplir hitos, entregables, conformidades y registros contractuales. | `CONTRACTUAL` | Contrato estatal. | contrato / evento | entregables, cargos y conformidad | contrato + `SRC-GOV-LAW-DIRECTIVES` |
| `GOV-008` | Controlar integridad, subcontratación y conflicto de interés pactados en el proceso o contrato. | `CONTRACTUAL` | Proceso/contrato público; impedimentos y declaraciones legales se cubren en otras filas. | contrato/tercero / continua | DD, cláusulas, autorizaciones | `SRC-GOV-LAW-DIRECTIVES`, `SRC-INTEGRITY-GUIDE` |

### 10.12. Comercio exterior y aduanas

| Código | Responsabilidad | Naturaleza | Activador resumido | Alcance / ciclo | Evidencia mínima | Fuentes |
|---|---|---|---|---|---|---|
| `TRA-001` | Mantener RUC activo y condición habilitante para operaciones aduaneras. | `STATUTORY` | Importador/exportador habitual u operación alcanzada. | C / continua | ficha RUC | `SRC-CUSTOMS-REQUIREMENTS` |
| `TRA-002` | Declarar mercancía y conservar documentos del despacho. | `STATUTORY` | Importación/exportación/régimen aduanero. | T / por operación | DAM, factura, transporte, seguro y pagos | `SRC-CUSTOMS-IMPORT` |
| `TRA-003` | Obtener documento de control para mercancía restringida y no operar mercancía prohibida. | `STATUTORY` | Subpartida/mercancía restringida o prohibida. | producto/T / antes | autorización VUCE/sector | `SRC-CUSTOMS-RESTRICTED`, `SRC-VUCE` |
| `TRA-004` | Usar agente de aduanas cuando el despacho lo exige. | `STATUTORY` | Tipo/valor de operación; p. ej., importación para consumo sobre umbral aplicable. | T / por operación | mandato y expediente | `SRC-CUSTOMS-AGENT` |
| `TRA-005` | Obtener autorización y mantener requisitos si actúa como operador de comercio exterior. | `PERMIT_CONDITION` | Agencia, depósito, courier u otro OCE. | C/E / antes/continua | resolución, garantía, registros | `SRC-CUSTOMS-OCE` |
| `TRA-006` | Aplicar valoración, clasificación y tributos/percepciones aduaneros correctos. | `STATUTORY` | Importación. | T / por operación | estudio, declaración y pago | `SRC-CUSTOMS-IMPORT`, `SRC-CUSTOMS-VALUATION` |
| `TRA-007` | Sustentar origen para trato arancelario preferencial. | `STATUTORY` | Solicita preferencia o emite prueba de origen. | producto/T / por operación | certificado/declaración y trazabilidad | VUCE/MINCETUR y acuerdo aplicable |
| `TRA-008` | Cumplir obligaciones del régimen aduanero especial elegido. | `STATUTORY` | Drawback, admisión temporal, depósito, tránsito u otro. | T / evento/plazo | expediente y regularización | `SRC-CUSTOMS-OPERATIONS` |

### 10.13. Obligaciones contractuales privadas

Estas filas no deben marcarse `APPLIES` sin leer el contrato.

| Código | Responsabilidad | Naturaleza | Activador | Evidencia mínima |
|---|---|---|---|---|
| `CTR-001` | Cumplir renta, uso permitido, mantenimiento, seguros y restitución de arrendamiento. | `CONTRACTUAL` | Contrato de alquiler/cesión. | contrato, pagos, inventarios, pólizas |
| `CTR-002` | Cumplir covenants, reportes, garantías y eventos de incumplimiento financieros. | `CONTRACTUAL` | Crédito, leasing, factoring, inversión. | contrato, certificados y cálculos |
| `CTR-003` | Cumplir SLA, niveles de calidad, seguridad, continuidad y penalidades. | `CONTRACTUAL` | Contrato con cliente/proveedor. | SLA, métricas y actas |
| `CTR-004` | Mantener pólizas y comunicar siniestros/cambios. | `CONTRACTUAL` | Póliza o contrato que exige cobertura. | póliza, pago, endosos, avisos |
| `CTR-005` | Cumplir licencia de software, contenido, franquicia o propiedad intelectual. | `CONTRACTUAL` | Usa activo licenciado. | licencia, inventario y controles |
| `CTR-006` | Cumplir confidencialidad y devolución/eliminación pactadas. | `CONTRACTUAL` | NDA/DPA/encargo; obligaciones legales de datos se evalúan en `DPA-*`. | contrato, accesos, certificados de eliminación |
| `CTR-007` | Mantener certificaciones/homologaciones requeridas por cliente o mercado. | `CONTRACTUAL` | Contrato o programa de proveedor. | certificado y auditorías |
| `CTR-008` | Controlar renovaciones, terminación, exclusividad, no competencia y cambios de control. | `CONTRACTUAL` | Contrato material. | calendario y aprobación legal |

### 10.14. Protección del negocio y crecimiento

| Código | Acción | Naturaleza | Activador recomendado | Evidencia/resultado | Fuentes |
|---|---|---|---|---|---|
| `PRO-001` | Registrar marca/nombre comercial y vigilar renovaciones/oposiciones. | `GROWTH_ENABLER` | Usa marca relevante. | título, clases, vigilancia | `SRC-IP-MARK` |
| `PRO-002` | Firmar cesiones/licencias de propiedad intelectual con empleados y proveedores. | `VOLUNTARY_RISK_CONTROL` | Encarga software, diseño, contenido, I+D. | contratos y entregables | `SRC-IP-MYPE-GUIDE` |
| `PRO-003` | Evaluar patente, modelo de utilidad, diseño o secreto empresarial. | `GROWTH_ENABLER` | Innovación diferenciadora. | estrategia y solicitudes/controles | `SRC-IP-INVENTOR` |
| `PRO-004` | Crear inventario de contratos y calendario de renovaciones. | `VOLUNTARY_RISK_CONTROL` | Tiene contratos materiales. | registro, alertas y responsables | buenas prácticas internas |
| `PRO-005` | Revisar seguros patrimoniales, RC, cyber, D&O, fraude, transporte e interrupción. | `VOLUNTARY_RISK_CONTROL` | Exposición material. | mapa de riesgo y pólizas | `SRC-SBS-MANDATORY-INSURANCE` para separar seguros legales |
| `PRO-006` | Implementar continuidad, copias, recuperación y simulacros. | `VOLUNTARY_RISK_CONTROL` | Dependencia de sistemas/local/personas clave. | BCP/DRP, backups probados | buenas prácticas internas |
| `PRO-007` | Segregar funciones, aprobar pagos y conciliar bancos/inventarios. | `VOLUNTARY_RISK_CONTROL` | Riesgo de error o fraude. | matriz de controles y pruebas | `SRC-INTEGRITY-GUIDE` |
| `PRO-008` | Mantener data room societario, tributario, laboral, contractual y de permisos. | `VOLUNTARY_RISK_CONTROL` | Busca inversión, crédito, venta o expansión. | índice y documentos vigentes | buenas prácticas de due diligence |
| `GRO-001` | Evaluar servicios CITE para calidad, laboratorio, producto y productividad. | `GROWTH_ENABLER` | MIPYME/productor busca mejorar. | plan/servicio CITE | `SRC-GROW-CITE` |
| `GRO-002` | Revisar convocatorias ProInnóvate y cofinanciamiento. | `GROWTH_ENABLER` | Proyecto de innovación/calidad/digitalización. | postulación y expediente | `SRC-GROW-PROINNOVATE` |
| `GRO-003` | Obtener certificaciones de calidad/inocuidad/seguridad requeridas por mercado. | `GROWTH_ENABLER` | Estrategia o acceso a mercado; si un contrato la exige, usar además `CTR-007`. | certificado vigente | `SRC-GROW-CITE` |
| `GRO-004` | Preparar habilitación para exportar y revisar acceso a mercados. | `GROWTH_ENABLER` | Potencial exportador. | plan, VUCE, requisitos país destino | `SRC-VUCE` |
| `GRO-005` | Medir huella de carbono y desempeño ESG cuando aporte a mercado/financiación. | `GROWTH_ENABLER` | Clientes/inversionistas/eficiencia. | reporte y metas | `SRC-ENV-CARBON` |

## 11. Router sectorial

Un `sector_route` no equivale a una obligación confirmada. Su salida inicial debe ser `REVIEW_REQUIRED` y crear un subcuestionario por actividad, establecimiento, producto, proyecto y rol de la empresa. El CIIU sirve como pista, no como prueba suficiente.

| Ruta / `flag_code` | Preguntas que la activan | Autoridades iniciales | Primera revisión obligatoria | Fuentes de entrada |
|---|---|---|---|---|
| `SECTOR-HEALTH-IPRESS` / `HEALTH_SERVICE_PROVIDER` | ¿Diagnostica, trata, rehabilita o presta servicios de salud? ¿Telemedicina? | MINSA, DIRESA/GERESA, SUSALUD | autorización/categorización y registro RENIPRESS por establecimiento, director/responsable, historias y datos sensibles | `SRC-SECTOR-IPRESS` |
| `SECTOR-PHARMA` / `PHARMA_OR_MEDICAL_PRODUCTS` | ¿Fabrica, importa, almacena, distribuye, dispensa o vende medicamentos, dispositivos o productos sanitarios? | DIGEMID/autoridad regional | autorización sanitaria previa del establecimiento, director técnico y registro del producto | `SRC-SECTOR-DIGEMID` |
| `SECTOR-FOOD-INDUSTRIAL` / `INDUSTRIALIZED_FOOD` | ¿Fabrica o importa alimentos/bebidas industrializados? | DIGESA, municipalidad; SENASA/SANIPES según origen | registro sanitario, rotulado, habilitación y vigilancia; separar restaurante de fabricación industrial | `SRC-SECTOR-DIGESA` |
| `SECTOR-RESTAURANT` / `FOOD_SERVICE` | ¿Prepara alimentos para consumo inmediato? | Municipalidad, autoridad sanitaria local; MINCETUR si calificado/turístico | condiciones sanitarias, manipulación, licencia/ITSE; registro sanitario de producto solo si realmente fabrica producto alcanzado | `SRC-MUN-LICENSE`, `SRC-SECTOR-TOURISM` |
| `SECTOR-FISHERY-SANITARY` / `HYDROBIOLOGICAL_PRODUCT` | ¿Cultiva, extrae, procesa, almacena, importa o exporta producto hidrobiológico? | PRODUCE, SANIPES, autoridad ambiental | derecho/autorización pesquera o acuícola, habilitación y certificación sanitaria, IGA | `SRC-SECTOR-SANIPES` |
| `SECTOR-AGRI-SENASA` / `AGRICULTURAL_OR_VETERINARY_REGULATED` | ¿Importa/exporta/procesa plantas, animales, plaguicidas, semillas, fármacos o alimentos veterinarios? | SENASA, MIDAGRI | registro de empresa/establecimiento/producto y permisos fito/zoosanitarios | `SRC-SECTOR-SENASA` |
| `SECTOR-FORESTRY-WILDLIFE` / `FORESTRY_OR_WILDLIFE` | ¿Aprovecha, transforma, transporta o comercia recursos forestales/fauna? | SERFOR, autoridad regional, SERNANP cuando corresponda | título, autorización/registro, guía y trazabilidad; CITES/área protegida | `SRC-SECTOR-SERFOR`, `SRC-VUCE` |
| `SECTOR-FINANCIAL-SBS` / `FINANCIAL_INSURANCE_PENSION` | ¿Capta dinero, presta como empresa regulada, emite dinero electrónico, asegura, administra pensiones o es COOPAC? | SBS | autorización de organización/funcionamiento, capital, idoneidad, conducta, riesgos y LA/FT | `SRC-SECTOR-SBS-AUTH` |
| `SECTOR-FX-LOAN-PAWN` / `FX_LOAN_OR_PAWN` | ¿Opera casa de cambio, préstamos o empeños bajo registro? | SBS/UIF | inscripción/registro y SPLAFT aplicable | `SRC-SECTOR-SBS-OTHER` |
| `SECTOR-CAPITAL-MARKET` / `SECURITIES_MARKET_ACTIVITY` | ¿Intermedia valores, administra fondos, tituliza, califica o realiza oferta pública? | SMV | autorización/registro, información financiera/hechos de importancia, conducta y LA/FT | `SRC-SECTOR-SMV` |
| `SECTOR-TELECOM` / `PUBLIC_TELECOM_SERVICE` | ¿Presta servicio público de telecomunicaciones o usa espectro? | MTC, OSIPTEL | concesión/registro/homologación/espectro; obligaciones a usuarios, calidad y reportes | `SRC-SECTOR-TELECOM` |
| `SECTOR-RADIO-TV` / `BROADCASTING` | ¿Opera radio o televisión? | MTC, CONCORTV/autoridades aplicables | autorización/frecuencia, contenido/publicidad y pagos | `SRC-SECTOR-MTC` |
| `SECTOR-MINING` / `MINING_ACTIVITY` | ¿Explora, explota, beneficia, transporta o almacena minerales? | MINEM/GORE, INGEMMET, OEFA, OSINERGMIN, ANA | concesión no basta: autorización de actividad, certificación ambiental, seguridad, agua y obligaciones del titular | `SRC-SECTOR-MINEM` |
| `SECTOR-HYDROCARBONS` / `HYDROCARBON_ACTIVITY` | ¿Explora, produce, transporta, almacena, distribuye o comercializa combustible/gas? | MINEM, OSINERGMIN, OEFA | registro/autorización, seguridad, ambiente, SCOP cuando corresponda | `SRC-SECTOR-MINEM`, `SRC-SECTOR-OSINERGMIN` |
| `SECTOR-ELECTRICITY` / `ELECTRICITY_ACTIVITY` | ¿Genera, transmite, distribuye o comercializa electricidad? | MINEM/GORE, OSINERGMIN, OEFA | concesión/autorización según capacidad/actividad, ambiente, seguridad y calidad | `SRC-SECTOR-MINEM` |
| `SECTOR-MANUFACTURING` / `MANUFACTURING_PLANT` | ¿Transforma materias primas o fabrica en planta? | PRODUCE/GORE, OEFA/EFA, municipalidad | compatibilidad, IGA/listado SEIA, límites/monitoreos, seguridad, registros de producto y SENATI | `SRC-SECTOR-PRODUCE`, `SRC-ENV-SEIA-CHECK`, `SRC-TAX-SENATI-RULE` |
| `SECTOR-CONSTRUCTION` / `CONSTRUCTION_OR_DEVELOPMENT` | ¿Ejecuta obras, desarrolla proyecto inmobiliario o vende unidades futuras? | Municipalidad, MVCS, SENCICO, SUNAFIL; OECE si Estado | habilitación/licencia/conformidad, RNE, SST construcción, SENCICO, contratos y consumidor | `SRC-MUN-BUILDING`, `SRC-TAX-SENCICO`, `SRC-SST-SECTOR-RULES` |
| `SECTOR-TRANSPORT` / `TRANSPORT_SERVICE` | ¿Transporta personas o mercancías por cuenta propia o de terceros? | MTC/GORE, SUTRAN, municipalidad | autorización/habilitación de empresa, conductor, vehículo, ruta/terminal, seguros y documentos de porte | `SRC-SECTOR-TRANSPORT` |
| `SECTOR-AVIATION` / `CIVIL_AVIATION` | ¿Realiza transporte aéreo, aviación general, mantenimiento, instrucción, drones o servicios aeroportuarios? | DGAC/MTC | permiso de operación, certificación RAP, aeronaves/personal/seguros y reportes | `SRC-SECTOR-DGAC` |
| `SECTOR-PORT` / `PORT_OR_MARITIME_SERVICE` | ¿Presta agenciamiento o servicio portuario básico? | APN, DICAPI, VUCE | licencia de operación por servicio/puerto y permisos marítimos | `SRC-SECTOR-APN` |
| `SECTOR-TOURISM` / `TOURISM_SERVICE_PROVIDER` | ¿Es hospedaje, agencia, guía, restaurante calificado, transporte turístico u otro prestador? | MINCETUR, GORE/MML, MTC según servicio | clasificación, registro/directorio y reglamento vigente según tipo de prestador | `SRC-SECTOR-TOURISM`, `SRC-SECTOR-TOURISM-DIRECTORY` |
| `SECTOR-EDUCATION-BASIC` / `PRIVATE_EDUCATION_BASIC` | ¿Opera colegio, inicial u otro servicio educativo básico privado? | DRE/UGEL, MINEDU, municipalidad | autorización de funcionamiento educativo, infraestructura, personal, información económica y convivencia | `SRC-SECTOR-MINEDU` |
| `SECTOR-EDUCATION-HIGHER` / `PRIVATE_HIGHER_EDUCATION` | ¿Opera universidad, instituto o escuela superior? | SUNEDU o MINEDU/GORE según tipo | licenciamiento/autorización, programas, grados y reportes | `SRC-SECTOR-SUNEDU`, `SRC-SECTOR-MINEDU` |
| `SECTOR-SECURITY` / `PRIVATE_SECURITY_SERVICE` | ¿Presta vigilancia, protección, tecnología de seguridad o seguridad privada? | SUCAMEC | autorización de modalidad/empresa y personal; armas si aplica | `SRC-SECTOR-SUCAMEC` |
| `SECTOR-ARMS-EXPLOSIVES` / `ARMS_EXPLOSIVES_PYROTECHNICS` | ¿Fabrica, importa, comercializa, almacena o usa armas/explosivos/pirotecnia? | SUCAMEC | licencia/autorización por producto, local, usuario y operación | `SRC-SECTOR-SUCAMEC` |
| `SECTOR-GAMBLING` / `GAMBLING_OR_BETTING` | ¿Explota casino, tragamonedas, juego a distancia o apuestas deportivas? | MINCETUR, SUNAT, UIF | autorización de explotación, homologaciones/registros, impuesto y SPLAFT | `SRC-SECTOR-GAMBLING`, `SRC-SECTOR-GAMBLING-ONLINE` |
| `SECTOR-WASTE-OPERATOR` / `WASTE_OPERATOR` | ¿Recolecta, transporta, valoriza, trata o dispone residuos de terceros? | MINAM/autoridad competente, OEFA | registro/autorización EO-RS, vehículos/infraestructura, SIGERSOL e IGA | `SRC-ENV-WASTE-LAW`, `SRC-ENV-SIGERSOL` |
| `SECTOR-WATER-ANA` / `WATER_USE_OR_DISCHARGE` | ¿Extrae agua, ocupa cauce, vierte o reúsa aguas residuales? | ANA/AAA/ALA, autoridad sectorial | derecho de uso, autorización de obra/vertimiento/reúso y monitoreo | `SRC-SECTOR-ANA` |
| `SECTOR-CHEMICAL-CONTROL` / `CONTROLLED_CHEMICALS` | ¿Usa/comercializa insumos químicos fiscalizados? | SUNAT, SUCAMEC u otra autoridad según sustancia | registro, inventario, reportes, seguridad y trazabilidad | `SRC-SECTOR-IQBF`, `SRC-SECTOR-SUCAMEC` |
| `SECTOR-LABOR-INTERMEDIATION` / `LABOR_INTERMEDIATION` | ¿Su negocio consiste en destacar trabajadores a clientes? | MTPE/GORE, SUNAFIL | RENEEIL, objeto permitido, fianza/contratos y límites | `SRC-LAB-INTERMEDIATION` |
| `SECTOR-CUSTOMS-OPERATOR` / `CUSTOMS_TRADE_OPERATOR` | ¿Presta agenciamiento, almacén, courier u otro servicio aduanero? | SUNAT Aduanas | autorización OCE, garantía, infraestructura, trazabilidad y categoría | `SRC-CUSTOMS-OCE` |
| `SECTOR-STATE-SUPPLIER` / `PUBLIC_PROCUREMENT` | ¿Vende o pretende vender al Estado? | OECE, RNP, entidad contratante | RNP/categoría, impedimentos, SEACE, capacidad y contrato | `SRC-GOV-SUPPLIER` |

### 11.1. Subcuestionario común para cualquier ruta sectorial

Cuando se activa una ruta, solicitar:

1. rol exacto: fabricante, titular, importador, distribuidor, operador, almacén, comercializador o prestador;
2. producto/servicio y subcategoría técnica;
3. dirección y jurisdicción de cada instalación;
4. capacidad, volumen, potencia, aforo, área u otro umbral sectorial;
5. fecha de inicio y etapas del proyecto;
6. autorizaciones vigentes, número, titular, alcance, condiciones y vencimiento;
7. responsable técnico, colegiatura/habilitación y vínculo;
8. registros de productos, equipos, vehículos, plataformas o programas;
9. reportes periódicos y último cargo;
10. fiscalizaciones, medidas, sanciones o procedimientos abiertos;
11. IGA/certificación ambiental y compromisos;
12. operaciones de importación/exportación y subpartidas;
13. personal en régimen especial y exposiciones de alto riesgo;
14. supervisores competentes nacionales, regionales y locales.

## 12. Matriz de autoridades

| Código | Entidad / rol | Materias que el motor puede asignar |
|---|---|---|
| `SUNAT` | Administración tributaria y aduanera | RUC, tributos, comprobantes, libros, planilla, aduanas, beneficiario final, IQBF |
| `SUNARP` | Registros públicos | Constitución, poderes, actos societarios, propiedad registrable |
| `MTPE` / `SUNAFIL` | Trabajo e inspección | Planilla laboral, derechos, SST, hostigamiento, regímenes y fiscalización |
| `ESSALUD` / `ONP` / `AFP` | Seguridad social y pensiones | Aportes, afiliaciones, coberturas y cobranza |
| `MUNICIPALITY` | Gobierno local por ubigeo | Licencia, ITSE, anuncios, edificación, tributos y ordenanzas |
| `INDECOPI` | Consumidor, competencia y propiedad intelectual | Reclamos, idoneidad, publicidad, marcas, patentes y derecho de autor |
| `ANPD` | Protección de datos | Bancos, flujos, derechos, seguridad, incidentes y ODP |
| `SBS_UIF` | Finanzas/seguros/pensiones y LA/FT | Autorizaciones, conducta, sujetos obligados y ROS |
| `MINAM_OEFA_SENACE` | Ambiente | SEIA, IGA, residuos, fiscalización y emergencias |
| `OECE_RNP` | Contratación pública | Proveedores, SEACE, RNP, impedimentos y sanciones |
| `SECTOR_AUTHORITY` | Autoridad especial | Título habilitante, registro, producto, reportes y condiciones |
| `PRIVATE_COUNTERPARTY` | Cliente, banco, arrendador, aseguradora, certificador | Deber contractual, no norma pública general |
| `BANK_LENDER_INVESTOR` | Banco, acreedor o inversionista privado | Covenants, garantías, información financiera, eventos de incumplimiento |
| `INSURER_BROKER` | Aseguradora o corredor | Pólizas legales/contractuales, declaraciones de riesgo, primas, endosos y siniestros |
| `AFP_EPS_HEALTH_PROVIDER` | AFP, EPS o prestador de salud ocupacional | Aportes/coberturas de origen legal y prestaciones contratadas; validar base normativa |
| `CUSTOMER_SUPPLIER_LANDLORD` | Cliente, proveedor o arrendador | SLA, calidad, pago, uso de local, confidencialidad, terminación y penalidades |
| `CLOUD_DATA_PROCESSOR` | Cloud, SaaS, call center u otro encargado | Contrato de encargo, seguridad, subencargo, ubicación y eliminación de datos |
| `CERTIFICATION_BODY` | Certificador, laboratorio u homologador | Requisito voluntario o contractual; será legal solo si una norma especial lo incorpora |

## 13. Formulario progresivo recomendado

No muestre las más de cien preguntas a todos los clientes. Use cinco fases.

### Fase A — Identificación automática y confirmación

1. RUC y fecha de corte.
2. Razón social, forma jurídica, estado/condición.
3. domicilio, establecimientos y CIIU registrados;
4. régimen tributario y padrones disponibles;
5. representantes y partida registral.

Todo dato importado de una fuente externa debe mostrarse al cliente para confirmar y conservar `source`, `retrieved_at` y `verification_status`.

### Fase B — Hechos que ninguna fuente registral prueba por sí sola

1. actividades reales y roles en la cadena;
2. productos, servicios y proyectos;
3. locales no declarados, obras y operaciones temporales;
4. personal, terceros, teletrabajo y riesgos;
5. consumidores y canales digitales;
6. datos personales y proveedores cloud;
7. ambiente, vehículos, comercio exterior y Estado.

### Fase C — Preguntas condicionales

| Si la respuesta es… | Abrir subformulario |
|---|---|
| `worker_count_total > 0` | planilla, beneficios, SST, seguros, hostigamiento y categorías salariales |
| `worker_count_total >= 20` | Comité SST, RISST y comité contra hostigamiento |
| `worker_count_total > 50` | promedio anual y cuota de discapacidad |
| `worker_count_total > 100` | Reglamento Interno de Trabajo |
| `women_age_15_49_at_site >= 20` | lactario de ese establecimiento |
| `high_risk_activity = true` | listado de puestos, SCTR, EMO y norma SST sectorial |
| `sells_to_end_consumers = true` | Libro de Reclamaciones, contratos, publicidad y marketing |
| `has_online_sales = true` | libro virtual, términos, logs, pagos, datos/cookies |
| `processes_personal_data = true` | inventario, bancos, avisos, derechos, seguridad y encargados |
| `processes_sensitive_data = true` | ODP, incidentes, DPIA recomendada y controles reforzados |
| `cross_border_data_flows count > 0` | países, receptores, contrato e inscripción |
| `establishments count > 0` | licencia/ITSE/anuncio/ordenanzas por local |
| `imports_goods = true` | subpartidas, VUCE, agente, valoración y permisos de producto |
| `contracts_with_state = true` | RNP, SEACE, impedimentos, contratos y garantías |
| `project_subject_to_seia != false` | listado SEIA, autoridad, certificación e IGA |
| `regulated_activity_flags count > 0` | una ruta sectorial por flag |

### Fase D — Carga de evidencia

Solicitar solo los documentos relevantes al match preliminar:

- ficha RUC;
- partida y vigencias;
- constancia del régimen/REMYPE;
- planilla/T-Registro agregado, sin exponer datos innecesarios;
- licencias/ITSE/autorizaciones;
- pólizas obligatorias;
- constancias de declaraciones;
- políticas y actas;
- contratos materiales;
- IGA y reportes;
- registros de producto/datos.

### Fase E — Declaración y revisión

El representante debe confirmar:

```text
Declaro que las actividades descritas son las que la empresa realiza efectivamente,
incluidas las secundarias, temporales, digitales y ejecutadas por terceros; que marqué
“desconocido” cuando no pude comprobar una respuesta; y que informaré los cambios
materiales para recalcular la matriz.
```

## 14. Formatos de carga

### 14.1. Perfil empresarial JSON

```json
{
  "schema_version": "1.0.0",
  "profile_as_of_date": "2026-08-24",
  "company": {
    "ruc": "20123456789",
    "legal_name": "EMPRESA EJEMPLO S.A.C.",
    "legal_form": "SAC",
    "operations_start_date": "2023-04-10"
  },
  "tax": {
    "tax_regime": "RMT",
    "ruc_status": "ACTIVE",
    "tax_domicile_condition": "HABIDO",
    "ruc_last_digit": 9,
    "annual_net_revenue_pen": 1400000,
    "net_assets_pen_previous_year_end": 850000
  },
  "activities": [
    {
      "description_plain": "Desarrollo y soporte de software empresarial",
      "ciiu_code": "6201",
      "activity_role": "SERVICE",
      "is_primary": true,
      "regulated_activity_flags": []
    }
  ],
  "workforce": {
    "worker_count_total": 27,
    "service_provider_count": 4,
    "remote_worker_count": 12,
    "high_risk_activity": false,
    "remype_status": "SMALL"
  },
  "consumer": {
    "sells_to_end_consumers": false,
    "has_online_sales": false,
    "performs_direct_marketing": true
  },
  "data": {
    "processes_personal_data": true,
    "processes_sensitive_data": false,
    "processes_children_data": false,
    "large_scale_or_many_subjects": null,
    "cross_border_data_flows": [
      {"country": "US", "recipient_role": "CLOUD_PROCESSOR"}
    ]
  },
  "establishments": [
    {
      "external_id": "LIM-OFFICE-01",
      "type": "OFFICE",
      "ubigeo": "150131",
      "public_access": false,
      "workers_at_site": 15,
      "women_age_15_49_at_site": 8,
      "municipal_risk_level": "LOW"
    }
  ]
}
```

### 14.2. Registro de responsabilidad para seed

```json
{
  "code": "SST-004",
  "name": "Constituir Comité de Seguridad y Salud en el Trabajo",
  "domain": "SST",
  "duty_nature": "STATUTORY",
  "scope_type": "COMPANY",
  "risk_level": "HIGH",
  "version": {
    "valid_from": "2011-08-21",
    "valid_to": null,
    "reviewed_at": "2026-08-24",
    "frequency_type": "EVENT_DRIVEN",
    "deadline_formula": {
      "type": "WHILE_CONDITION_TRUE",
      "renew_on": ["COMMITTEE_TERM_END", "WORKFORCE_THRESHOLD_CHANGE"]
    }
  },
  "rule": {
    "gte": [
      {"fact": "workforce.worker_count_total"},
      {"parameter": "SST_COMMITTEE_WORKERS_MIN"}
    ]
  },
  "evidence_types": [
    "ELECTION_CALL",
    "ELECTION_RECORD",
    "INSTALLATION_RECORD",
    "MEETING_MINUTES"
  ],
  "source_codes": ["SRC-SST-LAW", "SRC-SST-COMMITTEE"]
}
```

### 14.3. CSV mínimo de catálogo

```csv
code,name,domain,duty_nature,scope_type,frequency_type,risk_level,valid_from,valid_to,authority_code,review_policy
SST-004,Constituir Comité de SST,SST,STATUTORY,COMPANY,EVENT_DRIVEN,HIGH,2011-08-21,,SUNAFIL,AUTO
DPA-015,Designar Oficial de Datos Personales,DPA,STATUTORY,COMPANY,CONTINUOUS,HIGH,2025-03-31,,ANPD,HUMAN_IF_TRIGGERED
PRO-001,Registrar y proteger marca,PROTECTION,GROWTH_ENABLER,COMPANY,EVENT_DRIVEN,MEDIUM,2026-01-01,,INDECOPI,RECOMMENDATION
```

### 14.4. No usar Markdown como única fuente en producción

Este `.md` es la especificación maestra inicial. Para producción:

1. extraer el catálogo a JSON/CSV validado;
2. asignar IDs estables;
3. guardar reglas como JSON, no analizar texto libre en cada ejecución;
4. conservar enlaces y hashes de fuentes;
5. someter cambios a revisión y pruebas antes de activar una versión.

## 15. Calendario y generación de ocurrencias

### 15.1. Fórmulas, no fechas fijas

| Tipo | Fórmula sugerida |
|---|---|
| SUNAT mensual | `calendar_schedules[year, taxpayer_category, ruc_digit, tax_period]` |
| Renta anual | cronograma de ejercicio + condición MYPE/Ley 31940 vigente |
| Junta anual | `fiscal_year_end + 3 calendar months`, con reglas de convocatoria |
| CTS | primera quincena de mayo/noviembre; resolver día hábil y régimen |
| Gratificación | hasta 15 de julio/diciembre; validar norma vigente |
| Utilidades | fórmula vinculada a la DJ anual y comunicación al trabajador |
| Licencia/póliza | `expires_at - renewal_lead_days` |
| Respuesta a reclamo | `received_at + 15 business days` |
| Incidente de datos | `awareness_at + 48 hours` solo si el supuesto de notificación aplica |
| Accidente mortal/incidente peligroso | `occurred_or_known_at + 24 hours` |
| Ocurrencia contractual | hito y calendario del contrato, no de la ley general |

### 15.2. Estados de una ocurrencia

```yaml
occurrence_status:
  - NOT_STARTED
  - IN_PROGRESS
  - FILED_OR_COMPLETED
  - EVIDENCE_PENDING
  - UNDER_REVIEW
  - COMPLIANT
  - NONCOMPLIANT
  - OVERDUE
  - WAIVED_WITH_BASIS
  - NOT_APPLICABLE_AFTER_REASSESSMENT
```

Nunca considerar “cumplido” solo porque se cargó un archivo. Debe existir validación de tipo, periodo, titular, alcance, vigencia e integridad.

## 16. Casos de prueba del motor

### Caso A — Persona natural NRUS, tienda única, sin trabajadores

Hechos: una unidad de explotación, ventas/compras dentro de límites, B2C presencial, sin datos más allá de venta básica, local de bajo riesgo.

Resultados esperados:

- `TAX-002`, `TAX-010`, `MUN-001`, `MUN-003`, `CON-001`, `CON-002` → `APPLIES`;
- `LAB-001` → `DOES_NOT_APPLY` solo si también son conocidos y cero los demás supuestos de Planilla Electrónica;
- `LEG-006` → `DOES_NOT_APPLY` porque no es sociedad;
- si falta licencia o riesgo municipal → la responsabilidad aplica, el estado de cumplimiento será pendiente; no confundir aplicabilidad con cumplimiento.

### Caso B — SAC RMT, 27 trabajadores, software B2B y cloud exterior

Resultados esperados:

- planilla, beneficios, Vida Ley, SGSST, Comité SST, RISST, cuatro capacitaciones, hostigamiento con comité y teletrabajo para los remotos → `APPLIES`;
- RIT de más de 100 y cuota de discapacidad de más de 50 → `DOES_NOT_APPLY` con conteo conocido;
- datos: inventario, avisos, bancos, encargados, seguridad, derechos y flujo transfronterizo → `APPLIES` o `REVIEW_REQUIRED` según el detalle;
- ODP → `UNKNOWN` si no se conoce escala/daño; no inferir que no aplica.

### Caso C — Constructora RG, 80 trabajadores, obras públicas

Resultados esperados:

- obligaciones RG/IGV, SENCICO, planilla, utilidades si demás condiciones, cuota de discapacidad, SST general y SST construcción → `APPLIES`;
- `SECTOR-CONSTRUCTION` y cada proyecto/obra → `REVIEW_REQUIRED` hasta validar permisos e IGA;
- RNP/SEACE/contratos/garantías → `APPLIES`;
- SCTR → depende de actividad/puestos incluidos, normalmente debe revisarse por exposición y nómina.

### Caso D — Clínica privada, 35 trabajadores y datos de salud

Resultados esperados:

- ruta IPRESS → `REVIEW_REQUIRED` hasta verificar autorización/categoría/RENIPRESS;
- Comité SST, RISST, hostigamiento con comité y beneficios → `APPLIES`;
- datos sensibles y actividad principal con datos de salud → revisar/activar ODP, seguridad reforzada, incidentes, bancos y derechos;
- Libro de Reclamaciones y consumidor → `APPLIES`;
- no usar la licencia municipal como prueba de habilitación sanitaria.

### Caso E — Importador de alimentos industrializados sin trabajadores

Resultados esperados:

- aduanas, VUCE/mercancía restringida y ruta DIGESA → `REVIEW_REQUIRED` por producto/subpartida y luego responsabilidades por registro;
- CPE/tributación según régimen y bancarización por pago → condicional por transacción;
- planilla puede ser `DOES_NOT_APPLY` solo después de comprobar ausencia de todos los demás supuestos;
- si vende B2C en web → consumidor, libro virtual, datos y marketing aplican.

## 17. Controles de calidad antes de ponerlo en producción

### 17.1. Validación normativa

- toda responsabilidad obligatoria tiene al menos una fuente primaria oficial;
- la fuente estaba vigente en `valid_from`;
- toda modificación crea versión, nunca sobreescribe historia;
- umbral/tasa/cronograma vive en `parameter_values` o `calendar_schedules`;
- regla especial identifica autoridad y jurisdicción;
- una página orientativa no sustituye el texto normativo cuando existe duda;
- un TUPA prueba el trámite, pero deben revisarse también norma sustantiva y condiciones del título.

### 17.2. Validación técnica

- prueba de esquema para perfiles, reglas y parámetros;
- ninguna regla accede a un campo no declarado en `rule_required_facts`;
- pruebas de frontera: 19/20 trabajadores, 20/21, 50/51, 100/101;
- pruebas con `null` en cada activador;
- pruebas por fecha antes/después de vigencia;
- pruebas por establecimiento y no solo total de empresa;
- explicación reproducible con hechos y versión;
- idempotencia del mismo match run;
- detección de reglas superpuestas o contradictorias.

### 17.3. Validación de privacidad y seguridad de la propia plataforma

El cuestionario contiene datos corporativos, personales, financieros y posiblemente sensibles. La plataforma debe aplicar minimización, roles, cifrado, logs, segregación por cliente, respaldo, retención, gestión de incidentes y contratos con sus propios proveedores. Evitar cargar historias clínicas, DNIs completos o planillas nominales si un agregado o documento redactado satisface la verificación.

## 18. Mantenimiento y gobierno de la matriz

| Frecuencia | Revisión |
|---|---|
| diaria/semanal | normas o alertas críticas de autoridades relevantes |
| mensual | cronogramas, padrones, formularios y comunicados SUNAT/SUNAFIL/sector |
| trimestral | reglas sectoriales y municipales prioritarias |
| anual, antes del 1 de enero | UIT, RMV si cambia, cronogramas, tasas, umbrales y formularios |
| por evento | reforma legal, cambio de actividad/local/personal/producto/tecnología de una empresa |

Flujo de cambio:

```text
detectar fuente -> clasificar impacto -> redactar cambio -> revisión legal/técnica
-> pruebas de regresión -> nueva versión -> recalcular empresas afectadas
-> notificar diferencias -> conservar resultado anterior
```

Campos mínimos de una modificación:

```yaml
change_id: uuid
source_code: string
detected_at: timestamp
effective_from: date
affected_responsibility_codes: array
change_type: [NEW, AMEND, REPEAL, THRESHOLD, SCHEDULE, INTERPRETATION]
review_status: [DRAFT, LEGAL_REVIEW, APPROVED, REJECTED, DEPLOYED]
approved_by: string
regression_test_ids: array
```

## 19. Decisiones de implementación importantes

1. **Aplicabilidad no es cumplimiento.** `APPLIES` contesta “debe hacerlo”; `occurrence_status` contesta “lo hizo bien y a tiempo”.
2. **La empresa no es el único alcance.** Licencias, lactarios, permisos y riesgos pueden variar por local; registros por producto; seguros por trabajador/vehículo.
3. **Actividad real supera etiqueta registral.** Comparar RUC/CIIU con descripción y evidencia operacional.
4. **Fecha de corte es obligatoria.** Una empresa puede haber cumplido con reglas distintas en otro periodo.
5. **Hechos y conclusiones se separan.** El cliente declara hechos; el motor propone conclusiones; el especialista valida excepciones.
6. **El texto debe ser explicable.** Cada resultado muestra condición, valor, umbral, fecha, fuente y evidencia esperada.
7. **Los municipios son datos.** La base nacional no puede inventar horarios, tasas ni TUPA locales; se incorpora un módulo por `municipality_code`.
8. **El sector es una expansión.** La matriz transversal funciona como raíz y el router conecta módulos especializados.
9. **Las recomendaciones nunca se presentan como sancionables.** Protección y crecimiento usan `RECOMMENDED`.
10. **Revisión profesional visible.** Todo `REVIEW_REQUIRED` debe mostrar causa, preguntas y autoridad, no un mensaje genérico.

## 20. Registro de fuentes oficiales

Este registro permite resolver cada `source_code` usado por el catálogo y por las reglas. La fecha de consulta de esta edición es **24 de agosto de 2026**. La aplicación debe almacenar además `retrieved_at`, copia o huella del contenido consultado, vigencia comprobada y, cuando corresponda, la norma de rango superior. Un enlace vigente hoy puede cambiar de ruta sin que cambie la obligación.

### 20.1. Constitución, RUC y libros

- **SRC-LGS** — Diario Oficial El Peruano, [Ley General de Sociedades](https://diariooficial.elperuano.pe/Normas/obtenerDocumento?idNorma=15).
- **SRC-RUC-REGISTER** — SUNAT Emprender, [inscripción en el RUC](https://emprender.sunat.gob.pe/ruc/mi-ruc/inscripcion-ruc).
- **SRC-RUC-UPDATE** — SUNAT Emprender, [mantener actualizados los datos del RUC](https://emprender.sunat.gob.pe/ruc/mi-ruc/mantengo-mis-datos-actualizados).
- **SRC-RUC-CIIU** — SUNAT, [tablas anexas y CIIU del RUC](https://orientacion.sunat.gob.pe/6746-03-tablas-anexas-2-ruc-empresas).
- **SRC-BOOKS-RULE** — SUNAT, [Resolución de Superintendencia N.º 234-2006/SUNAT y normas de libros y registros](https://www.sunat.gob.pe/legislacion/superin/2006/234.htm).
- **SRC-SUNARP-SOCIETIES** — SUNARP, [Reglamento del Registro de Sociedades](https://scr.sunarp.gob.pe/resolucion-del-superintendente-nacional-de-los-registros-publicos-no-200-2001-sunarp-sn/).
- **SRC-SUNARP-REPRESENTATIVES** — SUNARP, [remoción y nombramiento de representantes de sociedad](https://scr.sunarp.gob.pe/remocion-y-nombramiento-de-representantes-de-sociedad/).

### 20.2. Tributación y comprobantes

- **SRC-TAX-REGIMES** — SUNAT Emprender, [comparación de regímenes tributarios](https://emprender.sunat.gob.pe/ruc/regimenes-tributarios-mype/regimenes-tributarios).
- **SRC-TAX-NRUS** — SUNAT Emprender, [Nuevo Régimen Único Simplificado](https://emprender.sunat.gob.pe/ruc/regimenes-tributarios-mype/nuevo-regimen-unico-simplificado-nuevo-rus).
- **SRC-TAX-RER** — SUNAT Emprender, [Régimen Especial de Renta](https://emprender.sunat.gob.pe/ruc/regimenes-tributarios-mype/regimen-especial-renta-rer).
- **SRC-TAX-RMT** — SUNAT Emprender, [Régimen MYPE Tributario](https://emprender.sunat.gob.pe/ruc/regimenes-tributarios-mype/regimen-mype-tributario).
- **SRC-TAX-RG** — SUNAT Emprender, [Régimen General de Renta](https://emprender.sunat.gob.pe/ruc/regimenes-tributarios-mype/regimen-general-renta-rg).
- **SRC-TAX-UIT** — SUNAT, [valores históricos y vigente de la UIT](https://www.sunat.gob.pe/indicestasas/uit.html).
- **SRC-TAX-CALENDAR-2026** — SUNAT, [cronograma de obligaciones mensuales 2026](https://www.sunat.gob.pe/orientacion/cronogramas/2026/cObligacionMensual2026.html).
- **SRC-TAX-ANNUAL-WHO** — SUNAT, [quiénes deben declarar el Impuesto a la Renta empresarial](https://renta.sunat.gob.pe/empresas/quienes-deben-declarar-el-impuesto).
- **SRC-TAX-ANNUAL-2025** — SUNAT, [cronogramas de la Declaración Anual 2025 presentada en 2026](https://renta.sunat.gob.pe/empresas/cronogramas-renta-anual-2025-fv-710).
- **SRC-TAX-CPE** — SUNAT, [obligados a emitir comprobantes de pago electrónicos](https://cpe.sunat.gob.pe/informacion_general/obligados_cpe).
- **SRC-TAX-SIRE** — SUNAT, [Sistema Integrado de Registros Electrónicos](https://cpe.sunat.gob.pe/node/139).
- **SRC-TAX-SIRE-CALENDAR** — SUNAT, [cronograma SIRE 2026](https://cpe.sunat.gob.pe/cronograma-2026).
- **SRC-TAX-GRE** — SUNAT Emprender, [Guía de Remisión Electrónica](https://emprender.sunat.gob.pe/comprobantes-libros/otros-documentos/guia-remision-electronica).
- **SRC-TAX-SPOT** — SUNAT Emprender, [Sistema de Detracciones del IGV](https://emprender.sunat.gob.pe/principales-impuestos/impuesto-general-las-ventas-igv/sistema-detracciones-igv).
- **SRC-TAX-WITHHOLD-PERCEPTION** — SUNAT, [sistemas de detracciones, percepciones y retenciones del IGV](https://emprender.sunat.gob.pe/principales-impuestos/impuesto-general-las-ventas-igv/sistema-detracciones-percepciones-retenciones).
- **SRC-TAX-BANK** — SUNAT Emprender, [bancarización](https://emprender.sunat.gob.pe/comprobantes-libros/comprobantes-pago/bancarizacion).
- **SRC-TAX-ITAN** — SUNAT, [sujetos obligados al ITAN](https://orientacion.sunat.gob.pe/3160-02-sujetos-obligados).
- **SRC-TAX-ITAN-CALENDAR** — SUNAT, [declaración, pago y cronograma del ITAN](https://orientacion.sunat.gob.pe/7354-08-cronograma-para-la-presentacion-y-pago-del-itan-2020).
- **SRC-TAX-DAOT** — SUNAT, [Declaración Anual de Operaciones con Terceros](https://orientacion.sunat.gob.pe/declaracion-anual-de-operaciones-con-terceros-daot).
- **SRC-TAX-TP-LOCAL** — SUNAT, [declaración informativa Reporte Local](https://orientacion.sunat.gob.pe/7119-02-declaracion-jurada-informativa-reporte-local).
- **SRC-TAX-TP-MASTER** — SUNAT, [declaración informativa Reporte Maestro](https://orientacion.sunat.gob.pe/7120-03-declaracion-jurada-informativa-reporte-maestro).
- **SRC-TAX-CBC** — SUNAT, [Reporte País por País](https://nodomiciliados.sunat.gob.pe/es/fiscalidad-internacional/precios-de-transferencia/2-declaraciones/23-reporte-pais-por-pais-country).
- **SRC-TAX-RETENTION** — SUNAT, [Código Tributario: obligaciones y conservación de libros/documentos](https://www.sunat.gob.pe/legislacion/codigo/libro2/titulo4.htm).
- **SRC-TAX-BENEFICIAL** — SUNAT Emprender, [declaración del beneficiario final](https://emprender.sunat.gob.pe/declaracion-pagos/declaracion/beneficiario-final).
- **SRC-TAX-BENEFICIAL-2026** — SUNAT, [cronograma 2026 para declaración del beneficiario final](https://www.sunat.gob.pe/mensajes/julio/2026/aviso-ti-200726.html).
- **SRC-TAX-COMPLIANCE-PROFILE** — SUNAT Emprender, [perfil de cumplimiento tributario](https://emprender.sunat.gob.pe/ruc/otros/perfil-cumplimiento).
- **SRC-TAX-CREDIT** — SUNAT, [requisitos sustanciales y formales del crédito fiscal](https://orientacion.sunat.gob.pe/3111-06-credito-fiscal).
- **SRC-TAX-NONRESIDENT** — SUNAT, [portal de fiscalidad internacional y no domiciliados](https://nodomiciliados.sunat.gob.pe/).
- **SRC-TAX-NOTIFICATIONS** — SUNAT, [padrones, publicaciones y notificaciones](https://www.sunat.gob.pe/padronesnotificaciones/).
- **SRC-TAX-WITHHOLD-INCOME** — SUNAT, [retención y declaración vinculada a trabajadores dependientes](https://personas.sunat.gob.pe/trabajador-dependiente/declaracion-pago).
- **SRC-TAX-SENATI-RULE** — SENATI, [contribuciones, sujetos y presentación anual](https://www.senati.edu.pe/content/contribuciones).
- **SRC-TAX-SENCICO** — SENCICO, [contribución al SENCICO](https://www.gob.pe/institucion/sencico/campa%C3%B1as/2860-contribucion-al-sencico).

### 20.3. Trabajo, planilla y derechos laborales

- **SRC-LAB-PLANILLA** — SUNAT Emprender, [Planilla Electrónica](https://emprender.sunat.gob.pe/principales-impuestos/planilla/planilla-electronica).
- **SRC-LAB-OBLIGATIONS** — SUNAT Emprender, [obligaciones con los trabajadores](https://emprender.sunat.gob.pe/principales-impuestos/planilla/obligaciones-con-mis-trabajadores).
- **SRC-LAB-TREG** — SUNAT, [obligaciones del empleador en el T-Registro](https://orientacion.sunat.gob.pe/03-obligaciones-del-empleador-t-registro).
- **SRC-LAB-RMV** — MTPE, [Decreto Supremo N.º 006-2024-TR sobre Remuneración Mínima Vital](https://www.gob.pe/institucion/mtpe/normas-legales/6335262-006-2024-tr).
- **SRC-LAB-VIDA-LEY** — SBS, [Seguro Vida Ley desde el inicio de la relación laboral](https://www.sbs.gob.pe/usuarios/aprende-con-la-sbs/seguro-vida-ley).
- **SRC-REMYPE** — Estado peruano, [Registro de la Micro y Pequeña Empresa](https://www.gob.pe/279-registro-de-la-micro-y-pequena-empresa-remyp).
- **SRC-REMYPE-COMPARE** — MTPE, [cuadro comparativo de la regulación MYPE](https://www.gob.pe/institucion/mtpe/informes-publicaciones/6887029-cuadro-comparativo-de-la-regulacion-de-las-mype).
- **SRC-LAB-CTS** — SUNAFIL, [obligación y oportunidad de pago de la CTS](https://www.gob.pe/institucion/sunafil/noticias/1167715-empleadores-que-se-retrasen-en-pago-de-cts-deben-considerar-intereses).
- **SRC-LAB-GRATIFICATION** — SUNAFIL, [gratificaciones legales](https://www.gob.pe/institucion/sunafil/noticias/679430-sunafil-empresas-tienen-hasta-el-15-de-diciembre-para-depositar-la-grati).
- **SRC-LAB-PROFIT** — SUNAFIL, [trabajadores beneficiarios y empresas obligadas a distribuir utilidades](https://www.gob.pe/institucion/sunafil/noticias/587495-quienes-pueden-recibir-las-utilidades-y-que-empresas-estan-obligadas-a-pagarlas).
- **SRC-LAB-PENSION** — SUNAFIL, [retención y pago de aportes al sistema privado de pensiones](https://www.gob.pe/institucion/sunafil/noticias/938750-empleadores-que-retengan-aportes-sin-haber-efectuado-el-pago-a-la-afp-incurren-en-una-falta-muy-grave).
- **SRC-LAB-EQUAL-PAY** — SUNAFIL, [igualdad salarial y cuadro de categorías y funciones](https://www.gob.pe/institucion/sunafil/noticias/1363500-sunafil-recibio-350-denuncias-en-el-2025-por-incumplimiento-de-la-igualdad-salarial-en-empresas).
- **SRC-LAB-DISABILITY** — SUNAFIL, [cuota de empleo para personas con discapacidad](https://www.gob.pe/institucion/sunafil/noticias/1264134-conoce-si-debes-cumplir-con-la-cuota-de-empleo-para-personas-con-discapacidad).
- **SRC-LAB-LACTATION** — SUNAFIL, [implementación de lactarios](https://www.gob.pe/institucion/sunafil/noticias/1285883-sunafil-solicita-informacion-sobre-implementacion-de-lactarios-en-beneficio-de-madres-trabajadoras).
- **SRC-LAB-LACTATION-LEAVE** — MTPE, [permiso por lactancia materna](https://www.gob.pe/institucion/mtpe/noticias/1010308-mtpe-madres-trabajadoras-tienen-derecho-a-una-hora-de-permiso-por-lactancia).
- **SRC-LAB-RIT** — MTPE, [Reglamento Interno de Trabajo para empresas con más de 100 trabajadores](https://www.gob.pe/institucion/mtpe/noticias/739390-mtpe-empresas-de-mas-de-100-trabajadores-estan-obligadas-a-tener-reglamento-interno-de-trabajo).
- **SRC-LAB-FOREIGN** — MTPE, [Registro Nacional de Contratos de Trabajo de Personal Extranjero y SIVICE](https://www2.trabajo.gob.pe/directivas-mtpe/viceministerio-de-trabajo/direccion-general-de-trabajo/direccion-de-registros-nacionales-de-relaciones-de-trabajo/registro-nacional-de-contratos-de-trabajo-de-personal-extranjero-en-el-sistema-virtual-de-contratos-de-extranjeros-sivice/).
- **SRC-LAB-ADOLESCENT** — MTPE, [autorización de trabajo para adolescentes](https://www.gob.pe/institucion/mtpe/informes-publicaciones/2078481-autorizacion-de-trabajo-para-el-adolescente).
- **SRC-LAB-TELEWORK** — Congreso de la República, [Ley N.º 31572, Ley del Teletrabajo](https://www.gob.pe/institucion/congreso-de-la-republica/normas-legales/3460247-31572).
- **SRC-LAB-TELEWORK-2026** — MTPE, [Decreto Supremo N.º 009-2026-TR](https://www.gob.pe/institucion/mtpe/normas-legales/8412124-009-2026-tr).
- **SRC-LAB-HARASSMENT** — SUNAFIL, [prevención e investigación del hostigamiento sexual laboral](https://www.gob.pe/institucion/sunafil/noticias/871869-sunafil-mas-de-1300-empleadores-fueron-orientados-para-prevenir-hostigamiento-sexual-en-los-centros-de-trabajo).
- **SRC-LAB-HARASSMENT-PLATFORM** — MTPE, [plataforma de registro de casos de hostigamiento sexual laboral](https://www.gob.pe/institucion/mtpe/campa%C3%B1as/539-plataforma-de-registro-de-casos-de-hostigamiento-sexual-laboral).
- **SRC-LAB-INTERMEDIATION** — MTPE, [registro de empresas y entidades de intermediación laboral](https://www2.trabajo.gob.pe/servicios/registro-de-empresas-y-entidades-que-realizan-actividades-de-intermediacion-laboral-en-el-ambito-de-lima-metropolitana/).
- **SRC-LAB-THIRD-PARTY** — SUNAT, [empleadores, prestadores y personal de terceros en el T-Registro](https://www.sunat.gob.pe/ayuda/tributos/tregistro-E-P/T-RegistroPrivado-H02.html).
- **SRC-LAB-SUNAFIL-MAILBOX** — Estado peruano, [acceso a la casilla electrónica de SUNAFIL](https://www.gob.pe/10006-acceder-a-la-casilla-electronica-de-sunafil).
- **SRC-LAB-RIGHTS** — MTPE, [portal oficial de derechos y obligaciones laborales](https://www.gob.pe/institucion/mtpe/tema/derechos-y-obligaciones-laborales).

### 20.4. Seguridad y salud en el trabajo

- **SRC-SST-LAW** — Diario Oficial El Peruano, [Ley N.º 29783, Ley de Seguridad y Salud en el Trabajo, texto actualizado](https://diariooficial.elperuano.pe/Normas/obtenerDocumento?idNorma=38).
- **SRC-SST-COMMITTEE** — SUNAFIL, [Comité o Supervisor de Seguridad y Salud en el Trabajo](https://www.gob.pe/institucion/sunafil/noticias/583696-empresas-con-20-o-mas-trabajadores-deben-contar-con-un-comite-de-seguridad-y-salud-en-el-trabajo).
- **SRC-SST-TRAINING** — SUNAFIL, [implementación y capacitaciones del sistema de SST](https://www.gob.pe/institucion/sunafil/noticias/1207383-mas-de-500-personas-participaron-en-seminario-sobre-la-implementacion-de-la-ley-de-seguridad-y-salud-en-el-trabajo-en-la-mype).
- **SRC-SST-SCTR** — SBS, [Seguro Complementario de Trabajo de Riesgo](https://www.sbs.gob.pe/usuarios/aprende-con-la-sbs/seguro-complementario-de-trabajo-de-riesgo-sctr).
- **SRC-SST-SCTR-ACTIVITIES** — ONP, [actividades que deben contratar SCTR Pensión](https://www.gob.pe/institucion/onp/noticias/1064619-restaurantes-veterinarias-y-seguridad-privada-conoce-las-270-actividades-que-deben-contratar-el-sctr-pension).
- **SRC-SST-EXAMS** — MTPE, [exámenes médicos ocupacionales y vigilancia de la salud](https://www2.trabajo.gob.pe/el-ministerio-2/sector-trabajo/direccion-general-de-trabajo/boletines/boletines-2017/boletin-no-74/).
- **SRC-SST-DOCUMENTS** — MTPE, [documentos y registros del sistema de gestión de SST](https://www.gob.pe/institucion/mtpe/informes-publicaciones/7022694-documentos-y-registros-del-sistema-de-gestion-de-seguridad-y-salud-en-el-trabajo).
- **SRC-SST-RECORDS** — SUNAFIL, [sistema de gestión y registros de SST](https://www.sunafil.gob.pe/portal/component/k2/item/3643-sistema-de-gestion-de-sst.html?print=1&tmpl=component).
- **SRC-SST-ACCIDENT** — Estado peruano, [notificación de accidentes, incidentes peligrosos y enfermedades ocupacionales](https://www.gob.pe/774-notificar-accidentes-de-trabajo-incidentes-peligrosos-y-enfermedades-ocupacionales).
- **SRC-SST-ACCIDENT-24H** — MTPE, [plazo para reportar accidentes mortales e incidentes peligrosos](https://www.gob.pe/institucion/mtpe/noticias/562941-atencion-empleadores-accidentes-de-trabajo-mortales-e-incidentes-peligrosos-deben-ser-notificados-al-mtpe-en-un-plazo-no-mayor-de-24-horas).
- **SRC-SST-MYPE-GUIDE** — MTPE, [guía del sistema de gestión de SST para MYPE](https://www.gob.pe/institucion/mtpe/informes-publicaciones/1942399-guia-del-sistema-de-gestion-de-seguridad-y-salud-en-el-trabajo-para-mypes).
- **SRC-SST-SECTOR-RULES** — MTPE, [repositorio oficial de normas laborales y de SST](https://www.gob.pe/institucion/mtpe/normas-legales).

### 20.5. Locales, edificaciones, vehículos y seguros obligatorios

- **SRC-MUN-LICENSE** — Estado peruano, [licencia municipal de funcionamiento](https://www.gob.pe/15880-obtener-licencia-municipal-de-funcionamiento).
- **SRC-MUN-ITSE** — Municipalidad Metropolitana de Lima, [Inspección Técnica de Seguridad en Edificaciones](https://www.munlima.gob.pe/tramites-y-servicios/certificado-de-inspecciones-tecnicas-de-seguridad-en-edificaciones-itse/).
- **SRC-MUN-SECTOR-PRIOR** — PCM, [actividades que requieren autorización sectorial previa a la licencia municipal](https://www.gob.pe/institucion/pcm/noticias/10600-fijan-actividades-que-deberan-presentar-autorizacion-del-ejecutivo-para-solicitar-licencias-de-funcionamiento-municipal).
- **SRC-MUN-ADVERTISING** — Estado peruano, [autorización municipal para anuncios de publicidad exterior](https://www.gob.pe/21049-solicitar-autorizacion-para-exhibir-anuncios-de-publicidad).
- **SRC-MUN-BUILDING** — Estado peruano, [licencia de edificación](https://www.gob.pe/20996-obtener-licencia-de-edificacion-para-proyectos-de-la-modalidad-a?child=23903).
- **SRC-MUN-TAXES** — Estado peruano, [tributos municipales](https://www.gob.pe/50848-tributos-municipales).
- **SRC-MUN-VEHICLE-TAX** — Estado peruano, [Impuesto al Patrimonio Vehicular](https://www.gob.pe/22057-pagar-el-impuesto-vehicular).
- **SRC-ASSET-SOAT** — SBS, [SOAT y obligaciones de aseguramiento vehicular](https://www.sbs.gob.pe/noticia/detallenoticia/idnoticia/3850).
- **SRC-ASSET-CITV** — Estado peruano, [inspección técnica vehicular](https://www.gob.pe/397-inspeccion-tecnica-vehicular).
- **SRC-SBS-MANDATORY-INSURANCE** — SBS, [relación de seguros obligatorios](https://www.sbs.gob.pe/relacion-de-seguros-obligatorios).

### 20.6. Consumidor, publicidad y canales digitales

- **SRC-CONSUMER-CODE** — Indecopi, [Ley N.º 29571, Código de Protección y Defensa del Consumidor](https://www.gob.pe/institucion/indecopi/normas-legales/1244218-29571).
- **SRC-CONSUMER-BOOK** — Indecopi, [Libro de Reclamaciones](https://consumidor.gob.pe/libro-de-reclamaciones/).
- **SRC-CONSUMER-MARKETING** — Indecopi, [reglas y fiscalización sobre llamadas y mensajes publicitarios](https://www.gob.pe/institucion/indecopi/noticias/1173297-comunicado).

### 20.7. Protección de datos personales

- **SRC-DPA-LAW** — Diario Oficial El Peruano, [Ley N.º 29733 y normativa oficial de protección de datos personales](https://diariooficial.elperuano.pe/Normas/obtenerDocumento?idNorma=23).
- **SRC-DPA-REG** — Autoridad Nacional de Protección de Datos Personales, [Decreto Supremo N.º 016-2024-JUS, nuevo Reglamento de la Ley N.º 29733](https://www.gob.pe/institucion/anpd/normas-legales/6554453-16-2024-jus).
- **SRC-DPA-BANK-REGISTER** — ANPD, [inscripción de bancos de datos personales](https://www.gob.pe/8060-inscribir-banco-de-datos-en-el-registro-nacional-de-proteccion-de-datos-personales).
- **SRC-DPA-CROSSBORDER** — ANPD, [inscripción/comunicación del flujo transfronterizo de datos personales](https://www.gob.pe/9253-inscribir-flujo-transfronterizo-de-datos-personales).
- **SRC-DPA-OFFICER** — ANPD, [funciones y supuestos del Oficial de Datos Personales](https://www.gob.pe/40556-quien-es-el-oficial-de-datos-personales).

### 20.8. Prevención de LA/FT e integridad

- **SRC-AML-SUBJECTS** — SBS/UIF, [relación y marco de sujetos obligados](https://www.sbs.gob.pe/prevencion-de-lavado-activos/sujetos-obligados).
- **SRC-AML-VERIFY** — SBS/UIF, [consulta para verificar si una persona jurídica es sujeto obligado](https://www.sbs.gob.pe/app/uif/voc/).
- **SRC-AML-UIF** — SBS/UIF, [portal de prevención de lavado de activos y financiamiento del terrorismo](https://www.sbs.gob.pe/prevencion-de-lavado-activos).
- **SRC-AML-OFFICER** — SBS/UIF, [Oficial de Cumplimiento](https://www.sbs.gob.pe/prevencion-de-lavado-activos/Oficial-de-Cumplimiento).
- **SRC-AML-REPORTS** — SBS/UIF, [información y reportes que los supervisados remiten a la UIF](https://www.sbs.gob.pe/prevencion-de-lavado-activos/Supervisados/Informacion-a-remitir-a-la-UIF).
- **SRC-AML-LISTS** — SBS/UIF, [listas de interés y sanciones internacionales](https://www.sbs.gob.pe/prevencion-de-lavado-activos/listas-de-interes).
- **SRC-INTEGRITY-LAW** — SMV, [Decreto Supremo N.º 002-2019-JUS, Reglamento de la Ley N.º 30424](https://www.gob.pe/institucion/smv/normas-legales/441953-decreto-supremo-n-002-2019-jus).
- **SRC-INTEGRITY-GUIDE** — SMV, [lineamientos del modelo de prevención para personas jurídicas](https://www.smv.gob.pe/ConsultasP8/documento.aspx?vidDoc=%7B50BC157A-0000-CA13-99DD-E41EA969B5EE%7D).

### 20.9. Ambiente, residuos y cambio climático

- **SRC-ENV-SEIA** — MINAM, [certificación ambiental en el SEIA](https://www.minam.gob.pe/seia/que-es-la-certificacion-ambiental/).
- **SRC-ENV-SEIA-CHECK** — MINAM, [cómo determinar si un proyecto está dentro de los parámetros del SEIA](https://www.minam.gob.pe/seia/como-se-si-mi-proyecto-de-inversion-esta-dentro-de-los-parametros-del-seia/).
- **SRC-ENV-WASTE-LAW** — MINAM, [Reglamento del Decreto Legislativo N.º 1278 sobre gestión integral de residuos sólidos](https://www.minam.gob.pe/wp-content/uploads/2017/12/ds_014-2017-minam.pdf).
- **SRC-ENV-SIGERSOL** — MINAM, [SIGERSOL No Municipal](https://site2.minam.gob.pe/sigersol-no-municipal).
- **SRC-ENV-OEFA** — OEFA, [portal institucional y obligaciones de fiscalización ambiental](https://www.gob.pe/oefa).
- **SRC-ENV-EMERGENCY** — OEFA, [reporte de emergencias ambientales](https://www.gob.pe/923-reporta-emergencias-ambientales-al-oefa).
- **SRC-ENV-CARBON** — MINAM, [Huella de Carbono Perú para medir emisiones organizacionales](https://www.gob.pe/7852-medir-emisiones-de-gases-de-efecto-invernadero-de-mi-organizacion).

### 20.10. Contratación pública

- **SRC-GOV-RNP** — OECE, [Dirección del Registro Nacional de Proveedores](https://www.gob.pe/9917-organismo-especializado-para-las-contrataciones-publicas-eficientes-direccion-del-registro-nacional-de-proveedores-rnp).
- **SRC-GOV-SUPPLIER** — OECE, [orientación para contratar con el Estado](https://www.gob.pe/institucion/oece/campa%C3%B1as/118018-quieres-contratar-con-el-estado).
- **SRC-GOV-RNP-UPDATE** — OECE, [actualización de información legal ante el RNP](https://www.gob.pe/10212-actualizar-informacion-legal-ante-el-rnp).
- **SRC-GOV-SEACE** — OECE, [acceso de proveedores al SEACE](https://www.gob.pe/7506-acceder-como-proveedor-al-sistema-electronico-de-contrataciones-del-estado-seace).
- **SRC-GOV-PARTICIPANTS** — OECE, [participantes de las contrataciones públicas](https://www.gob.pe/32200-quienes-participan-en-las-contrataciones).
- **SRC-GOV-SANCTIONS** — OECE, [relación de proveedores sancionados para contratar con el Estado](https://www.gob.pe/689-relacion-de-proveedores-sancionados-para-contratar-con-el-estado).
- **SRC-GOV-LAW-DIRECTIVES** — OECE, [directivas vigentes bajo la Ley N.º 32069](https://www.gob.pe/institucion/osce/colecciones/66212-directivas-vigentes-ley-n-32069).

### 20.11. Comercio exterior y aduanas

- **SRC-VUCE** — VUCE/MINCETUR, [Ventanilla Única de Comercio Exterior](https://www.vuce.gob.pe/).
- **SRC-CUSTOMS-REQUIREMENTS** — SUNAT Aduanas, [requisitos de importación simplificada](https://www.sunat.gob.pe/orientacionaduanera/despsimpimportacion/requisitos.html).
- **SRC-CUSTOMS-IMPORT** — SUNAT Aduanas, [procedimiento general de importación para el consumo](https://www.sunat.gob.pe/legislacion/procedim/despacho/importacion/importac/procGeneral/despa-pg.01.htm).
- **SRC-CUSTOMS-RESTRICTED** — SUNAT Aduanas, [mercancías restringidas y prohibidas](https://www.sunat.gob.pe/orientacionaduanera/mercanciasrestringidas/).
- **SRC-CUSTOMS-AGENT** — SUNAT Aduanas, [intervención de agente de aduana según valor FOB](https://asistenteaduanero.sunat.gob.pe/valor-fob-mayor-a-2000).
- **SRC-CUSTOMS-OCE** — SUNAT Aduanas, [autorización y registro de operadores de comercio exterior](https://www.sunat.gob.pe/legislacion/procedim/despacho/operadores/procGeneral/despa-pg.24.htm).
- **SRC-CUSTOMS-OPERATIONS** — SUNAT Aduanas, [portal de operatividad aduanera](https://www.sunat.gob.pe/operatividadaduanera/).
- **SRC-CUSTOMS-VALUATION** — SUNAT Aduanas, [procedimiento específico de valoración de mercancías](https://www.sunat.gob.pe/legislacion/procedim/despacho/importacion/importacA/procEspecif/inta-pe-01-10a.htm).

### 20.12. Propiedad intelectual, productividad y crecimiento

- **SRC-IP-MARK** — Indecopi, [registro de marca de productos o servicios](https://www.gob.pe/333-registrar-la-marca-de-producto-o-servicio-de-tu-negocio-en-indecopi).
- **SRC-IP-MYPE-GUIDE** — Indecopi, [guía de gestión de la propiedad intelectual para MYPE](https://escuela.indecopi.gob.pe/images/publicaciones/pdf/2021/EBOOK_GESTIN_DE_LA_PROPIEDAD_INTELECTUAL_PARA_MYPES_ASPECTOS_CLAVES.pdf).
- **SRC-IP-INVENTOR** — Indecopi, [guía y recursos para proteger invenciones](https://repositorio.indecopi.gob.pe/backend/api/core/bitstreams/c10251b6-cb35-48ab-a5c4-18f64308796f/content).
- **SRC-GROW-CITE** — ITP/PRODUCE, [servicios de los Centros de Innovación Productiva y Transferencia Tecnológica](https://www.gob.pe/959-centros-de-innovacion-productiva-y-transferencia-tecnologica-cite-servicios).
- **SRC-GROW-PROINNOVATE** — ProInnóvate, [programas e instrumentos de innovación empresarial](https://www.gob.pe/proinnovate).

### 20.13. Portales oficiales para los módulos sectoriales

Estos portales son puntos de entrada para activar una revisión sectorial; por sí solos no constituyen una lista exhaustiva de permisos. El módulo especializado debe bajar a la ley, reglamento, TUPA, autoridad territorial, clase de producto, instalación, proyecto y título habilitante aplicables.

- **SRC-SECTOR-IPRESS** — SUSALUD, [inscripción de una IPRESS en RENIPRESS](https://www.gob.pe/10194-inscribir-tu-institucion-prestadora-de-servicios-de-salud-en-el-renipress).
- **SRC-SECTOR-DIGEMID** — DIGEMID, [establecimientos farmacéuticos y autorizaciones sanitarias](https://www.digemid.minsa.gob.pe/webDigemid/establecimientos/).
- **SRC-SECTOR-DIGESA** — DIGESA, [habilitación y certificación sanitaria de alimentos y otros productos](https://www.digesa.minsa.gob.pe/dhaz/certificacion.asp).
- **SRC-SECTOR-SANIPES** — SANIPES, [portal de sanidad e inocuidad pesquera y acuícola](https://www.gob.pe/sanipes).
- **SRC-SECTOR-SENASA** — SENASA, [servicios, consultas y trámites agrarios y pecuarios](https://www.senasa.gob.pe/senasa/servicio-de-consultas-y-tramites/).
- **SRC-SECTOR-SERFOR** — SERFOR, [trámites y servicios forestales y de fauna silvestre](https://www.gob.pe/institucion/serfor/tramites-y-servicios).
- **SRC-SECTOR-SBS-AUTH** — SBS, [tipos de autorización para nuevas empresas supervisadas](https://www.sbs.gob.pe/autorizacion-de-nuevas-empresas/tipos-de-autorizacion).
- **SRC-SECTOR-SBS-OTHER** — SBS, [registros de casas de cambio, préstamos y empeños](https://www.sbs.gob.pe/supervisados-y-registros/registros/otros-registros/casas-de-cambio-prestamos-y-empenos).
- **SRC-SECTOR-SMV** — SMV, [portal institucional del mercado de valores](https://www.smv.gob.pe/).
- **SRC-SECTOR-TELECOM** — MTC, [directorio de concesionarios de servicios públicos de telecomunicaciones](https://www.gob.pe/institucion/mtc/informes-publicaciones/322450-directorio-de-concesionarios-publicos).
- **SRC-SECTOR-MTC** — MTC, [trámites y servicios de transportes y comunicaciones](https://www.gob.pe/institucion/mtc/tramites-y-servicios).
- **SRC-SECTOR-MINEM** — MINEM, [trámites de minería y energía](https://www.gob.pe/institucion/minem/tramites).
- **SRC-SECTOR-OSINERGMIN** — Osinergmin, [portal de supervisión de energía, hidrocarburos y minería](https://www.gob.pe/osinergmin).
- **SRC-SECTOR-PRODUCE** — PRODUCE, [trámites y servicios de industria, pesca, acuicultura y MYPE](https://www.gob.pe/institucion/produce/tramites-y-servicios).
- **SRC-SECTOR-TRANSPORT** — SUTRAN, [documentos de porte y condiciones del transporte de personas y mercancías](https://www.gob.pe/institucion/sutran/noticias/1006673-sutran-conoce-los-documentos-de-porte-obligatorio-y-otras-condiciones-exigidas-en-el-servicio-de-transporte-de-personas-y-de-mercancias).
- **SRC-SECTOR-DGAC** — MTC/DGAC, [empresas certificadas o autorizadas por la autoridad aeronáutica](https://www.gob.pe/institucion/mtc/colecciones/288-empresas-certificadas-y-o-autorizadas-por-la-dgac).
- **SRC-SECTOR-APN** — Autoridad Portuaria Nacional, [licencias de operación portuaria](https://www.gob.pe/institucion/apn/tema/licencias-de-operacion).
- **SRC-SECTOR-TOURISM** — MINCETUR, [trámites y servicios de comercio exterior y turismo](https://www.gob.pe/institucion/mincetur/tramites-y-servicios).
- **SRC-SECTOR-TOURISM-DIRECTORY** — MINCETUR, [Directorio Nacional de Prestadores de Servicios Turísticos Calificados](https://consultasenlinea.mincetur.gob.pe/directoriodeserviciosturisticos/).
- **SRC-SECTOR-MINEDU** — MINEDU, [recursos y autorizaciones de instituciones educativas privadas de educación básica](https://www.gob.pe/institucion/minedu/colecciones/13258-recursos-de-instituciones-educativas-privadas-de-educacion-basica).
- **SRC-SECTOR-SUNEDU** — SUNEDU, [licenciamiento institucional de universidades](https://www.sunedu.gob.pe/licenciamiento-institucional/).
- **SRC-SECTOR-SUCAMEC** — SUCAMEC, [trámites y servicios sobre seguridad privada, armas, explosivos y materiales relacionados](https://www.gob.pe/institucion/sucamec/tramites-y-servicios).
- **SRC-SECTOR-GAMBLING** — MINCETUR, [consultas y autorizaciones de casinos y máquinas tragamonedas](https://consultasenlinea.mincetur.gob.pe/caSiNos/index.html).
- **SRC-SECTOR-GAMBLING-ONLINE** — MINCETUR, [autorización y supervisión de juegos y apuestas deportivas a distancia](https://apuestasdeportivas.mincetur.gob.pe/).
- **SRC-SECTOR-ANA** — Autoridad Nacional del Agua, [trámites y servicios sobre derechos de uso, vertimientos y obras hídricas](https://www.gob.pe/institucion/ana/tramites-y-servicios).
- **SRC-SECTOR-IQBF** — SUNAT, [Registro para el Control de Bienes Fiscalizados](https://orientacion.sunat.gob.pe/21-registro-para-el-control-de-los-bienes-fiscalizados).

## 21. Cierre de alcance y advertencia de uso

Esta matriz es una **arquitectura de determinación y una línea base transversal para empresas en Perú**, no una opinión legal individual ni una garantía de cumplimiento. La aplicabilidad final puede cambiar por norma especial, convenio colectivo, contrato, resolución administrativa, municipio, región, tipo de producto, instalación, proyecto, operación o fecha.

Antes de mostrar una conclusión definitiva al cliente:

1. comprobar que todos los hechos requeridos por la regla están completos;
2. ejecutar la versión correspondiente a la fecha evaluada;
3. verificar la fuente primaria y su vigencia;
4. distinguir obligación, recomendación y oportunidad de crecimiento;
5. remitir excepciones y rutas sectoriales a revisión profesional;
6. conservar evidencia del hecho, de la regla y de la fuente que produjo el resultado.

El valor del sistema no es prometer que una lista genérica contiene todas las obligaciones. Su valor es **hacer visibles los hechos que faltan, enrutar correctamente la especialidad, explicar cada coincidencia y mantener un historial auditable de por qué una empresa debía —o no debía— cumplir una responsabilidad en una fecha determinada**.
