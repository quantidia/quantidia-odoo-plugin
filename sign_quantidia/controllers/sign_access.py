# -*- coding: utf-8 -*-
"""Endurecimiento de acceso a la pagina de firma.

Politica (setting `sign_quantidia.require_portal_login`, activada por
defecto): cuando el firmante de un enlace tiene usuario de Odoo (portal o
interno), el enlace solo no alcanza. Si la visita es anonima se redirige al
login (con vuelta al mismo enlace tras autenticarse); si esta logueada como
otro usuario, se bloquea. Firmantes sin usuario (externos) mantienen el flujo
nativo por token: no hay identidad con la que comparar.
"""

import logging
from urllib.parse import quote

from odoo import http
from odoo.http import request

from odoo.addons.sign.controllers.main import Sign

from .common import resolve_item_by_token

_logger = logging.getLogger(__name__)


class SignAccess(Sign):

    def _quantidia_sign_access_response(self, sign_request_id, token):
        """None para seguir el flujo nativo; respuesta si hay que exigir identidad."""
        param = request.env["ir.config_parameter"].sudo().get_param(
            "sign_quantidia.require_portal_login"
        )
        # get_param devuelve False cuando la clave NO existe (default de Odoo):
        # ausencia = politica activa por defecto; solo un valor explicito la apaga.
        if param in ("False", "0", "false"):
            return None

        item, sign_request, err = resolve_item_by_token(token, sign_request_id)
        if err or not item:
            return None  # token invalido: el flujo nativo devuelve su 404

        # Item ya completado: no hay nada que firmar; se conserva el
        # comportamiento nativo (incluye el redirect al backend para internos).
        if item.state != "sent":
            return None

        # Solo se endurece cuando el firmante TIENE usuario activo.
        if not item.partner_id.user_ids.filtered(lambda u: u.active):
            return None

        user = request.env.user
        if user._is_public():
            target = request.httprequest.full_path
            return request.redirect("/web/login?redirect=" + quote(target, safe=""))

        if user.partner_id != item.partner_id:
            _logger.info(
                "quantidia: acceso bloqueado al enlace del firmante %s (item %s) "
                "para el usuario logueado %s",
                item.partner_id.display_name,
                item.id,
                user.login,
            )
            return request.render(
                "sign_quantidia.signer_login_mismatch",
                {
                    "signer_name": item.partner_id.display_name,
                    "current_user": user.display_name,
                },
                status=403,
            )
        return None

    @http.route()
    def sign_document_public(self, sign_request_id, token, **post):
        # Ronda digital activa: la pagina nativa de firma electronica no es
        # la entrada correcta (no lista los anexos ni abre el SDK). Cualquier
        # link nativo (mail viejo, actividad, historial) va al portal
        # Quantidia, que muestra TODOS los documentos y un solo boton.
        item, sign_request, err = resolve_item_by_token(token, sign_request_id)
        if (
            not err
            and item
            and item.state == "sent"
            and sign_request.quantidia_digital_round
        ):
            return request.redirect(
                "/quantidia/sign/%s/%s" % (sign_request_id, token)
            )
        response = self._quantidia_sign_access_response(sign_request_id, token)
        if response:
            return response
        return super().sign_document_public(sign_request_id, token, **post)

    @http.route()
    def sign(self, sign_request_id, token, sms_token=False, signature=None, **kwargs):
        """D0: con ronda digital activa, la unica firma valida es Quantidia.

        El interceptor JS es experiencia de usuario; este guard server-side
        es la ley: un POST directo a la ruta nativa de firma electronica no
        puede completar el item mientras la ronda digital este en curso
        (saldria "firmado" sin firma criptografica). En Flujo A previo a la
        ronda (carga de datos) la firma electronica sigue funcionando.
        """
        item = request.env["sign.request.item"].sudo().search(
            [
                ("sign_request_id", "=", sign_request_id),
                ("access_token", "=", token),
            ],
            limit=1,
        )
        if item and item.sign_request_id.quantidia_digital_round:
            _logger.info(
                "quantidia: firma electronica RECHAZADA para item %s "
                "(ronda digital activa en request %s)",
                item.id,
                sign_request_id,
            )
            return {
                "success": False,
                "error": "digital_round_active",
                "message": "Este documento se firma digitalmente con Quantidia. "
                "Usá tu enlace de firma digital (te llegó por correo).",
            }
        return super().sign(
            sign_request_id, token, sms_token=sms_token, signature=signature, **kwargs
        )

    def _handle_origin_download(self, sign_request, sign_document_id):
        """Ronda digital (activa o completada): servir el PDF firmado vigente.

        El visor del documento (pagina publica y client action del backend)
        carga siempre la URL `/origin` del template. Con el proceso Quantidia
        en curso o completado, lo correcto es mostrar el ultimo PDF acumulado:
        las firmas digitales ya realizadas se ven al abrir el documento.
        """
        signed_att = sign_request.sudo().quantidia_signed_attachment_id
        if signed_att and sign_request.sudo().quantidia_status in (
            "in_progress",
            "signed",
        ):
            return self._create_document_response(
                sign_request, signed_att.datas, document_name=signed_att.name
            )
        return super()._handle_origin_download(sign_request, sign_document_id)
