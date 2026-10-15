# -*- coding: utf-8 -*-
import odoo

from . import models
from . import controllers
from . import services


def post_init_hook(env):
    """Se ejecuta tras instalar/actualizar el modulo.

    Odoo 19 invoca los hooks con un Environment unico (antes era (cr,
    registry); esa firma reventaba la instalacion con TypeError).

    Pre-genera las credenciales del introspect (client_id / client_secret /
    api-key) y el secreto de firma del JWT, para que queden listas en
    Settings desde el arranque (en vez de lazily en el primer introspect).
    El integrador las lee de ahi y se las comparte a Quantidia.
    """
    env = env(user=odoo.SUPERUSER_ID)
    token_service = env["sign.quantidia.token"]
    token_service._ensure_oauth_credentials()
    token_service._secret()

    # Instalar sobre una base con Odoo Sign en uso NO debe cambiar el flujo
    # de lo ya existente: los sign.request previos a la instalacion quedan
    # en modo "electronic" (firma nativa). Sin esto, el default del campo
    # los marcaba "digital" y el override de _sign() les disparaba una ronda
    # Quantidia al completarse la firma electronica. SQL directo: campo
    # recien agregado, sin dependencias ORM que actualizar.
    env.cr.execute(
        "UPDATE sign_request SET quantidia_mode = 'electronic' "
        "WHERE quantidia_mode IS NULL OR quantidia_mode = 'digital'"
    )
