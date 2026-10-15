# -*- coding: utf-8 -*-
"""Recepcion del PDF firmado que el SDK devolvio en el browser.

El SDK guarda los PDFs firmados en un store en memoria; el frontend debe
retirar los bytes (`getSignedDocumentBytes`), codificarlos a base64 y postearlos
a este endpoint, que los persiste como `ir.attachment` y cierra el flujo del
`sign.request`.

Usamos `type="jsonrpc"` (no `http` + `csrf`) para:
- evitar el check CSRF de formularios (la llamada `rpc` ya opera same-origin
  sobre la sesion logueada),
- devolver errores JSON limpios que el frontend pueda distinguir del exito.
"""

import base64
import logging

from odoo import http
from odoo.exceptions import AccessError, MissingError
from odoo.http import request

from .common import resolve_item_by_token

_logger = logging.getLogger(__name__)


def _resolve_writable_request(sign_request_id):
    """Resuelve un `sign.request` escribible por el usuario actual.

    Returns:
        (recordset, None) si existe y el usuario puede escribirlo.
        (empty, "sign_request not found") si no existe o el id es invalido.
        (empty, "access_denied") si existe pero el usuario no puede escribirlo.
    """
    try:
        sign_request = request.env["sign.request"].browse(
            int(sign_request_id)
        ).exists()
    except (ValueError, MissingError):
        return request.env["sign.request"], "sign_request not found"
    if not sign_request:
        return request.env["sign.request"], "sign_request not found"
    try:
        sign_request.check_access("write")
    except AccessError:
        return request.env["sign.request"], "access_denied"
    return sign_request, None


def _prepare_session_consumption(sign_request, session_token, partner, annex=False):
    """D8: valida la sesion Quantidia antes de persistir la firma.

    Un token de sesion (JWT con jti) autoriza UNA firma. Devuelve
    Devuelve ``(jti, error, already_consumed)``. Un ``jti`` nuevo autoriza
    el PDF principal y se consume al persistirlo. Un anexo puede reutilizar
    exclusivamente el ``jti`` ya consumido por ese mismo request y partner:
    todos los PDFs del bundle pertenecen a una sola accion del SDK, sin abrir
    la puerta a repetir la firma principal en otro request.

    Nota: no se exige vigencia (exp) a proposito — la carga de la firma
    puede demorar mas que el TTL del token; la identidad (sub) y el
    single-use (jti) son lo que cierran el circuito.
    """
    token_service = request.env["sign.quantidia.token"].sudo()
    payload = token_service._decode(session_token or "")
    if not payload:
        return None, "invalid_session_token", False
    if str(payload.get("sub") or "") != str(partner.id):
        return None, "session_partner_mismatch", False
    jti = payload.get("jti")
    if not jti:
        return None, "missing_jti", False
    consumption = request.env["quantidia.token.consumption"].sudo()
    previous = consumption.search([("jti", "=", jti)], limit=1)
    if not previous:
        return jti, None, False
    if (
        annex
        and previous.sign_request_id == sign_request
        and previous.partner_id == partner
    ):
        return jti, None, True
    return None, "session_already_consumed", False


def _consume_session(jti, sign_request, partner):
    """Registra el consumo del jti (llamar SOLO tras persistir la firma)."""
    request.env["quantidia.token.consumption"].sudo().create(
        {
            "jti": jti,
            "sign_request_id": sign_request.id,
            "partner_id": partner.id,
        }
    )


class SignUpload(http.Controller):
    @http.route(
        "/sign_quantidia/upload_signed",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def upload_signed(self, sign_request_id, datas, filename=None, session_token=None, annex=False, **kw):
        """Recibe el PDF firmado en base64 y lo persiste contra el request.

        Antes de tocar bytes valida acceso de escritura al `sign.request`: el
        ID viene del browser y no se puede confiar en el solo. En ronda
        digital exige ademas la sesion Quantidia (jti single-use, D8).
        """
        sign_request, error = _resolve_writable_request(sign_request_id)
        if error:
            # access_denied se reporta como not_found para no filtrar existencia.
            return {"ok": False, "error": "sign_request not found"}

        if not datas:
            return {"ok": False, "error": "empty_payload"}

        try:
            pdf_bytes = base64.b64decode(datas)
        except Exception:
            return {"ok": False, "error": "invalid_base64"}

        # D8: en ronda digital la sesion Quantidia es obligatoria y se
        # consume una sola vez (replay -> rechazo).
        jti = None
        session_already_consumed = False
        if sign_request.quantidia_digital_round:
            if not session_token:
                return {"ok": False, "error": "missing_session_token"}
            jti, session_error, session_already_consumed = _prepare_session_consumption(
                sign_request, session_token, request.env.user.partner_id, annex=annex
            )
            if session_error:
                _logger.warning(
                    "quantidia: upload rechazado para request %s (%s)",
                    sign_request_id, session_error,
                )
                return {"ok": False, "error": session_error}
            if annex and not session_already_consumed:
                return {"ok": False, "error": "annex_before_main_upload"}

        # En ronda digital, el flujo backend no trae request_item_id (el
        # publico lo resuelve por el token del item). Derivarlo por el partner
        # del usuario actual para que el turno serial se respete y su tracking
        # se marque al subir la firma; sin esto la ronda nunca avanza desde
        # el boton del chatter.
        request_item_id = None
        if sign_request.quantidia_digital_round:
            tracking = sign_request.quantidia_digital_signature_ids.filtered(
                lambda t: t.partner_id.id == request.env.user.partner_id.id
            )
            if tracking:
                request_item_id = tracking[:1].request_item_id.id

        attachment = sign_request._quantidia_store_signed_pdf(
            pdf_bytes, filename=filename, request_item_id=request_item_id, annex=annex
        )
        if jti and not session_already_consumed:
            _consume_session(jti, sign_request, request.env.user.partner_id)
        return {"ok": True, "attachment_id": attachment.id if attachment else False}

    @http.route(
        "/sign_quantidia/upload_signed_public",
        type="jsonrpc",
        auth="none",
        methods=["POST"],
        csrf=False,
    )
    def upload_signed_public(self, sign_request_id, access_token, datas, filename=None, session_token=None, annex=False, **kw):
        """Upload del PDF firmado por Quantidia, validado por access_token del item.
        Para el flujo publico (externos sin login)."""
        item, sign_request, err = resolve_item_by_token(access_token, sign_request_id)
        if err:
            return {"ok": False, "error": "sign_request not found"}
        if not datas:
            return {"ok": False, "error": "empty_payload"}
        try:
            pdf_bytes = base64.b64decode(datas)
        except Exception:
            return {"ok": False, "error": "invalid_base64"}

        # D8: en ronda digital la sesion Quantidia es obligatoria y se
        # consume una sola vez (replay -> rechazo).
        jti = None
        session_already_consumed = False
        if sign_request.quantidia_digital_round:
            if not session_token:
                return {"ok": False, "error": "missing_session_token"}
            jti, session_error, session_already_consumed = _prepare_session_consumption(
                sign_request, session_token, item.partner_id, annex=annex
            )
            if session_error:
                _logger.warning(
                    "quantidia: upload publico rechazado para request %s (%s)",
                    sign_request_id, session_error,
                )
                return {"ok": False, "error": session_error}
            if annex and not session_already_consumed:
                return {"ok": False, "error": "annex_before_main_upload"}

        attachment = sign_request._quantidia_store_signed_pdf(
            pdf_bytes, filename=filename, request_item_id=item.id, annex=annex
        )
        if jti and not session_already_consumed:
            _consume_session(jti, sign_request, item.partner_id)
        return {"ok": True, "attachment_id": attachment.id if attachment else False}

    @http.route(
        "/sign_quantidia/report_rejected",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def report_rejected(self, sign_request_id, reason=None, **kw):
        """Reporta que un firmante rechazo la firma digital (backend).

        Llamado por el frontend cuando el SDK de Quantidia devuelve un status
        de cancelacion/rechazo. Cancela la ronda digital pero NO la firma
        electronica (Opcion A: el PDF completado sigue siendo valido).
        """
        sign_request, error = _resolve_writable_request(sign_request_id)
        if error:
            return {"ok": False, "error": "sign_request not found"}

        # Buscar el tracking del usuario actual (por partner_id)
        partner_id = request.env.user.partner_id.id
        tracking = sign_request.quantidia_digital_signature_ids.filtered(
            lambda t: t.partner_id.id == partner_id and t.state == "pending"
        )
        if not tracking:
            _logger.warning("quantidia: no se encontro tracking pending para partner %s", partner_id)
            return {"ok": False, "error": "tracking_not_found"}

        tracking.action_mark_rejected(reason=reason)
        return {"ok": True}

    @http.route(
        "/sign_quantidia/report_rejected_public",
        type="jsonrpc",
        auth="none",
        methods=["POST"],
        csrf=False,
    )
    def report_rejected_public(self, sign_request_id, access_token, reason=None, **kw):
        """Reporta que un firmante rechazo la firma digital (flujo publico).

        Para externos sin login, validado por access_token del item.
        """
        item, sign_request, err = resolve_item_by_token(access_token, sign_request_id)
        if err:
            return {"ok": False, "error": "sign_request not found"}

        # Buscar el tracking del item
        tracking = sign_request.quantidia_digital_signature_ids.filtered(
            lambda t: t.request_item_id.id == item.id and t.state == "pending"
        )
        if not tracking:
            _logger.warning("quantidia: no se encontro tracking pending para item %s", item.id)
            return {"ok": False, "error": "tracking_not_found"}

        tracking.action_mark_rejected(reason=reason)
        return {"ok": True}
