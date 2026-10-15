# -*- coding: utf-8 -*-
"""Emision y validacion de access_token Odoo (JWT HS256, sin dependencias).

El access_token representa la identidad del usuario Odoo y es lo que el SDK
pasa a Quantidia. Quantidia lo valida llamando al endpoint de introspeccion
(`/oauth/introspect`, RFC 7662), que a su vez delega aca.

Usamos HMAC-SHA256 con un secreto en `ir.config_parameter` para no depender de
PyJWT; alcanza para el flujo de firma. Para multi-instancia o rotacion de
claves, migrar a una lib OIDC real (authlib / pyjwt + jwks).
"""

import base64
import hashlib
import hmac
import json
import logging
import time
import uuid

from odoo import api, models
from odoo.exceptions import AccessDenied

_logger = logging.getLogger(__name__)


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


class SignQuantidiaToken(models.AbstractModel):
    """Servicio de tokens para el flujo Quantidia."""

    _name = "sign.quantidia.token"
    _description = "Quantidia Sign Token Service"

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------
    @api.model
    def _get_param(self, key):
        return self.env["ir.config_parameter"].sudo().get_param(key)

    @api.model
    def _ttl(self):
        return int(self._get_param("sign_quantidia.token_ttl") or 300)

    @api.model
    def _secret(self):
        secret = self._get_param("sign_quantidia.token_secret")
        if not secret:
            # Scaffold: genera uno efimero y lo persiste. En produccion debe
            # venir de env/vault, no autogenerarse.
            secret = str(uuid.uuid4())
            self.env["ir.config_parameter"].sudo().set_param(
                "sign_quantidia.token_secret", secret
            )
        return secret

    # ------------------------------------------------------------------
    # Emision
    # ------------------------------------------------------------------
    @api.model
    def issue_token(self, sign_request=None, partner=None):
        """Emite un access_token (JWT HS256).

        - ``partner``: de quien es el token. Si es None, usa el usuario actual
          (requiere sesion Odoo). Usar ``partner`` para testing/portal (sin sesion).
        - ``sign_request``: opcional, vincula el token a un sign.request especifico.
        """
        partner = partner or self.env.user.partner_id
        now = int(time.time())
        ttl = self._ttl()
        url = self.env["ir.config_parameter"].sudo().get_param("web.base.url") or ""
        aud = self._get_param("sign_quantidia.sdk_api_base") or ""
        payload = {
            "iss": url,
            "aud": aud.replace("https://", "").replace("http://", "").rstrip("/") or "",
            "sub": str(partner.id),
            "jti": str(uuid.uuid4()),
            "iat": now,
            "nbf": now,
            "exp": now + ttl,
            "typ": "ID_TOKEN",
            "scope": "sign",
        }
        return self._encode(payload)

    @api.model
    def _encode(self, payload: dict) -> str:
        header = {"alg": "HS256", "typ": "JWT"}
        segments = [
            _b64url_encode(json.dumps(header, separators=(",", ":")).encode()),
            _b64url_encode(json.dumps(payload, separators=(",", ":")).encode()),
        ]
        signing_input = ".".join(segments).encode("ascii")
        sig = hmac.new(self._secret().encode(), signing_input, hashlib.sha256).digest()
        segments.append(_b64url_encode(sig))
        return ".".join(segments)

    # ------------------------------------------------------------------
    # Validacion / introspeccion (RFC 7662)
    # ------------------------------------------------------------------
    @api.model
    def introspect(self, token):
        """Devuelve el objeto de respuesta de introspeccion, o {active: False}.

        Construye el response enriquecido que espera Quantidia: claims OIDC +
        introspeccion + modelo interno (reference / customs / statuses).

        Corre con sudo porque la ruta es `auth="none"` (sin usuario logueado);
        sin sudo, acceder a partner.name/user.login dispara checks de acceso a
        campos que necesitan self.env.user (vacio) -> 500.
        """
        payload = self._decode(token)
        if not payload or not self._is_active(payload):
            return {"active": False}

        self = self.sudo()
        # Resolver partner por sub (partner_id) — sirve para internos y externos.
        partner = self.env["res.partner"].browse(int(payload.get("sub") or 0)).exists()
        if not partner:
            return {"active": False}

        # GATE de enrollment: el partner debe estar dado de alta como firmante
        # Quantidia (quantidia.signer con partner_id = este partner, activo).
        # Sin registro -> active:false (no hay fallback).
        signer = self.env["quantidia.signer"].search(
            [("partner_id", "=", partner.id), ("active", "=", True)],
            limit=1,
        )
        if not signer:
            _logger.info(
                "Introspect rechazado: partner %s sin quantidia.signer", partner.id
            )
            return {"active": False}

        user = partner.user_ids[:1] if partner.user_ids else self.env["res.users"]
        company = user.company_id if user else self.env.company

        # reference: environmentId / companyId / agencyId son UUIDs del lado
        # Quantidia (config en Odoo). userId = id de la tabla quantidia.signer.
        environment_id = self._get_param("sign_quantidia.environment_id") or ""
        company_ref = self._get_param("sign_quantidia.company_id") or ""
        agency_ref = self._get_param("sign_quantidia.agency_id") or ""

        # Claims OIDC: username/given/family vienen del signer (propios de
        # Quantidia); fallback de username al login/email del partner.
        username_claim = (
            signer.username or user.login or partner.email or partner.name or "signer"
        ).strip()
        given_name = signer.given_name or ""
        family_name = signer.family_name or ""
        email_claim = (signer.email or partner.email or "").strip()

        return {
            # temporales / control
            "exp": payload.get("exp"),
            "iat": payload.get("iat"),
            "nbf": payload.get("nbf"),
            "jti": payload.get("jti"),
            # emisor / audiencia / identidad
            "iss": payload.get("iss"),
            "aud": payload.get("aud"),
            "sub": payload.get("sub"),
            "typ": payload.get("typ", "ID_TOKEN"),
            # sesion / auth
            "azp": "Quantidia Backend Client",
            "sid": payload.get("jti"),
            "acr": "1",
            # claims OIDC (username, preferred_username, given_name, family_name,
            # email son mandatorios)
            "name": ((given_name + " " + family_name).strip()) or username_claim,
            "given_name": given_name,
            "family_name": family_name,
            "preferred_username": username_claim,
            "email": email_claim,
            "email_verified": bool(email_claim),
            "username": username_claim,
            "token_type": "Bearer",
            # RFC 7662
            "active": True,
            "scope": payload.get("scope", "sign"),
            "client_id": payload.get("azp", ""),
            # modelo interno Quantidia
            "reference": {
                "environmentId": environment_id,
                "companyId": company_ref,
                "agencyId": agency_ref,
                "userId": str(signer.id),
                "subscriptionId": "",
            },
            "customs": {
                "alias": username_claim,
                "company": company.name or "",
                "agency": agency_ref,
            },
            "statuses": ["Enabled"] if user and user.active else [],
        }

    @api.model
    def _decode(self, token: str):
        if not token or token.count(".") != 2:
            return None
        try:
            header_b64, payload_b64, sig_b64 = token.split(".")
            signing_input = (header_b64 + "." + payload_b64).encode("ascii")
            expected = hmac.new(
                self._secret().encode(), signing_input, hashlib.sha256
            ).digest()
            given = _b64url_decode(sig_b64)
            if not hmac.compare_digest(expected, given):
                _logger.warning("Quantidia token: firma invalida")
                return None
            return json.loads(_b64url_decode(payload_b64))
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Quantidia token: decode fallido (%s)", exc)
            return None

    @api.model
    def _is_active(self, payload):
        now = int(time.time())
        if payload.get("nbf") and now < payload["nbf"]:
            return False
        if payload.get("exp") and now >= payload["exp"]:
            return False
        # un solo uso: TODO marcar el jti como consumido en una tabla
        # de revocacion al confirmar la firma.
        return True

    @api.model
    def _ensure_oauth_credentials(self):
        """Auto-genera client_id/client_secret/introspect_api_key si faltan.

        Asi /oauth/introspect funciona out-of-the-box: el integrador lee esos
        valores de Settings (res.config.settings) y se los comparte a Quantidia
        para que los use al llamar al endpoint. En produccion conviene fijarlos
        desde vault/env en vez de autogenerarlos.
        """
        param = self.env["ir.config_parameter"].sudo()
        mapping = {
            "sign_quantidia.oauth_client_id": "qid_" + uuid.uuid4().hex,
            "sign_quantidia.oauth_client_secret": "sec_" + uuid.uuid4().hex,
            "sign_quantidia.introspect_api_key": "key_" + uuid.uuid4().hex,
        }
        for key, default in mapping.items():
            if not param.get_param(key):
                param.set_param(key, default)

    @api.model
    def assert_client_credentials(self, auth_header, api_key):
        """Valida el Basic (client_id:client_secret) + Api-Key del introspect."""
        self._ensure_oauth_credentials()
        expected_cid = self._get_param("sign_quantidia.oauth_client_id")
        expected_secret = self._get_param("sign_quantidia.oauth_client_secret")
        expected_apikey = self._get_param("sign_quantidia.introspect_api_key")
        if not (expected_cid and expected_secret and expected_apikey):
            raise AccessDenied("Credenciales de introspect Quantidia no configuradas")
        if not auth_header or not auth_header.startswith("Basic "):
            raise AccessDenied("Falta Authorization Basic")
        try:
            decoded = base64.b64decode(auth_header[6:]).decode()
            cid, secret = decoded.split(":", 1)
        except Exception:  # noqa: BLE001
            raise AccessDenied("Authorization Basic malformado")
        if not (
            hmac.compare_digest(cid, expected_cid)
            and hmac.compare_digest(secret, expected_secret)
        ):
            raise AccessDenied("client_id / client_secret invalidos")
        if not api_key or not hmac.compare_digest(api_key, expected_apikey):
            raise AccessDenied("Api-Key invalida")
        return True
