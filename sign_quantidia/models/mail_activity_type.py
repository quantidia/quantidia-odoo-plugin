# -*- coding: utf-8 -*-
from odoo import fields, models


class MailActivityType(models.Model):
    _inherit = "mail.activity.type"

    quantidia_adhoc = fields.Boolean(
        string="Firma Quantidia ad hoc",
        help="Abre el wizard Quantidia para elegir PDFs y firmantes, sin usar un template de negocio.",
    )
