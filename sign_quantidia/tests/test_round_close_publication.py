# -*- coding: utf-8 -*-
"""Cierre real de ronda por uploads: una sola publicacion al origen.

Simula el flujo completo del SDK: uploads del principal y anexos con
conteo por firmante, y verifica que el registro origen recibe UN solo
mensaje con TODOS los PDFs firmados (sin los posteos/mails nativos).
"""
import base64
import io

from odoo.tests.common import TransactionCase
from odoo.tools.translate import _
from reportlab.pdfgen import canvas


class TestRoundClosePublication(TransactionCase):
    def _pdf(self):
        buf = io.BytesIO()
        c = canvas.Canvas(buf)
        c.drawString(100, 700, "x")
        c.save()
        return buf.getvalue()

    def test_single_publication_on_close(self):
        source = self.env["res.partner"].create({"name": "Origen Unico"})
        signer = self.env["res.partner"].create(
            {"name": "Firma Uno", "email": "uno@x.com"}
        )
        self.env["quantidia.signer"].create({
            "partner_id": signer.id, "given_name": "Firma", "family_name": "Uno",
            "username": "uno", "email": "uno@x.com",
        })
        pdf = self._pdf()
        atts = self.env["ir.attachment"].create([{
            "name": "main.pdf",
            "datas": base64.b64encode(pdf),
            "mimetype": "application/pdf",
            "res_model": source._name,
            "res_id": source.id,
        }, {
            "name": "anexo.pdf",
            "datas": base64.b64encode(pdf),
            "mimetype": "application/pdf",
            "res_model": source._name,
            "res_id": source.id,
        }])
        wiz = self.env["quantidia.adhoc.sign.wizard"].create({
            "reference": "Publicacion unica",
            "source_model": source._name,
            "source_res_id": source.id,
            "document_line_ids": [
                (0, 0, {"attachment_id": atts[0].id, "sequence": 1}),
                (0, 0, {"attachment_id": atts[1].id, "sequence": 2}),
            ],
            "signer_line_ids": [
                (0, 0, {"partner_id": signer.id, "mail_sent_order": 1}),
            ],
        })
        wiz.action_create_request()
        request = self.env["sign.request"].search(
            [("reference", "like", "Publicacion unica%")]
        )
        item = self.env["sign.request.item"].search(
            [("sign_request_id", "=", request.id)], limit=1
        )
        self.assertTrue(item, "el wizard debe crear el item del firmante")

        # Uploads del SDK: principal primero, anexo despues (cierra la ronda)
        request._quantidia_store_signed_pdf(
            pdf, filename="main_signed.pdf", request_item_id=item.id
        )
        request._quantidia_store_signed_pdf(
            pdf, filename="anexo_signed.pdf",
            request_item_id=item.id, annex=True,
        )
        request.invalidate_recordset()

        self.assertEqual(request.state, "signed")
        self.assertEqual(request.quantidia_status, "signed")

        msgs = self.env["mail.message"].search([
            ("model", "=", source._name), ("res_id", "=", source.id),
        ])
        bodies = "\n".join(m.body or "" for m in msgs)
        # UN solo mensaje de publicacion, con los dos documentos.
        self.assertEqual(
            bodies.count("Firma Quantidia completada"), 1,
            "debe haber exactamente un mensaje de publicacion",
        )
        # Sin posteos nativos al origen.
        self.assertNotIn("se adjuntó una copia", bodies)
        self.assertNotIn("fully signed", bodies)
        completed = msgs.filtered(
            lambda m: "Firma Quantidia completada" in (m.body or "")
        )
        self.assertEqual(
            sorted(completed.attachment_ids.mapped("name")),
            ["anexo_signed.pdf", "main_signed.pdf"],
        )
        # Mail de cierre a cada firmante: con TODOS los PDFs firmados.
        close_mail = self.env["mail.mail"].search([
            ("subject", "like", "Publicacion unica%firma digital completada"),
        ], limit=1)
        self.assertTrue(close_mail, "debe generarse el mail de cierre")
        self.assertEqual(
            sorted(close_mail.attachment_ids.mapped("name")),
            ["anexo_signed.pdf", "main_signed.pdf"],
        )
