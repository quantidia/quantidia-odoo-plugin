# -*- coding: utf-8 -*-
"""Funciones compartidas entre controllers de sign_quantidia."""

from odoo import SUPERUSER_ID
from odoo.api import Environment
from odoo.exceptions import MissingError
from odoo.http import request
from odoo.tools import consteq


def resolve_item_by_token(access_token, sign_request_id):
    """Resuelve (item, sign.request) por access_token.

    Usa Environment con SUPERUSER_ID porque las rutas publicas (auth='none')
    tienen env.user vacio -> message_post y otras operaciones que tocan
    env.user crashean.

    Acepta el token del ITEM (sign.request.item.access_token) O del REQUEST
    (sign.request.access_token). Si es el token del request, resuelve el primer
    item en estado 'sent'.

    Returns (item, sign_request, None) | (None, None, error_code).
    """
    if not access_token:
        return None, None, "missing_token"
    # Environment con SUPERUSER: resuelve el env.user vacio de auth='none'.
    # Odoo 19: usar request.env.cr y request.env.context en lugar de request.cr/context
    env = Environment(request.env.cr, SUPERUSER_ID, request.env.context)
    try:
        sign_request = env["sign.request"].browse(int(sign_request_id)).exists()
    except (ValueError, MissingError):
        return None, None, "not_found"
    if not sign_request:
        return None, None, "not_found"
    # 1) token de un item especifico
    item = sign_request.request_item_ids.filtered(
        lambda it: it.access_token and consteq(it.access_token, access_token)
    )
    if item:
        return item[:1], sign_request, None
    # 2) token del request (Odoo lo permite): primer item 'sent'
    if sign_request.access_token and consteq(sign_request.access_token, access_token):
        sent = sign_request.request_item_ids.filtered(lambda it: it.state == "sent")
        return sent[:1] if sent else sign_request.request_item_ids[:1], sign_request, None
    return None, None, "invalid_token"
