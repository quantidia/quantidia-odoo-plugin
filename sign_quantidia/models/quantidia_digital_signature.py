# -*- coding: utf-8 -*-
from markupsafe import Markup

from odoo import api, fields, models


class QuantidiaDigitalSignature(models.Model):
    """Tracking de la firma digital de cada participante en la ronda Quantidia.

    Una fila por firmante por request. Se crea al arrancar la ronda digital
    (post-_sign() o inmediata si no hay campos). state=pending → signed.
    """

    _name = "quantidia.digital.signature"
    _description = "Firma digital Quantidia (tracking por firmante)"
    _order = "sign_request_id, sequence, partner_id"

    sign_request_id = fields.Many2one(
        comodel_name="sign.request",
        string="Request",
        required=True,
        ondelete="cascade",
        index=True,
    )
    request_item_id = fields.Many2one(
        comodel_name="sign.request.item",
        string="Item",
        required=True,
        ondelete="cascade",
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Partner",
        related="request_item_id.partner_id",
        store=True,
        readonly=True,
    )
    sequence = fields.Integer(
        string="Secuencia de firma",
        help="Orden serial en que debe firmar (1, 2, 3...)",
        default=1,
    )
    state = fields.Selection(
        [
            ("waiting", "Esperando turno"),
            ("pending", "Turno actual"),
            ("signed", "Firmado"),
            ("rejected", "Rechazado"),
            ("cancelled", "Cancelado"),
        ],
        string="Estado digital",
        default="waiting",
        tracking=100,  # Odoo 19: tracking es un número (prioridad), no boolean
    )
    signing_date = fields.Datetime(string="Fecha firma digital", readonly=True)
    expected_documents = fields.Integer(
        string="Documentos a subir",
        default=0,
        help="Tamaño del bundle que este firmante firma con el SDK "
        "(principal + anexos). Se calcula en su primer upload.",
    )
    upload_count = fields.Integer(
        string="Documentos subidos",
        default=0,
        help="Uploads recibidos de este firmante en su turno. El tracking "
        "pasa a signed solo cuando upload_count alcanza "
        "expected_documents (ultimo documento del grupo).",
    )
    signer_profile_id = fields.Many2one(
        comodel_name="quantidia.signer",
        string="Perfil Quantidia",
        domain="[('partner_id', '=', partner_id)]",
    )

    def _valid_field_parameter(self, field, name):
        """Override para permitir el parametro 'tracking' en Odoo 19.

        Odoo 19 prohibe 'tracking' por defecto, pero podemos permitirlo
        sobrescribiendo este metodo.
        """
        if name == "tracking":
            return True
        return super()._valid_field_parameter(field, name)

    def _notify_turn(self):
        """Fase 2b: avisa al firmante que llegó su turno de firma digital.

        Mail a todos los firmantes con el enlace del portal (D9) y, para
        usuarios internos, además una actividad sobre el sign.request (lo
        ven en su bandeja de Odoo y pueden firmar desde el chatter).
        Idempotente respecto al estado: solo notifica trackings pending.
        """
        template = self.env.ref(
            "sign_quantidia.mail_template_quantidia_turn", raise_if_not_found=False
        )
        for tracking in self:
            if tracking.state != "pending":
                continue
            if template and tracking.partner_id.email:
                template.sudo().send_mail(tracking.id, force_send=False)
            internal = tracking.partner_id.user_ids.filtered(
                lambda u: u.active and not u.share
            )
            if internal:
                # Mensaje interno en el chatter, notificado al inbox del
                # firmante (queda en el historial del documento).
                sign_request = tracking.sign_request_id
                link = "%s/quantidia/sign/%s/%s" % (
                    tracking.get_base_url(),
                    sign_request.id,
                    tracking.request_item_id.access_token,
                )
                sign_request.message_post(
                    # Markup: message_post escapa los str planos (Odoo 19)
                    body=Markup(
                        "Es tu turno de firma digital de este documento. "
                        "<p><a href='%s' style='background-color:#0d6efd;color:#fff;"
                        "padding:10px 18px;border-radius:8px;text-decoration:none;"
                        "font-weight:600;'>Firmar digitalmente</a></p>" % link
                    ),
                    partner_ids=[tracking.partner_id.id],
                    subtype_xmlid="mail.mt_comment",
                )
                self.env["mail.activity"].sudo().create(
                    {
                        "res_model_id": self.env["ir.model"]
                        .sudo()
                        ._get_id("sign.request"),
                        "res_id": tracking.sign_request_id.id,
                        "user_id": internal[:1].id,
                        "summary": "Firma digital pendiente: %s"
                        % tracking.sign_request_id.reference,
                        "note": "Es tu turno de firma digital. Firmá desde este "
                        "documento (botón \"Firmar documentos\") o desde tu enlace "
                        "de firma digital.",
                        "activity_type_id": self.env.ref(
                            "mail.mail_activity_data_todo"
                        ).id,
                    }
                )

    def action_mark_signed(self):
        """Marca este tracking como firmado, activa al siguiente firmante y verifica el cierre."""
        self.ensure_one()
        self.write({"state": "signed", "signing_date": fields.Datetime.now()})
        # Reflejar la firma digital en el item NATIVO: el firmante pasa a
        # verde (lista de firmantes y visor del documento) en el momento en
        # que firma con Quantidia. En Flujo A el item ya esta completado
        # electronicamente y esto no hace nada.
        if self.request_item_id and self.request_item_id.state == "sent":
            # Contexto quantidia_digital_completion: al completarse el item,
            # _post_fill_request_item puede disparar _sign() del request; sin
            # el flag ese _sign publica los mensajes/mails nativos al
            # registro origen (duplicando la publicacion Quantidia).
            self.request_item_id.sudo().with_context(
                quantidia_digital_completion=True
            )._post_fill_request_item()
        # Activar al siguiente firmante en la secuencia serial
        self.sign_request_id._activate_next_signer(self.sequence)
        # Verificar si la ronda digital esta completa
        self.sign_request_id._check_digital_round_complete()

    def action_mark_rejected(self, reason=None):
        """Marca este tracking como rechazado y cancela la ronda digital."""
        self.ensure_one()
        self.write({
            "state": "rejected",
            "signing_date": fields.Datetime.now(),
        })
        # Rechazo cancela toda la ronda digital
        self.sign_request_id._cancel_digital_round(reason=reason)

    def action_cancel(self):
        self.write({"state": "cancelled"})
