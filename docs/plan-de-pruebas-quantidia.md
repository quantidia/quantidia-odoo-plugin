# Plan de pruebas — Integración Quantidia + Odoo Sign

Este documento describe, paso a paso, cómo probar la integración de firma
digital Quantidia dentro de Odoo Sign. Está pensado para ejecutarse desde
cero: no requiere conocimiento del código ni del historial del proyecto.

Al final de cada prueba se indica **qué se considera exitoso**. Si algo falla,
registrar el número de prueba, el paso y una captura (ver "Cómo reportar
resultados").

---

## 1. Alcance

La integración permite firmar documentos PDF con firma digital Quantidia
desde tres puntos de entrada de Odoo:

1. **Solicitud desde un template de negocio** (flujo estándar de Odoo Sign).
2. **Solicitud ad hoc** sobre PDFs sueltos (adjuntos existentes o subidos).
3. **Enlace de firma** que llega por correo (portal web, con o sin usuario
   de Odoo).

Conceptos que se usan en este plan:

- **Firma electrónica**: la que hace Odoo Sign nativo (el firmante completa
  campos: nombre, fecha, firma dibujada, etc.).
- **Firma digital**: la criptográfica que aplica Quantidia (PAdES) sobre el
  PDF. Es la que se prueba acá.
- **Ronda digital**: secuencia de firmas digitales, una por firmante, en
  orden (primero firma el 1, después el 2, etc.).
- **Recuadro de firma**: posición y tamaño donde el SDK de Quantidia estampa
  cada firma dentro del PDF. Cada firmante recibe su propio recuadro para
  que las firmas no se superpongan.

## 2. Preparación del entorno (una sola vez)

### 2.1 Instalación

- Odoo 19 con los módulos **Sign** (enterprise) y **sign_quantidia**
  (este módulo) instalados.
- Verificar en *Apps* que "Quantidia Sign" figure como **Instalado**.

### 2.2 Configuración de Quantidia

Ir a **Ajustes → Quantidia Sign** y completar:

| Campo | Valor para probar |
|---|---|
| SDK baseUrl | provisto por Quantidia (entorno test) |
| SDK apiBase | provisto por Quantidia |
| Environment ID | provisto por Quantidia |
| Company ID / Agency ID | provisto por Quantidia (si aplica) |
| SDK Api-Key | **la public key del SDK** (confirmar con Quantidia; NO es el secret del servidor) |
| Modo de autenticación | **Simple (usuario/clave test)** para este plan, salvo que se quiera probar SSO |
| Usuario / Password de test | credenciales de prueba provistas por Quantidia |

> Nota: el modo Simple es solo para testing. El modo SSO requiere que
> Quantidia esté apuntando al endpoint de introspección de Odoo.

### 2.3 Contactos firmantes

Para cada persona que va a firmar:

1. **Contactos**: crear o verificar que exista el contacto, con **email
   válido** (llegan ahí las notificaciones).
2. **Perfil Quantidia**: en el contacto, sección/pestaña Quantidia, crear el
   perfil de firmante ("Firma digital Quantidia") y dejarlo activo. Sin
   este perfil, el contacto no puede ser seleccionado como firmante digital.

Recomendado para el plan: preparar 4 contactos con perfil:
- 1 usuario interno de Odoo (con usuario y contraseña de Odoo) — "Firmante interno"
- 3 externos sin usuario de Odoo — "Externo A", "Externo B", "Externo C"

### 2.4 PDFs de prueba

Tener a mano:

- Un PDF simple de 1 página ("contrato.pdf").
- Un PDF de 2 o más páginas ("multipagina.pdf").
- Dos o tres PDFs distintos para la prueba multi-PDF.

> Los PDFs no deben estar encriptados ni protegidos con contraseña.

## 3. Cómo verificar los resultados

Durante las pruebas se usan tres lugares de verificación:

1. **El documento de firma en Odoo** (menú *Firma → Documentos*): estado
   general, lista de firmantes (pasan a verde al firmar), historial
   (chatter) con cada evento.
2. **El PDF firmado**: botón de descarga del PDF firmado en el documento
   → abrirlo y verificar visualmente **dónde quedó cada firma** y que todas
   las firmas estén presentes (solapadas = falla).
3. **La consola del navegador** (F12) durante la firma: buscar la línea
   `[quantidia] openSigningWithLogin params:` — muestra entre otros datos el
   `signatureRect` que se le pasa al SDK (página, x, y, ancho, alto). Sirve
   para confirmar que cada firmante recibe **una posición distinta**.

Estados del documento que aparecen en Odoo:

| Estado | Significado |
|---|---|
| Por firmar | todavía no arrancó la ronda digital |
| Firma iniciada | ronda digital en curso (alguien debe firmar) |
| Firmado (Quantidia) | todos firmaron digitalmente |
| Cancelado | alguien rechazó o se canceló el proceso |

---

## 4. Pruebas

### P1 — Firma simple con un solo firmante (flujo básico)

**Qué valida**: el circuito mínimo de punta a punta.

1. Crear una solicitud de firma desde un template con **contrato.pdf**
   (sin campos) o usar el wizard ad hoc (ver P13) con 1 firmante.
2. El firmante abre la firma desde el enlace del correo (o el usuario
   interno desde el botón **"Firmar documentos"** del encabezado del
   pedido — visible solo cuando es su turno).
3. Se abre el modal del SDK de Quantidia con el PDF cargado.
4. Firmar en el SDK con la firma Cloud/keystore de test.
5. Volver a Odoo y abrir el documento.

**Exitoso si**:
- El modal abre con el PDF correcto.
- El documento queda en estado **Firmado**.
- El PDF descargable desde el documento es el **firmado digitalmente**
  (verificar la firma en un lector de PDF).
- El chatter registra la firma.

---

### P2 — Multi-firmante en ronda serial (sin campos)

**Qué valida**: la ronda uno-por-uno y el avance de turnos.

1. Crear una solicitud con **contrato.pdf** y 3 firmantes (interno +
   Externo A + Externo B), todos con perfil Quantidia.
2. Al enviarse la solicitud, arranca la ronda: **solo el primer firmante
   puede firmar**.
3. Verificar que al firmante 2 y 3 les llega el aviso de que aún no es su
   turno si intentan abrir la firma (y que NO pueden firmar).
4. Firmante 1 firma desde su enlace → cuando completa **todos los
   documentos** de su grupo (la ronda avanza con el último upload, no con
   el primero), el firmante 2 recibe el mail de que llegó su turno.
5. Firmante 2 firma → firmante 3 firma.

**Exitoso si**:
- El orden se respeta (nadie firma fuera de turno).
- Cada firmante pasa a verde en la lista al firmar.
- Al firmar el último, el documento pasa a **Firmado (Quantidia)** y llega
  el mail de cierre **con todos los PDFs firmados adjuntos** (principal +
  anexos) a todos los firmantes.
- El PDF final contiene las **3 firmas digitales**.

---

### P3 — Posicionamiento automático: grilla de 3 por línea

**Qué valida**: que las firmas no se superpongan cuando el PDF no tiene
marcas de posición.

1. Crear una solicitud con **contrato.pdf** y **4 firmantes** (sin template
   con campos, ej. wizard ad hoc).
2. Los 4 firman en orden (P2).

**Exitoso si** al abrir el PDF final:
- Las firmas están en el **final del documento** (última página, zona
  inferior).
- Se acomodan **3 por línea**: los primeros 3 en una línea (izquierda a
  derecha) y el 4to arriba a la izquierda de la línea siguiente.
- Ninguna firma se superpone con otra ni se corta fuera de la página.

---

### P4 — Posicionamiento con el campo "Quantidia"

**Qué valida**: que el usuario pueda elegir dónde firma cada rol.

1. Crear o editar un **template de firma** en Odoo (*Firma → Plantillas*)
   con **contrato.pdf**.
2. En el editor de plantillas, en el panel de campos del firmante, buscar
   el tipo de campo **"Quantidia"** y arrastrarlo al PDF:
   - Colocar uno para el rol del firmante 1 en el medio de la página 1.
   - (Opcional) colocar otro más abajo para el firmante 2.
3. Guardar el template y crear una solicitud con 2 firmantes
   (asignando los roles correspondientes).
4. Ambos firman.

**Exitoso si**:
- Durante la carga del template, el campo se puede arrastrar y redimensionar
  como cualquier otro campo (y el checkbox "campo obligatorio" no aparece
  para este tipo de campo).
- En el PDF final, la firma del firmante 1 quedó **dentro del recuadro que
  se colocó en el template** (misma página y posición).
- Si había un segundo campo, la firma del firmante 2 quedó en ese segundo
  recuadro; si no había, la firma 2 se acomoda en la grilla debajo del
  campo colocado.
- Verificación técnica opcional: en la consola del navegador, los
  `signatureRect` de cada firmante son distintos y coinciden con lo visible.

---

### P5 — Multi-PDF: principal + anexos en una sola acción ⭐ (VERIFICADA)

**Qué valida**: que el SDK firma **varios PDFs en una sola acción de firma**.
Verificada en el entorno demo (3 PDFs firmados en una acción, orden de
`listSignedDocuments` correcto).

1. Crear una solicitud ad hoc (ver P13) seleccionando **2 o 3 PDFs**
   (el primero es el "principal", el resto "anexos"), con 1 firmante.
2. El firmante abre el modal del SDK.

**Exitoso si**:
- El modal muestra **todos los PDFs** de la solicitud.
- Al firmar, se firman **todos** en la misma acción.
- En Odoo, el documento queda con:
  - el PDF **principal** firmado (es el que se descarga del documento), y
  - cada anexo firmado guardado como archivo de la solicitud
    (se ven mencionados en el historial/chatter).
- Al descargar cada anexo, contiene la firma digital.

> **Punto de atención para Quantidia**: al terminar la firma, Odoo asume que
> el SDK devuelve los documentos firmados **en el mismo orden** en que se
> cargaron (el primero es el principal). Si en la prueba el orden resultara
> otro, informarlo: hay que ajustar el lado de Odoo.

---

### P6 — Multi-firma acumulativa sobre el mismo PDF ⭐ (VERIFICADA ✅)

**Qué valida**: que cada firmante **suma** su firma sobre el PDF ya firmado
por el anterior. **Verificada (2026-09-30)** en rondas reales de 2 firmantes
sobre solicitudes multi-PDF: las firmas se acumulan en **todos** los PDFs
(principal y anexos), cada uno con las dos firmas. El firmante N+1 recibe
del SDK la versión firmada por N (no el original).

1. Repetir **P5 pero con 2 firmantes** (o P2 con 2 firmantes).
2. Firmante 1 firma → firmante 2 abre su turno.

**Exitoso si**:
- El SDK **acepta firmar** el PDF que ya tiene la firma del firmante 1
  (no lo rechaza).
- El PDF final tiene **las 2 firmas** visibles y ambas válidas como firmas
  digitales (cada una en su recuadro, ver P3/P4).
- En solicitudes multi-PDF, **cada anexo** termina con las firmas de todos
  (una sola versión por anexo, sin copias paralelas).
- Ningún paso de la ronda falla con "el PDF no pudo ser firmado".

---

### P7 — Firma desde el portal (externos)

**Qué valida**: los enlaces de firma sin sesión de Odoo.

1. En una ronda activa (P2), el Externo A abre el enlace de su mail en una
   ventana **sin sesión** de Odoo (o navegación privada).
2. Ve la página de firma de Quantidia con el PDF a firmar y su estado de
   turno.
3. Firma desde ahí.

**Exitoso si**:
- La página muestra el PDF correcto y el estado ("es tu turno").
- La firma se completa y la página refleja el avance.
- Un externo **sin turno** (firmante posterior) que abre su enlace ve el
  estado "aún no es tu turno" y **sin botón de firma**.

Variante (opcional): con el ajuste "Exigir login al firmante con usuario"
activo, un firmante **con** usuario de Odoo debe iniciar sesión con SU
usuario antes de firmar; otro usuario logueado distinto debe ser bloqueado.

---

### P8 — Rechazo de firma y reinicio

**Qué valida**: el circuito de rechazo.

1. En una ronda de 2+ firmantes, el firmante con turno **rechaza** en el
   modal del SDK (cancelar con motivo si el SDK lo ofrece).
2. Intentar que otro firmante firme.

**Exitoso si**:
- La ronda se **cancela** (estado Cancelado) y el resto no puede firmar.
- El historial registra el motivo del rechazo.
- Usando la acción **reiniciar ronda** sobre el documento (usuario con
  permisos), solo los firmantes **que no firmaron** vuelven a ser
  notificados y pueden firmar; los que ya firmaron no repiten.

---

### P9 — Cancelación definitiva

1. Sobre un documento con ronda activa, usar el botón **Cancelar** nativo
   del documento (único botón de cancelación: cierra también la ronda
   digital).
2. Verificar el documento y los enlaces de los firmantes.

**Exitoso si**: el documento queda cancelado, nadie puede firmar, la ronda
digital queda cerrada (estado Cancelado en el badge de firma) y queda
registrado en el historial.

---

### P10 — Anexos agregados por el chatter

1. Crear una solicitud normal (1 firmante) con **contrato.pdf**.
2. **Antes de que firme**, adjuntar otro PDF al historial (chatter) del
   documento.
3. El firmante firma.

**Exitoso si**: el modal incluye también el PDF adjuntado al chatter y
ambos quedan firmados (mismo criterio que P5).

---

### P11 — Seguridad (validaciones server-side)

**Qué valida**: que no se pueda bypasear el flujo desde afuera de la UI.

1. **Validación electrónica bloqueada**: en un documento con ronda digital
   activa, intentar completar la firma electrónica nativa de Odoo (la
   pantalla clásica "Validar y enviar documento completado"). También sirve
   probar en la consola del navegador llamando a la acción nativa.
   - *Exitoso si*: la operación es interceptada y, ante un intento directo,
     Odoo rechaza con el mensaje de que el documento se firma con Quantidia.
     El firmante NO queda marcado como firmado sin su firma digital.
2. **Firma fuera de turno**: con la ronda activa, que un firmante SIN turno
   intente subir una firma (p. ej. reutilizando una sesión anterior).
   - *Exitoso si*: Odoo rechaza la subida ("no es tu turno").
3. **Reuso de sesión de firma**: después de firmar exitosamente, reintentar
   la misma operación de subida una segunda vez.
   - *Exitoso si*: la segunda subida es rechazada (la sesión de firma es de
     un solo uso).

> Estas tres pruebas son de "intento de romper": si el sistema se comporta,
   fallan elegante con mensajes claros y el estado del documento no cambia.

---

### P12 — Notificaciones

Durante P1–P8 ya se ejercitan; verificar al cierre:

1. Mail de **turno** de Quantidia a cada firmante cuando le toca (con
   enlace al portal). El mail nativo de Odoo ("Su Firma") NO debe llegar:
   ni en la creación ni durante la ronda.
   El asunto y el nombre del documento usan el **resumen de la actividad**
   origen cuando la solicitud se creó desde una.
2. Para internos con usuario: además aparece una **actividad/tarea** en su
   bandeja de Odoo.
3. Mail de **cierre** al completar la ronda, con **todos los PDFs firmados
   adjuntos** (principal + anexos).
4. Mail de rechazo/cancelación según corresponda.

**Exitoso si**: llegan los 3 tipos de mail, con los enlaces correctos y los
PDFs firmados adjuntos en el de cierre.

---

### P13 — Solicitud ad hoc sobre PDFs sueltos

**Qué valida**: firmar sin template de negocio.

Probar las tres entradas:

1. **Desde adjuntos**: seleccionar PDFs → acción "Firmar con Quantidia".
2. **Desde el módulo Documentos**: seleccionar documentos → misma acción.
3. **Desde una actividad de firma** en cualquier registro con chatter.

En el wizard: elegir los PDFs, agregar 1+ firmantes (solo aparecen
contactos con perfil Quantidia), definir el orden y enviar.

**Exitoso si**:
- El wizard valida: exige PDF, firmante con perfil y con email, sin
  repetidos.
- Se crea el documento de firma y arranca la ronda digital igual que P2.
- La **referencia** del pedido (y los mails) usan el resumen de la
  actividad; con anexos agrega "(+N anexo/s)".
- El orden de firmantes definido en el wizard se respeta en la ronda.
- Si viene de actividad, la actividad queda cerrada con el comentario de
   la solicitud creada.
- Al completar la ronda, el registro **origen** (contacto, factura, ...)
  recibe en su chatter **todos los PDFs firmados** (principal + anexos,
  cada uno con todas las firmas) en un solo mensaje.
- En el formulario del pedido: bloque **"Archivos a firmar"** visible con
  todos los PDFs y botón "Ver"; en multi-PDF el botón **Descargar** entrega
  un ZIP con todo lo firmado y "Vista previa" no aparece.

---

## 5. Orden sugerido de ejecución

1. Preparación (sección 2).
2. P1 (básico) → P2 (serial) → P3 (grilla) → P4 (campo de posición).
3. **P5 (multi-PDF)** y **P6 (acumulativa)** — ambas ⭐ ya verificadas en
   el entorno demo; re-ejecutar como regresión tras cambios del SDK.
4. P7 (portal) → P8 (rechazo) → P9 (cancelación).
5. P10 (anexos chatter) → P13 (wizard ad hoc, si no se usó ya).
6. P11 (seguridad) → P12 (notificaciones).

## 6. Cómo reportar resultados

Por cada prueba, completar:

| Prueba | Paso | Resultado (OK / Falló) | Detalle / captura |
|---|---|---|---|
| P1 | 4 | OK | ... |

Adjuntar siempre:
- captura o PDF final cuando el tema sea **posición de firmas**,
- el texto exacto del error (o captura de consola del navegador, solapa
  Console, líneas `[quantidia]`) cuando algo falle en el modal del SDK.

## 7. Nota sobre pruebas automáticas

El módulo incluye una suite automática de 16 tests (flujos de creación,
posicionamiento, wizard ad hoc y guardas de seguridad) que ya se ejecuta en
 verde sobre Odoo 19. Este plan cubre lo que la suite no puede: la
 interacción real con el SDK y el entorno de Quantidia.
