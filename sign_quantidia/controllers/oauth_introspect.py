# -*- coding: utf-8 -*-
"""Endpoint de introspeccion de tokens (RFC 7662).

Quantidia llama a este endpoint para validar el access_token Odoo que el SDK
le paso. Devuelve el objeto enriquecido (OIDC + introspeccion + modelo
interno Quantidia) o `{active: false}`.
"""

import json
import logging

from odoo import http
from odoo.exceptions import AccessDenied
from odoo.http import request

_logger = logging.getLogger(__name__)


class OAuthIntrospect(http.Controller):
    @http.route(
        "/oauth/introspect",
        type="http",
        auth="none",
        methods=["POST"],
        csrf=False,
    )
    def introspect(self, **kw):
        token_service = request.env["sign.quantidia.token"]
        try:
            token_service.assert_client_credentials(
                request.httprequest.headers.get("Authorization", ""),
                request.httprequest.headers.get("Api-Key", ""),
            )
        except AccessDenied as exc:
            _logger.info("Introspect rechazado: %s", exc)
            return request.make_response(
                json.dumps({"active": False}),
                headers={"Content-Type": "application/json"},
                status=401,
            )

        token = request.httprequest.form.get("token")
        response = token_service.introspect(token)
        _logger.info(
            "Introspect ok: active=%s sub=%s jti=%s",
            response.get("active"),
            response.get("sub"),
            response.get("jti"),
        )
        return request.make_response(
            json.dumps(response),
            headers={"Content-Type": "application/json"},
        )
