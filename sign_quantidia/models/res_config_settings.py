# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    """Configuracion de la integracion Quantidia.

    Las credenciales se persisten como `ir.config_parameter` (con grupo
    restringido) para no hardcodearlas en codigo, tal como pide el manual
    del SDK. El acceso a produccion debe ir por vault/env en deploy.
    """

    _inherit = "res.config.settings"

    # Modo default para requests NUEVOS (wizard y creacion directa).
    # "electronic" conserva el flujo nativo de Odoo Sign; "digital" hace que
    # todo nuevo pedido use la ronda Quantidia. Los requests existentes no
    # cambian retroactivamente.
    quantidia_default_mode = fields.Selection(
        [("electronic", "Odoo (firma electrónica nativa)"),
         ("digital", "Quantidia (firma digital)")],
        string="Modo de firma por defecto",
        config_parameter="sign_quantidia.default_mode",
        default="electronic",
        required=True,
    )

    quantidia_sdk_base_url = fields.Char(
        string="SDK baseUrl",
        config_parameter="sign_quantidia.sdk_base_url",
        default="https://test.quantidia.com/integration",
    )
    quantidia_sdk_api_base = fields.Char(
        string="SDK apiBase",
        config_parameter="sign_quantidia.sdk_api_base",
        default="https://test.quantidia.com",
    )
    quantidia_environment_id = fields.Char(
        string="Environment ID",
        config_parameter="sign_quantidia.environment_id",
    )
    # companyId y agencyId del lado Quantidia (UUIDs que ellos proveen); van en
    # el reference del introspect. NO son los IDs internos de Odoo.
    quantidia_company_id = fields.Char(
        string="Company ID (Quantidia)",
        config_parameter="sign_quantidia.company_id",
    )
    quantidia_agency_id = fields.Char(
        string="Agency ID (Quantidia)",
        config_parameter="sign_quantidia.agency_id",
    )
    quantidia_api_key = fields.Char(
        string="SDK Api-Key",
        config_parameter="sign_quantidia.api_key",
        help="Api-Key PUBLICA del SDK (viaja al browser en headersOverride). "
        "NO usar aqui el Api-Key del introspect OAuth: ese es secreto de "
        "servidor y no debe exponerse al frontend (D10, deuda tecnica).",
    )
    # Modo de auth del SDK: "simple" = usuario/clave de test (solo demo);
    # "sso" = access_token Odoo validado por Quantidia via /oauth/introspect.
    quantidia_auth_mode = fields.Selection(
        [("sso", "SSO (access_token Odoo)"), ("simple", "Simple (usuario/clave test)")],
        string="Modo de autenticacion",
        config_parameter="sign_quantidia.auth_mode",
        default="sso",
        required=True,
    )
    quantidia_test_username = fields.Char(
        string="Usuario de test (modo simple)",
        config_parameter="sign_quantidia.test_username",
    )
    quantidia_test_password = fields.Char(
        string="Password de test (modo simple)",
        config_parameter="sign_quantidia.test_password",
    )
    # Credenciales del introspect (lado Odoo, asignadas a Quantidia)
    quantidia_oauth_client_id = fields.Char(
        string="OAuth client_id",
        config_parameter="sign_quantidia.oauth_client_id",
    )
    quantidia_oauth_client_secret = fields.Char(
        string="OAuth client_secret",
        config_parameter="sign_quantidia.oauth_client_secret",
    )
    quantidia_introspect_api_key = fields.Char(
        string="Introspect Api-Key",
        config_parameter="sign_quantidia.introspect_api_key",
    )
    quantidia_token_secret = fields.Char(
        string="Token signing secret",
        config_parameter="sign_quantidia.token_secret",
        help="Secreto HMAC usado para firmar los access_token Odoo.",
    )
    quantidia_token_ttl = fields.Integer(
        string="Token TTL (segundos)",
        config_parameter="sign_quantidia.token_ttl",
        default=300,
    )
    quantidia_require_portal_login = fields.Boolean(
        string="Exigir login al firmante con usuario",
        config_parameter="sign_quantidia.require_portal_login",
        default=True,
        help="Si el firmante de un enlace tiene usuario de Odoo, la pagina de firma "
        "le exige iniciar sesion y verifica que el usuario coincida con el firmante. "
        "Firmantes sin usuario siguen firmando solo con el token del enlace.",
    )
