# -*- coding: utf-8 -*-
import base64
import io

from odoo.tests.common import TransactionCase
from reportlab.pdfgen import canvas


class TestQuantidiaMailRender(TransactionCase):
    def test_turn_body_renders(self):
        """El cuerpo del mail de turno debe renderizar sin placeholders crudos.

        Regresion: el body_html usa motor QWeb (t-out / t-attf-href); las
        sintaxis {{ }} y ${ } no se renderizan en el cuerpo y el boton
        llegaba con la URL rota.
        """
        partner = self.env["res.partner"].create(
            {"name": "Laura Prueba", "email": "laura@x.com"}
        )
        self.env["quantidia.signer"].create(
            {"partner_id": partner.id, "given_name": "Laura", "family_name": "P",
             "username": "laura", "email": "laura@x.com"}
        )
        buf = io.BytesIO()
        c = canvas.Canvas(buf)
        c.drawString(100, 700, "x")
        c.save()
        att = self.env["ir.attachment"].create({
            "name": "doc.pdf",
            "datas": base64.b64encode(buf.getvalue()),
            "mimetype": "application/pdf",
            "res_model": "res.partner",
            "res_id": partner.id,
        })
        wiz = self.env["quantidia.adhoc.sign.wizard"].create({
            "reference": "Render mail turno",
            "source_model": "res.partner",
            "source_res_id": partner.id,
            "document_line_ids": [(0, 0, {"attachment_id": att.id, "sequence": 1})],
            "signer_line_ids": [(0, 0, {"partner_id": partner.id, "mail_sent_order": 1})],
        })
        wiz.action_create_request()
        request = self.env["sign.request"].search(
            [("reference", "=", "Render mail turno")]
        )
        tracking = request.quantidia_digital_signature_ids[:1]
        template = self.env.ref("sign_quantidia.mail_template_quantidia_turn")
        values = template._render_field(
            "body_html", tracking.ids, engine="qweb",
            add_context={"object": tracking},
        )[tracking.id]
        self.assertNotIn("{{", values)
        self.assertNotIn("${", values)
        self.assertIn("Laura Prueba", values)
        self.assertIn("Render mail turno", values)
        self.assertIn("/quantidia/sign/%s/" % request.id, values)
