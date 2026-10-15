# -*- coding: utf-8 -*-
from odoo import models


class IrAttachment(models.Model):
    """Boton de vista rapida para los PDFs de una solicitud Quantidia."""

    _inherit = "ir.attachment"

    def action_quantidia_view_pdf(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=true" % self.id,
            "target": "self",
        }
