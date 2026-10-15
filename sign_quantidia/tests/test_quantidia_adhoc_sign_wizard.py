# -*- coding: utf-8 -*-
import base64
import io

from reportlab.pdfgen import canvas

from odoo.tests.common import TransactionCase


class TestQuantidiaAdhocSignWizard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.signer = cls.env["res.partner"].create({
            "name": "Firmante ad hoc",
            "email": "firmante.adhoc@example.com",
        })
        cls.env["quantidia.signer"].create({"partner_id": cls.signer.id})
        cls.main_attachment = cls._create_pdf_attachment("principal-ad-hoc.pdf")
        cls.annex_attachment = cls._create_pdf_attachment("anexo-ad-hoc.pdf")

    @classmethod
    def _create_pdf_attachment(cls, name):
        output = io.BytesIO()
        pdf = canvas.Canvas(output)
        pdf.drawString(72, 720, name)
        pdf.save()
        return cls.env["ir.attachment"].create({
            "name": name,
            "mimetype": "application/pdf",
            "raw": output.getvalue(),
        })

    def test_open_from_multiple_attachments(self):
        action = self.env["quantidia.adhoc.sign.wizard"].action_open_from_attachments(
            self.main_attachment | self.annex_attachment
        )

        self.assertEqual(action["res_model"], "quantidia.adhoc.sign.wizard")
        self.assertEqual(len(action["context"]["default_document_line_ids"]), 2)
        self.assertEqual(action["context"]["default_reference"], self.main_attachment.name)

    def test_quantidia_activity_opens_the_adhoc_wizard_with_record_pdfs(self):
        source = self.env["res.partner"].create({"name": "Registro origen"})
        attachment = self._create_pdf_attachment("desde-registro.pdf")
        attachment.write({"res_model": source._name, "res_id": source.id})
        activity = self.env["mail.activity"].create({
            "activity_type_id": self.env.ref(
                "sign_quantidia.mail_activity_type_quantidia_signature"
            ).id,
            "res_model_id": self.env["ir.model"]._get(source._name).id,
            "res_id": source.id,
        })

        action = self.env["sign.template"].with_context(
            default_activity_id=activity.id,
        ).open_sign_send_dialog()

        self.assertEqual(action["res_model"], "quantidia.adhoc.sign.wizard")
        self.assertEqual(action["context"]["default_activity_id"], activity.id)
        self.assertEqual(len(action["context"]["default_document_line_ids"]), 1)

    def test_create_request_keeps_one_technical_main_document_and_d11_annex(self):
        wizard = self.env["quantidia.adhoc.sign.wizard"].create({
            "reference": "Solicitud ad hoc multi-PDF",
            "document_line_ids": [
                (0, 0, {"attachment_id": self.main_attachment.id, "sequence": 1}),
                (0, 0, {"attachment_id": self.annex_attachment.id, "sequence": 2}),
            ],
            "signer_line_ids": [
                (0, 0, {"partner_id": self.signer.id, "mail_sent_order": 1}),
            ],
        })

        action = wizard.action_create_request()
        request = self.env["sign.request"].browse(action["res_id"])
        annexes = self.env["ir.attachment"].search([
            ("res_model", "=", "sign.request"),
            ("res_id", "=", request.id),
            ("mimetype", "=", "application/pdf"),
        ])

        self.assertTrue(request.quantidia_adhoc)
        self.assertFalse(request.template_id.active)
        self.assertEqual(len(request.template_document_ids), 1)
        self.assertEqual(request.template_document_ids.attachment_id.name, self.main_attachment.name)
        self.assertEqual(annexes.name, self.annex_attachment.name)
        self.assertEqual(request.quantidia_digital_signature_ids.state, "pending")

    def test_create_request_preserves_the_signer_order(self):
        signer_second = self.env["res.partner"].create({
            "name": "Segundo firmante",
            "email": "segundo.adhoc@example.com",
        })
        signer_third = self.env["res.partner"].create({
            "name": "Tercer firmante",
            "email": "tercero.adhoc@example.com",
        })
        self.env["quantidia.signer"].create([
            {"partner_id": signer_second.id},
            {"partner_id": signer_third.id},
        ])
        wizard = self.env["quantidia.adhoc.sign.wizard"].create({
            "reference": "Solicitud con orden de firma",
            "document_line_ids": [
                (0, 0, {"attachment_id": self.main_attachment.id, "sequence": 1}),
            ],
            "signer_line_ids": [
                (0, 0, {"partner_id": self.signer.id, "mail_sent_order": 3}),
                (0, 0, {"partner_id": signer_second.id, "mail_sent_order": 1}),
                (0, 0, {"partner_id": signer_third.id, "mail_sent_order": 2}),
            ],
        })

        action = wizard.action_create_request()
        request = self.env["sign.request"].browse(action["res_id"])
        request_items = request.request_item_ids.sorted("mail_sent_order")

        self.assertEqual(
            [item.partner_id.id for item in request_items],
            [signer_second.id, signer_third.id, self.signer.id],
        )
        self.assertEqual(request_items.mapped("mail_sent_order"), [1, 2, 3])

    def test_create_request_closes_activity_with_feedback(self):
        source = self.env["res.partner"].create({"name": "Registro con actividad"})
        activity = self.env["mail.activity"].create({
            "activity_type_id": self.env.ref(
                "sign_quantidia.mail_activity_type_quantidia_signature"
            ).id,
            "res_model_id": self.env["ir.model"]._get(source._name).id,
            "res_id": source.id,
        })
        wizard = self.env["quantidia.adhoc.sign.wizard"].create({
            "reference": "Solicitud desde actividad",
            "activity_id": activity.id,
            "document_line_ids": [
                (0, 0, {"attachment_id": self.main_attachment.id, "sequence": 1}),
            ],
            "signer_line_ids": [
                (0, 0, {"partner_id": self.signer.id, "mail_sent_order": 1}),
            ],
        })

        wizard.action_create_request()

        # Odoo 19 archiva la actividad hecha en vez de borrarla segun build:
        # con que quede cerrada (inexistente o archivada) alcanza.
        self.assertFalse(activity.exists() and activity.active)
        self.assertTrue(any(
            "Solicitud Quantidia creada: Solicitud desde actividad" in body
            for body in source.message_ids.mapped("body")
        ))

    def test_completed_round_publishes_signed_pdfs_to_source_record(self):
        from odoo import Command

        source = self.env["res.partner"].create({"name": "Registro con PDFs"})
        attachment = self._create_pdf_attachment("origen-firma.pdf")
        attachment.write({"res_model": source._name, "res_id": source.id})
        activity = self.env["mail.activity"].create({
            "activity_type_id": self.env.ref(
                "sign_quantidia.mail_activity_type_quantidia_signature"
            ).id,
            "res_model_id": self.env["ir.model"]._get(source._name).id,
            "res_id": source.id,
        })
        wizard = self.env["quantidia.adhoc.sign.wizard"].create({
            "reference": "Publicacion al origen",
            "activity_id": activity.id,
            # En la UI vienen por contexto de la actividad; a mano para el test.
            "source_model": source._name,
            "source_res_id": source.id,
            "document_line_ids": [
                (0, 0, {"attachment_id": attachment.id, "sequence": 1}),
            ],
            "signer_line_ids": [
                (0, 0, {"partner_id": self.signer.id, "mail_sent_order": 1}),
            ],
        })
        wizard.action_create_request()
        request = self.env["sign.request"].search(
            [("reference", "=", "Publicacion al origen")]
        )

        # Simular la ronda completa: firma principal + anexo firmado.
        main_att = self.env["ir.attachment"].create({
            "name": "firmado.pdf",
            "datas": attachment.datas,
            "mimetype": "application/pdf",
            "res_model": "sign.request",
            "res_id": request.id,
        })
        request.write({"quantidia_signed_attachment_id": main_att.id})
        self.env["ir.attachment"].create({
            "name": "anexo_firmado.pdf",
            "datas": attachment.datas,
            "mimetype": "application/pdf",
            "res_model": "sign.request",
            "res_id": request.id,
            "res_field": "quantidia_annex_signed",
        })
        request.quantidia_digital_signature_ids.write({"state": "signed"})
        request._check_digital_round_complete()

        source_message = source.message_ids.filtered(
            lambda m: "Firma Quantidia completada" in (m.body or "")
        )
        self.assertTrue(source_message)
        published = source_message.attachment_ids.mapped("name")
        # Con el conteo de uploads la ronda cierra con el ULTIMO documento
        # del grupo: al publicar, todo esta en version final.
        self.assertIn("firmado.pdf", published)
        self.assertIn("anexo_firmado.pdf", published)

        # El anexo que llega DESPUES del cierre se publica solo, con nota
        # propia, como copia al registro origen.
        request._quantidia_store_signed_pdf(
            base64.b64decode(attachment.datas),
            filename="anexo_firmado.pdf",
            annex=True,
        )
        annex_messages = self.env["mail.message"].search([
            ("model", "=", source._name),
            ("res_id", "=", source.id),
            ("body", "like", "Anexo firmado (Quantidia)"),
        ])
        self.assertTrue(annex_messages)
        annex_message = annex_messages[0]
        # Nombre normalizado: una sola terminacion "_signed".
        self.assertIn(
            "anexo_firmado_signed.pdf",
            annex_message.attachment_ids.mapped("name"),
        )
