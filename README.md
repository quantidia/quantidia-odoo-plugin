# quantidia-odoo-plugin

Integración de **Quantidia** (firma digital PAdES) con **Odoo Sign** para Odoo 19.

El módulo `sign_quantidia` incorpora la firma digital de Quantidia dentro de
Odoo Sign: firma directa desde el backend, ronda digital serial
multi-firmante, portal de firma para firmantes externos, solicitudes ad hoc
multi-PDF y posicionamiento de firmas por template o grilla automática.

## Contenido

- `sign_quantidia/`: addon de Odoo (código, vistas, tests, i18n). Ver su
  [README](sign_quantidia/README.md) para el diseño funcional y técnico.
- `docs/plan-de-pruebas-quantidia.md`: protocolo de pruebas de la integración
  (13 pruebas de punta a punta sobre el entorno de test de Quantidia).

## Versión

**19.0.2.39.0** (ver `sign_quantidia/__manifest__.py`).

## Instalación

1. Requiere Odoo 19 con **Sign** (enterprise) instalado.
2. Copiar `sign_quantidia/` al addon path e instalar desde *Apps*
   ("Quantidia Sign").
3. Configurar en *Ajustes → Quantidia Sign*: URLs del SDK, credenciales del
   tenant y modo de autenticación (`sso` con Odoo como Identity Provider, o
   `simple` con credenciales de test). Las credenciales no se versionan: se
   cargan por Settings / variables de entorno.
4. En cada contacto firmante, crear el perfil "Firma digital Quantidia"
   (pestaña Quantidia del contacto).

## Pruebas

La suite automática del módulo (16 tests: flujos de creación, posicionamiento,
wizard ad hoc y guardas de seguridad) corre con el estándar de Odoo:

```bash
odoo-bin -i sign_quantidia --test-enable --stop-after-init
```

La verificación end-to-end contra el SDK y el entorno de Quantidia sigue el
protocolo en `docs/plan-de-pruebas-quantidia.md`.
