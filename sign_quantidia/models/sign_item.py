# -*- coding: utf-8 -*-
from odoo import api, models

from .sign_item_type import QUANTIDIA_ITEM_TYPE


class SignItem(models.Model):
    _inherit = "sign.item"

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        # El campo Quantidia es un marcador de posicion para la ronda digital:
        # si quedara required=True bloquearia la validacion electronica nativa
        # (sign.request.item._sign exige completar los campos required del rol).
        records._quantidia_keep_optional()
        return records

    def write(self, vals):
        res = super().write(vals)
        # El editor dropea campos con required=True por defecto y el popover
        # permite re-marcarlos: se normaliza siempre del lado del servidor.
        if vals.get("required"):
            self._quantidia_keep_optional()
        return res

    def _quantidia_keep_optional(self):
        bad = self.filtered(
            lambda item: item.type_id.item_type == QUANTIDIA_ITEM_TYPE
            and item.required
            and not self.env.context.get("quantidia_keep_optional_done")
        )
        if bad:
            bad.with_context(quantidia_keep_optional_done=True).write(
                {"required": False}
            )
