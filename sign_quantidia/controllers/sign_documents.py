# -*- coding: utf-8 -*-
"""Entrega al frontend los PDFs origen del `sign.request` para el SDK.

El SDK de Quantidia firma sobre `File[]` que viven en el browser; este endpoint
devuelve los `template_document_ids` (la fuente canonica de Odoo Sign) ya como
base64 + nombre + mime, validando acceso de lectura al request.

Delega la logica de negocio en `sign.request._quantidia_get_source_documents`
para mantener el controller fino y la logica testeable.
"""

from odoo import http
from odoo.exceptions import AccessError, MissingError
from odoo.http import request

from .common import resolve_item_by_token


class SignDocuments(http.Controller):
    @http.route(
        "/sign_quantidia/get_source_documents",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def get_source_documents(self, sign_request_id):
        try:
            sign_request = request.env["sign.request"].browse(
                int(sign_request_id)
            ).exists()
        except (ValueError, MissingError):
            return {"error": "sign_request not found"}

        # browse(<id valido pero inexistente>).exists() devuelve un recordset
        # vacio (no lanza): sin este guard, check_access_rule seria no-op y
        # _quantidia_get_source_documents reventaria en ensure_one() -> HTTP 500.
        if not sign_request:
            return {"error": "sign_request not found"}

        # Derechos a nivel modelo + reglas de registro: quien no pueda leer el
        # request no debe poder retirar los bytes de sus PDFs.
        try:
            sign_request.check_access_rights("read")
            sign_request.check_access_rule("read")
        except AccessError:
            return {"error": "access_denied"}

        return {"documents": sign_request._quantidia_get_source_documents()}

    @http.route(
        "/sign_quantidia/is_last_signer",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def is_last_signer(self, sign_request_id):
        """Indica al frontend si el firmante actual es el ultimo pendiente.

        Lo usa el patch de `SignableDocument.signDocuments` para decidir si
        intercepta el "Validar y enviar" nativo y abre Quantidia (modo Replace)
        o si deja continuar el flujo nativo.
        """
        try:
            sign_request = request.env["sign.request"].browse(
                int(sign_request_id)
            ).exists()
        except (ValueError, MissingError):
            return {"is_last": False, "num_signers": 0, "mode": "electronic"}
        if not sign_request:
            return {"is_last": False, "num_signers": 0, "mode": "electronic"}
        try:
            sign_request.check_access("read")
        except AccessError:
            return {"is_last": False, "num_signers": 0, "mode": "electronic"}
        tracking = sign_request.quantidia_digital_signature_ids.filtered(
            lambda t: t.partner_id.id == request.env.user.partner_id.id
        )[:1]
        # D2: el item del firmante actual (por partner) valida que sea el
        # pendiente, no alcanza con contar items.
        current_item = sign_request.request_item_ids.filtered(
            lambda it: it.partner_id.id == request.env.user.partner_id.id
        )[:1]
        return {
            "is_last": sign_request._quantidia_is_last_signer(item=current_item),
            "num_signers": len(sign_request.request_item_ids),
            "mode": sign_request.quantidia_mode or "electronic",
            # Ronda digital: si esta activa, la firma nativa NO debe completar
            # electronicamente (el frontend redirige al SDK de Quantidia).
            "round_active": bool(sign_request.quantidia_digital_round),
            "my_turn": bool(tracking and tracking.state == "pending"),
            "my_state": tracking.state or "",
        }

    @http.route(
        "/sign_quantidia/signer_partner_ids",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def signer_partner_ids(self):
        """Devuelve los ids de partners con un quantidia.signer activo.

        Lo usa el widget signer_x2many para marcar en vivo (client-side) que
        firmantes tienen perfil Quantidia, sin depender del onchange de la linea.
        """
        partner_ids = (
            self.env["quantidia.signer"]
            .search([("active", "=", True)])
            .mapped("partner_id")
            .ids
        )
        return {"partner_ids": partner_ids}

    # ------------------------------------------------------------------
    # Publicos (auth=none, validados por access_token del item) - D1
    # Para firmantes externos / portal sin login.
    # ------------------------------------------------------------------
    @http.route(
        "/sign_quantidia/prepare_public",
        type="jsonrpc",
        auth="none",
        methods=["POST"],
        csrf=False,
    )
    def prepare_public(self, sign_request_id, access_token):
        """Prepara config + emite JWT para el PARTNER del item (no self.env.user).
        Usado por el flujo publico (externos sin login)."""
        item, sign_request, err = resolve_item_by_token(access_token, sign_request_id)
        if err:
            return {"error": err}
        # Ronda serial: no emitir el JWT de Quantidia si no es el turno del
        # firmante (evita firmar del lado Quantidia un documento que Odoo
        # luego rechazaria por turno al intentar subirlo).
        if sign_request.quantidia_digital_round:
            tracking = sign_request.quantidia_digital_signature_ids.filtered(
                lambda t: t.request_item_id.id == item.id
            )
            if tracking and tracking.state != "pending":
                return {"error": "not_your_turn"}
        token_service = request.env["sign.quantidia.token"].sudo()
        jwt = token_service.issue_token(sign_request=sign_request, partner=item.partner_id)
        sign_request.sudo().write({"quantidia_session_token": jwt, "quantidia_status": "in_progress"})
        get_param = request.env["ir.config_parameter"].sudo().get_param
        return {
            "baseUrl": get_param("sign_quantidia.sdk_base_url") or "",
            "apiBase": get_param("sign_quantidia.sdk_api_base") or "",
            "environmentId": get_param("sign_quantidia.environment_id") or "",
            "companyId": get_param("sign_quantidia.company_id") or "",
            "agencyId": get_param("sign_quantidia.agency_id") or "",
            "apiKey": get_param("sign_quantidia.api_key") or "",
            "authMode": get_param("sign_quantidia.auth_mode") or "sso",
            "accessToken": jwt,
            # Recuadro de firma (signatureRect) de ESTE firmante: cada
            # firmante de la ronda recibe su posicion para no superponerse.
            "signatureRect": sign_request.sudo()._quantidia_get_signature_rect(item),
        }

    @http.route(
        "/sign_quantidia/get_source_documents_public",
        type="jsonrpc",
        auth="none",
        methods=["POST"],
        csrf=False,
    )
    def get_source_documents_public(self, sign_request_id, access_token):
        item, sign_request, err = resolve_item_by_token(access_token, sign_request_id)
        if err:
            return {"error": err}
        return {"documents": sign_request._quantidia_get_source_documents()}

    @http.route(
        "/sign_quantidia/is_last_signer_public",
        type="jsonrpc",
        auth="none",
        methods=["POST"],
        csrf=False,
    )
    def is_last_signer_public(self, sign_request_id, access_token):
        item, sign_request, err = resolve_item_by_token(access_token, sign_request_id)
        if err:
            return {"is_last": False, "num_signers": 0, "mode": "electronic"}
        tracking = sign_request.quantidia_digital_signature_ids.filtered(
            lambda t: t.request_item_id.id == item.id
        )[:1]
        return {
            "is_last": sign_request._quantidia_is_last_signer(item=item),
            "num_signers": len(sign_request.request_item_ids),
            "mode": sign_request.quantidia_mode or "electronic",
            # Ronda digital: si esta activa, la firma nativa NO debe completar
            # electronicamente (el frontend redirige al SDK de Quantidia).
            "round_active": bool(sign_request.quantidia_digital_round),
            "my_turn": bool(tracking and tracking.state == "pending"),
            "my_state": tracking.state or "",
        }

    # ------------------------------------------------------------------
    # Avance del proceso (para el mensaje del dialogo post-firma)
    # ------------------------------------------------------------------
    def _sign_progress_payload(self, sign_request, tracking=None):
        remaining = sign_request.request_item_ids.filtered(lambda it: it.state == "sent")
        return {
            "mode": sign_request.quantidia_mode or "electronic",
            "phase": sign_request._quantidia_sign_phase(),
            "num_signers": len(sign_request.request_item_ids),
            "remaining_signers": len(remaining),
            # tracking del firmante que consulta: en fase digital_round dice si
            # ya le toca firmar (el mensaje del dialogo depende de esto).
            "my_turn": bool(tracking and tracking.state == "pending"),
        }

    @http.route(
        "/sign_quantidia/sign_progress",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def sign_progress(self, sign_request_id):
        """Avance del request para el dialogo de agradecimiento post-firma.

        En modo digital multi-firmante el proceso NO termina al validar: el
        mensaje debe reflejar cuantos firmantes faltan y el estado de la ronda
        digital. Si no hay acceso de lectura, se degrada a modo electronic para
        que el dialogo muestre el mensaje nativo (sin filtrar datos).
        """
        try:
            sign_request = request.env["sign.request"].browse(
                int(sign_request_id)
            ).exists()
        except (ValueError, MissingError):
            sign_request = request.env["sign.request"]
        if not sign_request:
            return {"mode": "electronic", "remaining_signers": 0, "digital_round": False}
        try:
            sign_request.check_access("read")
        except AccessError:
            return {"mode": "electronic", "remaining_signers": 0, "digital_round": False}
        tracking = sign_request.quantidia_digital_signature_ids.filtered(
            lambda t: t.partner_id.id == request.env.user.partner_id.id
        )[:1]
        return self._sign_progress_payload(sign_request, tracking=tracking)

    @http.route(
        "/sign_quantidia/sign_progress_public",
        type="jsonrpc",
        auth="none",
        methods=["POST"],
        csrf=False,
    )
    def sign_progress_public(self, sign_request_id, access_token):
        """Variante publica (token del item) del avance del request."""
        item, sign_request, err = resolve_item_by_token(access_token, sign_request_id)
        if err:
            return {"mode": "electronic", "remaining_signers": 0, "digital_round": False}
        tracking = sign_request.quantidia_digital_signature_ids.filtered(
            lambda t: t.request_item_id.id == item.id
        )[:1]
        return self._sign_progress_payload(sign_request, tracking=tracking)
