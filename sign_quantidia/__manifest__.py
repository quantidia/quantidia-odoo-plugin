# -*- coding: utf-8 -*-
{
    "name": "Quantidia Sign",
    "version": "19.0.2.39.0",
    "summary": "Integracion Quantidia como mecanismo de firma digital dentro de Odoo Sign",
    "description": """
Quantidia Sign
==============

Integra Quantidia (firma digital PAdES) con Odoo Sign.

Modos:
- Replace: con un solo firmante digital, la firma electronica nativa
  se reemplaza por la firma digital Quantidia.
- Augment: ciclo nativo + ronda digital SERIAL multi-firmante; cada
  firmante suma su firma criptografica sobre el PDF acumulado.

Incluye:
- Odoo como Identity Provider frente a Quantidia (OAuth/OIDC + RFC 7662)
- Endpoint de introspeccion de tokens `/oauth/introspect`
- Emision de access_token de corta duracion para el flujo de firma
- Ronda digital serial con turnos, rechazo, reinicio y cancelacion
- Portal de firma para firmantes externos (con o sin usuario de Odoo)
- Solicitudes ad hoc multi-PDF (adjuntos, documentos o actividad)
- Posicionamiento de firmas: campo de template "Quantidia" o grilla automatica
- Notificaciones de turno y de cierre con PDFs firmados adjuntos
    """,
    "author": "Quantidia Integration",
    "website": "https://github.com/quantidia/quantidia-sdk-js",
    "license": "LGPL-3",
    "category": "Services/Sign",
    # sign: circuito nativo + chatter. documents: acción masiva ad hoc.
    "depends": ["sign", "documents"],
    "external_dependencies": {
        "python": [],
    },
    "data": [
        "security/groups.xml",
        "security/ir.model.access.csv",
        "data/ir_config_parameter.xml",
        "data/mail_activity_type.xml",
        "data/sign_item_type_data.xml",
        "views/sign_request_views.xml",
        "views/sign_request_item_views.xml",
        "views/quantidia_signer_views.xml",
        "views/quantidia_digital_signature_views.xml",
        "views/sign_send_request_views.xml",
        "views/quantidia_adhoc_sign_wizard_views.xml",
        "views/sign_portal_templates.xml",
        "views/sign_templates.xml",
        "views/mail_templates.xml",
        "views/sign_access_templates.xml",
        "views/sign_quantidia_portal_templates.xml",
        "views/res_partner_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "sign_quantidia/static/src/js/sign_quantidia_core.js",
            "sign_quantidia/static/src/js/sign_quantidia_chatter.js",
            "sign_quantidia/static/src/js/sign_quantidia_header_button.js",
            "sign_quantidia/static/src/js/sign_quantidia_public_sign.js",
            "sign_quantidia/static/src/js/sign_quantidia_thankyou_dialog.js",
            "sign_quantidia/static/src/js/signer_x2many.js",
            "sign_quantidia/static/src/xml/sign_quantidia_chatter.xml",
            "sign_quantidia/static/src/xml/signer_x2many.xml",
            "sign_quantidia/static/src/xml/sign_item_custom_popover.xml",
            "sign_quantidia/static/src/xml/sign_activity_patch.xml",
            "sign_quantidia/static/src/xml/sign_items.xml",
            "sign_quantidia/static/src/scss/sign_quantidia.scss",
        ],
        # La pagina de firma PORTAL/PUBLICA (template sign._doc_sign) carga este
        # bundle. La pagina de firma de usuario BACKEND usa web.assets_backend
        # (client action sign.SignableDocument), por eso public_sign va en AMBOS.
        "sign.assets_public_sign": [
            "sign_quantidia/static/src/js/sign_quantidia_core.js",
            "sign_quantidia/static/src/js/sign_quantidia_public_sign.js",
            "sign_quantidia/static/src/js/sign_quantidia_thankyou_dialog.js",
        ],
        # web.assets_frontend: sin assets propios. El portal publico (D9) usa
        # la pagina QWeb /quantidia/sign/<id>/<token> con script inline y el
        # bundle sign.assets_public_sign para la pagina de firma nativa.
    },
    "installable": True,
    "application": False,
    "auto_install": False,
    "post_init_hook": "post_init_hook",
}
