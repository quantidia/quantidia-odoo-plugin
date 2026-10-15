# -*- coding: utf-8 -*-
from odoo import _, models
from odoo.exceptions import UserError


class ResPartner(models.Model):
    """Boton para dar de alta un perfil Quantidia desde el partner: asocia el
    partner_id y pre-llena given_name/family_name partiendo partner.name.
    """

    _inherit = "res.partner"

    def action_create_quantidia_signer(self):
        self.ensure_one()
        Signer = self.env["quantidia.signer"]
        signer = Signer.search([("partner_id", "=", self.id)], limit=1)
        if not signer:
            if not self.email:
                raise UserError(_(
                    "Para crear el perfil de firmante Quantidia el contacto "
                    "necesita un email (es el identificador del firmante). "
                    "Cargalo en el contacto y volvé a intentar."
                ))
            parts = (self.name or "").strip().split(" ", 1)
            signer = Signer.create(
                {
                    "partner_id": self.id,
                    "given_name": parts[0] if parts else "",
                    "family_name": parts[1] if len(parts) > 1 else "",
                    "username": self.email.split("@")[0],
                    "email": self.email,
                }
            )
        return {
            "type": "ir.actions.act_window",
            "res_model": "quantidia.signer",
            "res_id": signer.id,
            "view_mode": "form",
            "target": "current",
        }
