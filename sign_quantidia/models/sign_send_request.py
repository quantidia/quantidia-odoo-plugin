# -*- coding: utf-8 -*-
from odoo import fields, models


class SignSendRequest(models.TransientModel):
    """Agrega quantidia_mode al wizard de creación de sign.request."""

    _inherit = "sign.send.request"

    quantidia_mode = fields.Selection(
        [("digital", "Quantidia"), ("electronic", "Odoo")],
        string="Modo de firma",
        # Mismo default configurable que sign.request (Settings): así el
        # wizard y el campo del request no divergen entre instalaciones.
        default=lambda self: self.env["sign.request"]._quantidia_default_mode(),
        required=True,
    )

    def create_request(self):
        # Pasar quantidia_mode al sign.request al crearlo.
        self.ensure_one()
        sign_request = super().create_request()
        if sign_request and self.quantidia_mode:
            sign_request.write({"quantidia_mode": self.quantidia_mode})
        return sign_request
