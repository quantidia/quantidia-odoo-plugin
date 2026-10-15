# sign_quantidia

Integración de Quantidia (firma digital PAdES) dentro de Odoo Sign. Odoo actúa
como Identity Provider frente a Quantidia (OAuth/OIDC + introspección RFC 7662)
y orquesta dos modos de firma sobre un `sign.request`:

- **Replace (Fase 1)**: con un solo firmante, el SDK de Quantidia reemplaza la
  firma electrónica nativa. Odoo Sign solo aporta el documento y el circuito.
- **Augment (Fase 2)**: circuito nativo completo (carga de datos + firma
  electrónica) y, al terminar, una **ronda digital serial** donde cada
  participante firma criptográficamente el PDF acumulado con Quantidia.

## Flujo Augment (ronda digital serial)

El campo `quantidia_mode` del request decide el modo (`digital` por defecto,
`electronic` desactiva toda la integración).

1. **Ciclo electrónico nativo**: cada firmante completa sus campos y firma
   electrónicamente como en Odoo Sign puro. Nada se intercepta (ver
   `sign_quantidia_public_sign.js`: solo se intercepta con `mode=digital` y un
   único firmante — Replace).
2. **Arranque de la ronda**: al `_sign()` del último firmante electrónico,
   `_start_digital_round` crea un `quantidia.digital.signature` por firmante
   (`sequence` 1..N; el primero queda `pending`, el resto `waiting`) y marca
   `quantidia_digital_round = True`, `quantidia_status = in_progress`.
   Con templates sin campos (Flujo B) corresponde `_start_digital_round_immediate`.
3. **Turnos**: cada firmante firma en su orden. Su firma sube por
   `upload_signed` / `upload_signed_public` → `_quantidia_store_signed_pdf`:
   - valida server-side que el tracking esté `pending` (fuera de turno rechaza),
   - persiste el PDF acumulado (cada firma se suma a la anterior),
   - actualiza el completed document y `quantidia_signed_attachment_id`,
   - marca el tracking `signed` (`action_mark_signed`),
   - `_activate_next_signer` pasa el siguiente a `pending`,
   - `_check_digital_round_complete` cierra la ronda cuando todos firmaron
     (`quantidia_status = signed`).
4. **Rechazo**: si un firmante cancela en el modal de Quantidia, el frontend lo
   reporta a `report_rejected(_public)` → `_cancel_digital_round`: la ronda
   digital se cancela (los demás ya no pueden firmar) pero la firma electrónica
   previa se mantiene.

### Puntos de entrada de la firma digital

| Firmante | Entry point | Flujo |
|---|---|---|
| Interno / backend | Botón **"Firmar anexos"** en el chatter del `sign.request` | `action_prepare_quantidia_sign` → SDK (SSO con JWT del usuario) → `upload_signed` (deriva el item por partner del usuario logueado) |
| Portal / externo | Página **`/quantidia/sign/<request_id>/<item_token>`** | Botón "Firmar digitalmente" → `prepare_public` (JWT del **partner del item**) → SDK → `upload_signed_public` |

La página portal (D9) muestra el **visor del PDF a firmar** (la misma fuente
que usa el SDK: en ronda activa es el último PDF acumulado), el estado del
turno (`turn` / `waiting` / `signed` / `completed` / `cancelled`) y una barra de
progreso. Tras firmar se recarga sola y refleja el nuevo estado. Para externos
sin usuario el token del enlace alcanza; para firmantes con usuario se aplica
el endurecimiento de identidad (ver abajo).

### Fases semánticas

`sign.request._quantidia_sign_phase()` computa la fase como única fuente de
verdad (`electronic`, `data_collection`, `electronic_signing`, `digital_round`,
`complete`, `cancelled`). Los endpoints `sign_progress(_public)` la exponen y el
patch del `ThankYouDialog` muestra el mensaje acorde a la fase en vez del
"recibirás el documento por correo" nativo (que solo es cierto al terminar todo).

### Endurecimiento de identidad (login)

Setting `sign_quantidia.require_portal_login` (activo por defecto). Aplica a la
página de firma nativa y a la página de turno digital: si el firmante del
enlace **tiene usuario activo** en Odoo, el token solo no alcanza — visita
anónima → redirect a `/web/login` con vuelta; logueado como otro usuario → 403.
Firmantes externos sin usuario mantienen el flujo por token.

## UI backend

- **Ribbons**: "Firma digital en proceso" (azul) mientras la ronda está activa,
  "Firma digital cancelada" (rojo) si se canceló; el ribbon nativo "Signed" se
  oculta hasta que la ronda termina (la firma electrónica completada no es el
  final del proceso).
- **Estado fusionado** `quantidia_combined_status` con badge en form / lista /
  kanban; filtros de búsqueda reemplazados (Por firmar / Firma iniciada /
  Firma Quantidia / Firma Odoo / Cancelado).
- Pestaña **"Firma Digital"** con el tracking por firmante (turno, secuencia,
  fecha de firma digital).
- Botones **"Reiniciar ronda digital"** y **"Cancelar proceso digital"**
  (grupos `sign.group_sign_manager` / `group_sign_user_advanced`).

## Configuración

Settings → Quantidia Sign (todo persiste en `ir.config_parameter`):

| Clave | Uso |
|---|---|
| `sign_quantidia.sdk_base_url` / `sdk_api_base` | CDN del SDK y API Quantidia |
| `sign_quantidia.environment_id` / `company_id` / `agency_id` / `api_key` | Tenant y credenciales del SDK |
| `sign_quantidia.auth_mode` | `sso` (Odoo = IdP, recomendado) o `simple` (creds de test) |
| `sign_quantidia.test_username` / `test_password` | Solo modo `simple` |
| `sign_quantidia.oauth_client_id` / `oauth_client_secret` / `introspect_api_key` | OAuth/OIDC + introspección RFC 7662 |
| `sign_quantidia.token_secret` / `token_ttl` | Firma y TTL de los JWT de sesión |
| `sign_quantidia.require_portal_login` | Endurecimiento de identidad en enlaces de firma |

## Endpoints HTTP

| Ruta | Auth | Descripción |
|---|---|---|
| `/oauth/introspect` | Basic + Api-Key | Introspección RFC 7662 (Odoo = IdP) |
| `/sign_quantidia/upload_signed` | user | Subida del PDF firmado (backend) |
| `/sign_quantidia/upload_signed_public` | none (token del item) | Subida del PDF firmado (portal/externo) |
| `/sign_quantidia/report_rejected(_public)` | user / none (token) | Rechazo: cancela la ronda digital |
| `/sign_quantidia/prepare_public` | none (token del item) | Config del SDK + JWT del partner del item; rechaza fuera de turno |
| `/sign_quantidia/get_source_documents(_public)` | user / none (token) | PDFs origen (en ronda: el acumulado) |
| `/sign_quantidia/is_last_signer(_public)` | user / none (token) | Modo y cantidad de firmantes (interceptor Replace) |
| `/sign_quantidia/sign_progress(_public)` | user / none (token) | Fase semántica + avance (ThankYouDialog) |
| `/sign_quantidia/document_pdf/<id>/<token>` | none (token del item) | PDF a firmar, inline (visor de la página D9) |
| `/quantidia/sign/<id>/<token>` | public (+ login si corresponde) | Página portal del turno digital (D9) |

## Frontend

| Archivo | Bundle | Rol |
|---|---|---|
| `sign_quantidia_core.js` | backend + public_sign | Núcleo: carga SDK, PDFs, `openQuantidiaSign`, subida del firmado |
| `sign_quantidia_chatter.js` (+xml) | backend | Botón "Firmar anexos" en el chatter |
| `sign_quantidia_public_sign.js` | public_sign | Interceptor Replace (1 firmante digital) |
| `sign_quantidia_thankyou_dialog.js` | backend + public_sign | Mensaje post-firma según fase |
| `signer_x2many.js` | backend | Columnas de firma digital en la lista de firmantes |

El SDK **no sube el documento solo**: al resolverse `openSigningWithLogin` el
frontend retira los bytes (`getSignedDocumentBytes`) y los postea a
`upload_signed(_public)`.

## Seguridad

- Sesión de firma de **un solo uso**: el `jti` del JWT se consume al firmar;
  reintentar la misma subida es rechazado.
- Guardas server-side en toda la ronda: firma fuera de turno rechazada, firma
  electrónica nativa bloqueada mientras la ronda digital está activa.
- Notificaciones de turno por mail (con enlace al portal) y actividad interna;
  mail de cierre con todos los PDFs firmados adjuntos.

## Desarrollo

- La página pública de firma carga `sign.assets_public_sign`; los cambios de JS
  necesitan upgrade o restart para regenerar el bundle (Ctrl+Shift+R en el
  browser si queda cacheado).

Ver `docs/plan-de-pruebas-quantidia.md` (raíz del repo) para el protocolo de
pruebas de la integración.
