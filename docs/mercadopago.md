# Pagos con Mercado Pago (Checkout Pro)

El flujo: la persona elige plan en `/suscripcion` → el API crea una *preferencia* → se la
lleva a Mercado Pago a pagar (tarjeta, Yape, PagoEfectivo…) → vuelve a `/suscripcion?estado=ok`
→ Mercado Pago avisa por **webhook** → el API consulta el pago por id y, si está aprobado,
activa el plan. El plan **nunca** se activa por la vuelta del navegador: solo por el webhook
verificado (o por el admin, en modo manual).

## 1. Credenciales

1. Entra a <https://www.mercadopago.com.pe/developers> → **Tus integraciones** → **Crear aplicación**
   (tipo: pagos online, Checkout Pro).
2. En la app: **Credenciales de prueba** → copia el **Access Token** (`TEST-…`) para probar;
   **Credenciales de producción** → `APP_USR-…` cuando vayas en serio.
3. Pega en `empresario-api/.env`:

   ```
   MERCADOPAGO_ACCESS_TOKEN=TEST-xxxxxxxx
   ```

   Con el token presente la pasarela pasa a ser Mercado Pago automáticamente
   (`BILLING_GATEWAY` puede quedar vacío). Reinicia `runserver`.

## 2. URLs de retorno y webhook

Se deducen solas del **origen desde el que se paga** (la URL del túnel en desarrollo, tu
dominio en producción): como Next hace proxy de `/api/*`, ese mismo origen recibe el webhook
en `…/api/billing/webhook/mercadopago/`. Solo si el API vive en otro dominio define
`API_PUBLIC_URL=https://api.tudominio.pe`.

El webhook va en cada preferencia (`notification_url`), así que **no hace falta configurarlo en
el panel**. Si además quieres validar la firma, en la app de Mercado Pago → **Webhooks** →
configura la URL y copia la *clave secreta* en `MERCADOPAGO_WEBHOOK_SECRET`.

> En desarrollo el webhook tiene que poder entrar: con el túnel de Cloudflare entra. Con
> `localhost` a secas, no (el pago se aprobaría en Mercado Pago pero el API no se enteraría
> hasta que alguien lo apruebe en el admin).

## Suscripciones (cobro recurrente)

Los planes con **cobro recurrente** (`Plan.recurring`, activo en mensual y anual) no usan
Checkout Pro sino **Suscripciones de Mercado Pago** (`/preapproval`): la persona autoriza su
tarjeta una vez y Mercado Pago cobra solo cada mes (o cada 12 meses en el anual). Cada cobro
llega por webhook y alarga la vigencia; «Cancelar renovación» en `/suscripcion` llama a
`PUT /preapproval/{id}` con `status=cancelled` y lo pagado sigue vigente hasta su fin.

Dos cosas a tener en cuenta:

1. **El webhook de suscripciones se configura en el panel** (a diferencia de Checkout Pro, la
   preapproval no lleva `notification_url`): Tus integraciones → tu app → **Webhooks** → URL
   `https://TU-ORIGEN/api/billing/webhook/mercadopago/` → eventos **Pagos** y **Planes y
   suscripciones**. El API entiende `payment`, `subscription_preapproval` y
   `subscription_authorized_payment`.
2. `payer_email` tiene que ser el correo del usuario que paga; con credenciales de prueba
   debe ser un **usuario de prueba comprador** (Tus integraciones → Cuentas de prueba), y se
   entra a Mercado Pago con ese usuario para autorizar.

Si prefieres que el anual sea un pago único sin renovación, desmarca «cobro recurrente» en
Admin → Planes → Anual.

## 3. Probar

Con credenciales de prueba, paga con las tarjetas de prueba de Mercado Pago
(<https://www.mercadopago.com.pe/developers/es/docs/checkout-pro/additional-content/your-integrations/test/cards>),
p. ej. Visa `4009 1753 3280 6176`, CVV `123`, fecha futura, nombre `APRO` (aprobado) o `OTHE`
(rechazado). El correo del pagador debe ser el de un **usuario de prueba comprador** de tu app
(Tus integraciones → Cuentas de prueba).

## 4. Qué mirar si algo falla

- `POST /api/billing/checkout/` → **502** «No pudimos iniciar el pago»: token inválido o sin
  permisos. El detalle va en el log del API.
- Pagó pero sigue en prueba: el webhook no llegó (URL no pública). El pago queda `pending` en
  **Admin → Pagos**; al recibir el webhook se aprueba solo, o apruébalo a mano con la acción
  «Aprobar».
- Todo pago lleva `external_reference` = id del `Payment`; el webhook solo aprueba si Mercado
  Pago confirma `status=approved` para esa referencia (consulta con tu token).
