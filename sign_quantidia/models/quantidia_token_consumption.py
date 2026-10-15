# -*- coding: utf-8 -*-
from odoo import fields, models


class QuantidiaTokenConsumption(models.Model):
    """Consumo single-use de una sesion de firma Quantidia (D8).

    Cada JWT que emitimos lleva un ``jti`` unico. Cuando el PDF firmado se
    sube a Odoo, el ``jti`` de la sesion que lo produjo queda registrado aca:
    una sesion = una firma. Replays del mismo token (re-apertura de modal o
    re-upload) se rechazan en ``sign_upload`` consultando esta tabla.

    Solo se escribe via sudo desde el flujo de upload; no hay UI.
    """

    _name = "quantidia.token.consumption"
    _description = "Consumo de sesion Quantidia (jti single-use)"
    _order = "consumed_at desc"

    jti = fields.Char(
        string="JTI",
        required=True,
        index=True,
        help="Identificador unico del JWT de sesion consumido.",
    )
    sign_request_id = fields.Many2one(
        "sign.request",
        string="Request",
        index=True,
        ondelete="cascade",
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Firmante",
        help="Partner cuya sesion se consumo (claim sub del JWT).",
    )
    consumed_at = fields.Datetime(
        string="Consumido",
        default=fields.Datetime.now,
        readonly=True,
    )

    # Odoo 19 depreca _sql_constraints: restriccion unica via Constraint.
    _jti_unique = models.Constraint(
        "unique(jti)",
        "Cada sesion de firma (jti) se consume una sola vez.",
    )
