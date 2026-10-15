# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models
from odoo.exceptions import UserError


class SignRequestItem(models.Model):
    """Extiende el item de firma para indicar si el partner esta habilitado
    como firmante digital Quantidia (tiene quantidia.signer activo).

    Sirve para advertir, al armar el request, que firmantes tienen/no tienen
    firma digital — sin tocar el flujo nativo de Sign (solo un computed + vista).
    """

    _inherit = "sign.request.item"

    def _sign(self, signature, **kwargs):
        # D0 (deuda tecnica): con ronda digital activa, la firma del item es
        # la digital de Quantidia (tracking serial). El interceptor JS ya
        # evita completar electronicamente, pero un POST directo a la ruta
        # nativa de validacion lo salteaba y marcaba el item "firmado" sin
        # firma digital: se bloquea tambien del lado servidor. El flujo
        # Quantidia no pasa por aca (usa _post_fill_request_item directo).
        for item in self:
            if item.sign_request_id.quantidia_digital_round:
                raise UserError(
                    "Este documento se firma digitalmente con Quantidia. "
                    "Usá tu enlace de firma digital (o el botón \"Firmar "
                    "anexos\") cuando llegue tu turno."
                )
        return super()._sign(signature, **kwargs)

    def send_signature_accesses(self):
        """Con ronda digital activa, el mail nativo de invitacion no se manda.

        Ese mail enlaza a la pagina de firma ELECTRONICA nativa (que en la
        ronda digital ya esta completada): confunde al firmante, que debe
        entrar por el mail del turno Quantidia (link al portal digital).
        """
        digital = self.filtered(
            lambda i: i.sign_request_id.quantidia_digital_round
        )
        remaining = self - digital
        if digital:
            _logger = logging.getLogger(__name__)
            _logger.info(
                "quantidia: invitacion nativa suprimida para %s items (ronda digital activa)",
                len(digital),
            )
            # El mail que informa el turno es el de Quantidia (portal
            # digital), pero is_mail_sent igual debe quedar en True: la
            # lista del portal /my filtra los items sent por ese flag y
            # sin el la solicitud queda invisible para el firmante.
            digital.write({"is_mail_sent": True})
        if remaining:
            return super(SignRequestItem, remaining).send_signature_accesses()
        return True

    quantidia_digital_ready = fields.Boolean(
        string="Firma digital",
        compute="_compute_quantidia_digital_ready",
        search="_search_quantidia_digital_ready",
    )

    @api.depends("partner_id")
    def _compute_quantidia_digital_ready(self):
        Signer = self.env["quantidia.signer"]
        for item in self:
            if not item.partner_id:
                item.quantidia_digital_ready = False
                continue
            signer = Signer.search_count(
                [
                    ("partner_id", "=", item.partner_id.id),
                    ("active", "=", True),
                ]
            )
            item.quantidia_digital_ready = bool(signer)

    quantidia_digital_state = fields.Selection(
        [
            ("waiting", "Esperando turno"),
            ("pending", "Turno actual"),
            ("signed", "Firmado"),
            ("rejected", "Rechazado"),
            ("cancelled", "Cancelado"),
        ],
        string="Estado firma digital",
        compute="_compute_quantidia_digital_state",
        help="Estado del firmante dentro de la ronda digital Quantidia "
        "(tracking asociado a este item).",
    )

    @api.depends(
        "sign_request_id.quantidia_digital_signature_ids.state",
        "sign_request_id.quantidia_digital_signature_ids.request_item_id",
    )
    def _compute_quantidia_digital_state(self):
        for item in self:
            tracking = item.sign_request_id.quantidia_digital_signature_ids.filtered(
                lambda t: t.request_item_id.id == item.id
            )
            item.quantidia_digital_state = tracking[:1].state or False

    def _search_quantidia_digital_ready(self, operator, value):
        # Soporte basico de busqueda por el flag (partners con/without signer activo).
        ready_partners = self.env["quantidia.signer"].search(
            [("active", "=", True)]
        ).partner_id
        if (operator == "=" and value) or (operator == "!=" and not value):
            return [("partner_id", "in", ready_partners.ids)]
        return [("partner_id", "not in", ready_partners.ids)]
