# -*- coding: utf-8 -*-
"""Firma Quantidia ad hoc de PDFs existentes, sin template de negocio."""

from odoo import _, api, fields, models, Command
from odoo.exceptions import UserError, ValidationError
from odoo.tools import html_escape


class QuantidiaAdhocSignWizard(models.TransientModel):
    _name = "quantidia.adhoc.sign.wizard"
    _description = "Solicitud ad hoc de firma Quantidia"

    document_line_ids = fields.One2many(
        "quantidia.adhoc.sign.wizard.document", "wizard_id", string="Documentos", required=True, copy=False,
    )
    signer_line_ids = fields.One2many(
        "quantidia.adhoc.sign.wizard.signer", "wizard_id", string="Firmantes", required=True, copy=False,
    )
    reference = fields.Char(string="Referencia", required=True)
    subject = fields.Char(string="Asunto")
    validity = fields.Date(string="Válido hasta")
    set_sign_order = fields.Boolean(default=True)
    activity_id = fields.Many2one("mail.activity", string="Actividad origen", readonly=True)
    source_model = fields.Char(readonly=True)
    source_res_id = fields.Integer(readonly=True)
    uploaded_attachment_ids = fields.Many2many(
        "ir.attachment",
        "quantidia_adhoc_sign_wizard_upload_rel",
        "wizard_id",
        "attachment_id",
        string="Subir PDFs nuevos",
    )

    @api.model
    def _action_open(
        self, document_commands, reference, source_model=False, source_res_id=False, activity_id=False,
    ):
        return {
            "type": "ir.actions.act_window", "name": _("Firmar con Quantidia"),
            "res_model": self._name, "view_mode": "form", "target": "new",
            "context": {
                "default_document_line_ids": document_commands,
                "default_reference": reference,
                "default_subject": _("Solicitud de firma - %s") % reference,
                "default_source_model": source_model or False,
                "default_source_res_id": source_res_id or False,
                "default_activity_id": activity_id or False,
            },
        }

    @api.model
    def action_open_from_attachments(self, attachments):
        """Abre el wizard con los PDF de un adjunto o una selección masiva."""
        attachments = attachments.exists()
        commands = [Command.create({"attachment_id": attachment.id, "sequence": index})
                    for index, attachment in enumerate(attachments, start=1)]
        if not commands:
            raise UserError(_("Seleccione al menos un archivo PDF."))
        origins = {(attachment.res_model, attachment.res_id) for attachment in attachments}
        source_model, source_res_id = (False, False)
        if len(origins) == 1:
            source_model, source_res_id = origins.pop()
        return self._action_open(commands, attachments[:1].name, source_model, source_res_id)

    @api.model
    def action_open_from_documents(self, documents):
        """Entrada desde una selección del módulo Documentos."""
        return self.action_open_from_attachments(documents.mapped("attachment_id"))

    @api.model
    def action_open_from_activity(self, activity):
        """Entrada de la actividad de firma sobre un registro con chatter."""
        activity.ensure_one()
        source = self.env[activity.res_model].browse(activity.res_id).exists()
        if not source:
            raise UserError(_("El registro asociado a la actividad ya no existe."))
        attachments = self.env["ir.attachment"].search([
            ("res_model", "=", source._name),
            ("res_id", "=", source.id),
            ("mimetype", "=", "application/pdf"),
        ])
        commands = [Command.create({"attachment_id": attachment.id, "sequence": index})
                    for index, attachment in enumerate(attachments, start=1)]
        reference = attachments[:1].name or source.display_name
        return self._action_open(
            commands,
            reference,
            source._name,
            source.id,
            activity.id,
        )

    @api.onchange("document_line_ids")
    def _onchange_documents_set_reference(self):
        for wizard in self:
            first_document = wizard.document_line_ids.sorted("sequence")[:1]
            if first_document and not wizard.reference:
                wizard.reference = first_document.attachment_id.name
            if first_document and not wizard.subject:
                wizard.subject = _("Solicitud de firma - %s") % first_document.attachment_id.name

    def _add_uploaded_attachments_to_documents(self):
        """Incluye siempre los PDF cargados, incluso si el cliente no ejecutó onchange."""
        self.ensure_one()
        selected_ids = set(self.document_line_ids.mapped("attachment_id").ids)
        sequence = max(self.document_line_ids.mapped("sequence") or [0])
        commands = []
        for attachment in self.uploaded_attachment_ids:
            if attachment.id not in selected_ids:
                sequence += 1
                commands.append(Command.create({
                    "attachment_id": attachment.id,
                    "sequence": sequence,
                }))
        if commands:
            self.write({"document_line_ids": commands})

    def _get_documents(self):
        self.ensure_one()
        lines = self.document_line_ids.sorted("sequence")
        if not lines:
            raise ValidationError(_("Agregue al menos un PDF."))
        attachment_ids = lines.mapped("attachment_id")
        if len(attachment_ids) != len(lines):
            raise ValidationError(_("Cada línea debe tener un PDF seleccionado."))
        if len(set(attachment_ids.ids)) != len(attachment_ids):
            raise ValidationError(_("Un mismo PDF no puede firmarse dos veces en la misma solicitud."))
        invalid = attachment_ids.filtered(lambda attachment: attachment.mimetype != "application/pdf" or not attachment.datas)
        if invalid:
            raise ValidationError(_("Todos los documentos deben ser PDF con contenido."))
        attachment_ids.check_access("read")
        return attachment_ids

    def _get_signers(self):
        self.ensure_one()
        lines = self.signer_line_ids.sorted("mail_sent_order")
        if not lines:
            raise ValidationError(_("Agregue al menos un firmante Quantidia."))
        if any(not line.partner_id for line in lines):
            raise ValidationError(_("Cada línea de firmante debe tener un contacto."))
        if any(not line.role_id for line in lines):
            raise ValidationError(_("Cada firmante debe tener un rol."))
        if len(set(lines.mapped("partner_id").ids)) != len(lines):
            raise ValidationError(_("Un contacto no puede repetirse como firmante."))
        if any(not line.partner_id.email for line in lines):
            raise ValidationError(_("Todos los firmantes deben tener correo electrónico."))
        ready_partner_ids = set(self.env["quantidia.signer"].search([
            ("partner_id", "in", lines.mapped("partner_id").ids),
            ("active", "=", True),
        ]).mapped("partner_id").ids)
        if any(line.partner_id.id not in ready_partner_ids for line in lines):
            raise ValidationError(_("Todos los firmantes deben tener un perfil Quantidia activo."))
        return lines

    def _create_technical_template(self, main_attachment, name):
        """Crea el wrapper archivado exigido por el esquema de Odoo Sign.

        ``name``: la referencia de la solicitud (resumen de la actividad
        origen cuando existe) — el nombre del template es el que llega a
        las bandejas y los mails, no el nombre del archivo.
        """
        template = self.env["sign.template"].create({
            "name": _("[Quantidia] %s") % (name or main_attachment.name),
            "active": False,
        })
        self.env["sign.document"].create({
            "attachment_id": main_attachment.id,
            "sequence": 1,
            "template_id": template.id,
        })
        return template

    def _link_source_record(self, sign_request):
        """Enlaza al origen únicamente cuando es un registro mail.thread válido."""
        self.ensure_one()
        if not self.source_model or not self.source_res_id:
            return
        model = self.env["ir.model"].sudo()._get(self.source_model)
        if not model or not model.is_mail_thread:
            return
        record = self.env[self.source_model].browse(self.source_res_id).exists()
        if not record:
            return
        sign_request.reference_doc = "%s,%s" % (self.source_model, record.id)
        record.message_post(
            body=_("Se creó una solicitud de firma Quantidia: %s") % sign_request._get_html_link(),
        )

    def _link_uploaded_attachments_to_source(self):
        """Conserva en el registro origen los PDFs que se subieron desde el wizard."""
        self.ensure_one()
        if not self.source_model or not self.source_res_id or not self.uploaded_attachment_ids:
            return
        source = self.env[self.source_model].browse(self.source_res_id).exists()
        if source:
            self.uploaded_attachment_ids.write({
                "res_model": source._name,
                "res_id": source.id,
            })

    def _complete_activity(self, sign_request):
        self.ensure_one()
        if self.activity_id:
            self.activity_id.filtered("active").action_feedback(
                feedback=_("Solicitud Quantidia creada: %s") % sign_request.reference,
            )

    def action_create_request(self):
        """Crea y activa una solicitud Quantidia sin template de negocio."""
        self.ensure_one()
        self._add_uploaded_attachments_to_documents()
        self._link_uploaded_attachments_to_source()
        attachments = self._get_documents()
        signer_lines = self._get_signers()
        main_attachment = attachments[:1]
        # La referencia es lo que el firmante ve en el portal y los mails.
        # Prioridad: lo que el usuario escribio en el wizard, el resumen de
        # la actividad origen (el nombre que le dio al tramite) o el nombre
        # del PDF principal.
        reference = (
            self.reference
            or (self.activity_id.summary if self.activity_id else False)
            or main_attachment.name
        )
        if len(attachments) > 1:
            reference = _(
                "%s (+%s anexo%s)",
                reference,
                len(attachments) - 1,
                "s" if len(attachments) > 2 else "",
            )
        template = self._create_technical_template(main_attachment, reference)
        # Se difiere el inicio: primero se vinculan todos los anexos D11 y
        # recién después se notifica al primer firmante.
        # no_sign_mail: la invitacion nativa sale en el create() ANTES de
        # que la ronda este activa (donde la suprimimos); sin esto el
        # firmante recibe el mail nativo de Odoo en vez del del turno
        # Quantidia. El flag is_mail_sent se marca abajo, tras arrancar la
        # ronda.
        sign_request = self.env["sign.request"].with_context(
            quantidia_skip_immediate_round=True, no_sign_mail=True
        ).create({
            "template_id": template.id,
            "reference": reference,
            "subject": self.subject or _("Solicitud de firma - %s") % reference,
            "validity": self.validity,
            "quantidia_mode": "digital",
            "quantidia_adhoc": True,
            "request_item_ids": [Command.create({
                "partner_id": line.partner_id.id,
                "role_id": line.role_id.id,
                "mail_sent_order": line.mail_sent_order,
            }) for line in signer_lines],
        })
        annexes = self.env["ir.attachment"]
        for attachment in attachments[1:]:
            annexes |= self.env["ir.attachment"].create({
                "name": attachment.name,
                "datas": attachment.datas,
                "mimetype": "application/pdf",
                "res_model": sign_request._name,
                "res_id": sign_request.id,
            })
        if annexes:
            names = ", ".join(html_escape(name) for name in annexes.mapped("name"))
            sign_request.message_post(body=_("Anexos ad hoc para firma Quantidia: %s") % names)
        self._link_source_record(sign_request)
        sign_request._start_digital_round_immediate()
        # Ronda activa: el override de send_signature_accesses no manda el
        # mail nativo pero SI marca is_mail_sent (la lista del portal /my
        # filtra por ese flag).
        sign_request.request_item_ids.send_signature_accesses()
        self._complete_activity(sign_request)
        return {
            "type": "ir.actions.act_window", "name": sign_request.reference,
            "res_model": "sign.request", "res_id": sign_request.id,
            "view_mode": "form", "target": "current",
        }


class QuantidiaAdhocSignWizardDocument(models.TransientModel):
    _name = "quantidia.adhoc.sign.wizard.document"
    _description = "Documento ad hoc de firma Quantidia"
    _order = "sequence, id"

    wizard_id = fields.Many2one("quantidia.adhoc.sign.wizard", required=True, ondelete="cascade")
    sequence = fields.Integer(default=1, required=True)
    attachment_id = fields.Many2one(
        "ir.attachment", string="PDF", required=True,
        domain="[('mimetype', '=', 'application/pdf')]",
    )
    name = fields.Char(string="Name", related="attachment_id.name", readonly=True)
    mimetype = fields.Char(related="attachment_id.mimetype", readonly=True)


class QuantidiaAdhocSignWizardSigner(models.TransientModel):
    _name = "quantidia.adhoc.sign.wizard.signer"
    _description = "Firmante ad hoc Quantidia"
    _order = "mail_sent_order, id"

    wizard_id = fields.Many2one("quantidia.adhoc.sign.wizard", required=True, ondelete="cascade")
    mail_sent_order = fields.Integer(string="Orden", default=1, required=True)
    role_id = fields.Many2one(
        "sign.item.role",
        string="Rol",
        required=True,
        default=lambda self: self.env.ref("sign.sign_item_role_default"),
    )
    partner_id = fields.Many2one("res.partner", string="Firmante", required=True)
    email = fields.Char(related="partner_id.email", readonly=True)
    quantidia_digital_ready = fields.Boolean(string="Firmante Quantidia", compute="_compute_quantidia_digital_ready")

    @api.depends("partner_id")
    def _compute_quantidia_digital_ready(self):
        partner_ids = self.mapped("partner_id").ids
        ready_partner_ids = set(self.env["quantidia.signer"].search([
            ("partner_id", "in", partner_ids), ("active", "=", True),
        ]).mapped("partner_id").ids)
        for line in self:
            line.quantidia_digital_ready = line.partner_id.id in ready_partner_ids
