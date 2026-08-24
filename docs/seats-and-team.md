# Asientos de empresa y usuarios por empresa

Dos controles sobre "cuánto cabe en una cuenta":

1. **Cuántas empresas puede administrar un titular** (asientos de empresa).
2. **Qué personas ven cada empresa** (miembros e invitaciones), cada una atada a
   **una sola** empresa.

Ambos se gestionan desde el **Django admin**; el titular también puede sumar
gente a su empresa desde el front.

## Asientos de empresa

La "cuenta" es el **usuario titular**. Su tope de empresas es:

```
límite = Plan.included_company_seats (del plan del titular)  +  User.extra_company_seats
```

- `Plan.included_company_seats` — base por plan (por defecto **3**). Editable en
  *Billing › Planes*.
- `User.extra_company_seats` — asientos extra que **tú** otorgas por cuenta, en
  *Accounts › Usuarios* (columna editable en la lista y en la ficha). Cada
  empresa extra cuesta lo que fije `BILLING_EXTRA_COMPANY_PRICE` (por defecto
  `9`, solo informativo para el front; el cobro no es self-service).
- Si el titular aún no tiene plan (en prueba), la base es
  `BILLING_DEFAULT_COMPANY_SEATS` (por defecto 3).

Cuentan solo las empresas donde la persona es **titular** (`role=OWNER`), no las
que ve como invitada. La resolución del plan del titular reusa
`billing.services._primary_subscription` (la empresa propia más antigua).

**Enforcement**: al crear una empresa (`POST /api/accounts/organizations/`), si
`companies_in_use >= company_seat_limit` la API responde **409** con
`{"code": "limite_empresas", "detail": ..., "seats": {...}}`. El resumen de
asientos viaja también en la sesión y en `/api/accounts/me/` como `seats`.

Helpers en `billing/services.py`: `company_seat_limit(user)`,
`companies_in_use(user)`, `can_add_company(user)`, `seat_summary(user)`.

## Usuarios por empresa (miembros e invitaciones)

El acceso vive en `accounts.Membership` (ya existía). Nuevo: invitar por correo
y administrar roles desde el API, más el modelo `Invitation` para correos que
aún no tienen cuenta.

- **Invitar** (`POST /api/accounts/organizations/members/`, `{email, role}`):
  - Si el correo ya tiene cuenta → `Membership` de inmediato (solo ve esta empresa).
  - Si no → `Invitation` **pendiente**; se convierte en acceso cuando esa persona
    inicia sesión con ese correo (`accept_pending_invitations`, llamado en cada
    `_session_payload`).
- **Listar** (`GET .../members/`): miembros + invitaciones pendientes. Cualquier
  miembro puede ver; solo titular/contador pueden invitar.
- **Cambiar rol** (`PATCH .../members/<id>/`, `{role}`) y **quitar**
  (`DELETE .../members/<id>/`, baja lógica).
- **Revocar invitación** (`DELETE .../invitations/<id>/`).

Reglas: nunca se deja la empresa sin titular; tocar o crear un **titular** exige
que quien lo hace sea titular. Roles: `owner` (Titular), `accountant` (Contador),
`viewer` (Solo lectura); `can_manage = owner|accountant`.

Servicio: `accounts/services/team.py`. Admin: `Invitation` registrado, e inlines
de *Membership* e *Invitation* en la ficha de la Organización.

## Migraciones

- `accounts.0006_*` — `User.extra_company_seats`, `Membership.invited_by`, modelo
  `Invitation`.
- `billing.0006_plan_included_company_seats`.
