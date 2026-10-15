# -*- coding: utf-8 -*-
"""Pagina portal del turno de firma digital Quantidia (D9).

URL: /quantidia/sign/<request_id>/<item_token>

Punto de entrada del flujo Augment para firmantes cuyo item electronico ya
completo: la pagina nativa de firma los manda a /my ("nada para firmar"),
asi que la ronda digital necesita su propia pagina donde firmar cuando
llega su turno. La pagina refleja el estado del tracking de ese item:

    turn       -> es su turno: boton que abre el SDK de Quantidia
    waiting    -> aun no es su turno (ronda serial)
    signed     -> ya firmo digitalmente
    completed  -> la ronda completa termino
    cancelled  -> la ronda fue cancelada/rechazada

El boton llama a /sign_quantidia/prepare_public (JWT del PARTNER del item,
no del usuario visitante) y openQuantidiaSign hace el resto: sube el PDF
por upload_signed_public, que valida el turno server-side.
"""

import base64
import logging
from urllib.parse import quote

from odoo import http
from odoo.http import request

from .common import resolve_item_by_token

_logger = logging.getLogger(__name__)


class QuantidiaPortalSign(http.Controller):

    @http.route(
        "/quantidia/sign/<int:sign_request_id>/<token>",
        type="http",
        auth="public",
        website=True,
    )
    def quantidia_portal_sign(self, sign_request_id, token, **post):
        item, sign_request, err = resolve_item_by_token(token, sign_request_id)
        if err or not item:
            return request.not_found()

        # Modo electronico: no hay ronda digital, la firma es la nativa.
        if (sign_request.quantidia_mode or "electronic") != "digital":
            return request.redirect(
                "/sign/document/%s/%s" % (sign_request_id, token)
            )

        # Misma politica que sign_access (setting require_portal_login), pero
        # aplicada SIEMPRE: en la ronda digital el item ya esta completado
        # electronicamente y queda una firma (la digital) que proteger.
        denied = self._quantidia_portal_access_denied(item)
        if denied:
            return denied

        tracking = sign_request.quantidia_digital_signature_ids.filtered(
            lambda t: t.request_item_id.id == item.id
        )[:1]
        status = sign_request.quantidia_status
        if status == "signed":
            page_state = "completed"
        elif status == "cancelled" or tracking.state in ("rejected", "cancelled"):
            page_state = "cancelled"
        elif sign_request.quantidia_digital_round and tracking.state == "pending":
            page_state = "turn"
        elif tracking.state == "signed":
            page_state = "signed"
        else:
            page_state = "waiting"

        total = len(sign_request.quantidia_digital_signature_ids)
        done = len(
            sign_request.quantidia_digital_signature_ids.filtered(
                lambda t: t.state == "signed"
            )
        )

        documents = self._quantidia_portal_documents(sign_request)

        return request.render(
            "sign_quantidia.quantidia_portal_sign",
            {
                "sign_request": sign_request,
                "reference": sign_request.reference,
                "signer_name": item.partner_id.display_name,
                "page_state": page_state,
                "sign_request_id": sign_request.id,
                "item_token": token,
                "round_total": total,
                "round_done": done,
                "documents": [
                    {"index": i, "name": att.name}
                    for i, att in enumerate(documents)
                ],
            },
        )

    def _quantidia_portal_documents(self, sign_request):
        """Lista de PDFs del portal (principal + anexos).

        Delega en sign.request._quantidia_display_documents (misma fuente
        que el bloque 'Archivos a firmar' del formulario): firmados durante
        o despues de la ronda, con fallback a los origenes si todavia no
        llego ningun firmado. Todo con env sudo: un search de
        ir.attachment con env de portal agrega un subquery de reglas sobre
        el comodel (sign.request) y el ACL deniega la lectura.
        """
        return sign_request.sudo()._quantidia_display_documents()

    @http.route(
        "/sign_quantidia/document_pdf/<int:sign_request_id>/<token>",
        type="http",
        auth="public",
        website=True,
    )
    def quantidia_document_pdf(self, sign_request_id, token, doc=None, **post):
        """Sirve inline un PDF de la lista de documentos del portal.

        ``doc`` es el indice en la lista (ver _quantidia_portal_documents);
        sin ``doc`` se sirve el primero (compatibilidad con el visor unico).
        Lo que se ve es lo que el SDK firma.
        """
        item, sign_request, err = resolve_item_by_token(token, sign_request_id)
        if err or not item:
            return request.not_found()
        documents = self._quantidia_portal_documents(sign_request)
        if not documents:
            return request.not_found()
        try:
            index = int(doc) if doc is not None else 0
        except (TypeError, ValueError):
            index = 0
        if index < 0 or index >= len(documents):
            index = 0
        att = documents[index]
        pdf = base64.b64decode(att.datas)
        return request.make_response(
            pdf,
            headers=[
                ("Content-Type", "application/pdf"),
                (
                    "Content-Disposition",
                    "inline; filename*=UTF-8''%s"
                    % quote(att.name or "document.pdf"),
                ),
            ],
        )

    def _quantidia_portal_access_denied(self, item):
        """Endurecimiento de identidad para la pagina de turno digital.

        None para continuar; Response si hay que exigir login / bloquear.
        """
        param = request.env["ir.config_parameter"].sudo().get_param(
            "sign_quantidia.require_portal_login"
        )
        if param in ("False", "0", "false"):
            return None

        if not item.partner_id.user_ids.filtered(lambda u: u.active):
            return None  # externo sin usuario: el token del item alcanza

        user = request.env.user
        if user._is_public():
            target = request.httprequest.full_path
            return request.redirect(
                "/web/login?redirect=" + quote(target, safe="")
            )
        if user.partner_id != item.partner_id:
            _logger.info(
                "quantidia: acceso bloqueado al turno digital del firmante %s "
                "para el usuario logueado %s",
                item.partner_id.display_name,
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
