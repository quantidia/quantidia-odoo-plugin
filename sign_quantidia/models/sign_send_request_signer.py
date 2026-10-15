# -*- coding: utf-8 -*-
from odoo import api, fields, models


class SignSendRequestSigner(models.TransientModel):
    """Expone en el wizard si el contacto puede firmar con Quantidia."""

    _inherit = "sign.send.request.signer"

    quantidia_digital_ready = fields.Boolean(
        string="Firmante Quantidia",
        compute="_compute_quantidia_digital_ready",
    )

    @api.depends("partner_id")
    def _compute_quantidia_digital_ready(self):
        partner_ids = self.mapped("partner_id").ids
        ready_partner_ids = set(
            self.env["quantidia.signer"]
            .search(
                [
                    ("partner_id", "in", partner_ids),
                    ("active", "=", True),
                ]
            )
            .mapped("partner_id")
            .ids
        )
        for signer in self:
            signer.quantidia_digital_ready = signer.partner_id.id in ready_partner_ids

    @api.onchange("partner_id")
    def _onchange_partner_id_quantidia_digital_ready(self):
        """Refresca el badge al editar una linea del widget de signatarios.

        El widget ``signer_x2many`` modifica la linea transitoria en el
        navegador. El compute resuelve el valor al cargarla, pero este onchange
        hace que Odoo devuelva el nuevo estado en la misma respuesta al cambiar
        de contacto.
        """
        self._compute_quantidia_digital_ready()
