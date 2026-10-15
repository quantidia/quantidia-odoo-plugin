# -*- coding: utf-8 -*-
"""Guardas server-side de la ronda digital Quantidia (D0, deuda tecnica).

1. Con ronda digital activa, la ruta nativa de validacion electronica
   (sign.request.item.sign) no debe completar el item: la firma del item es
   la digital de Quantidia (tracking serial).
2. El search de ``need_my_signature`` debe alinearse con el compute: con
   ronda activa "me toca" solo con tracking pending.
"""

import io

from reportlab.pdfgen import canvas

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase


class TestQuantidiaDigitalRoundGuard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.default_role = cls.env.ref("sign.sign_item_role_default")
        cls.signers = cls.env["res.partner"].create(
            [
                {"name": "Guardia uno", "email": "guardia.uno@example.com"},
                {"name": "Guardia dos", "email": "guardia.dos@example.com"},
            ]
        )

    @classmethod
    def _create_pdf_attachment(cls, name):
        output = io.BytesIO()
        pdf = canvas.Canvas(output)
        pdf.drawString(72, 720, name)
        pdf.showPage()
        pdf.save()
        return cls.env["ir.attachment"].create(
            {
                "name": name,
                "mimetype": "application/pdf",
                "raw": output.getvalue(),
            }
        )

    def _create_digital_request(self, partners):
        attachment = self._create_pdf_attachment("guard-d0.pdf")
        template = self.env["sign.template"].create(
            {"name": "Template D0", "active": False}
        )
        self.env["sign.document"].create(
            {
                "attachment_id": attachment.id,
                "sequence": 1,
                "template_id": template.id,
            }
        )
        return self.env["sign.request"].create(
            {
                "template_id": template.id,
                "reference": "Test guard D0",
                "quantidia_mode": "digital",
                "request_item_ids": [
                    (0, 0, {"partner_id": partner.id, "role_id": self.default_role.id})
                    for partner in partners
                ],
            }
        )

    def test_native_sign_route_is_blocked_while_round_is_active(self):
        request = self._create_digital_request(self.signers[:1])
        item = request.request_item_ids[0]

        self.assertTrue(request.quantidia_digital_round)
        self.assertEqual(item.state, "sent")
        with self.assertRaises(UserError) as ctx:
            # Ruta nativa: el controller llama item.sudo().sign(signature)
            item.sudo().sign({})
        self.assertIn("Quantidia", str(ctx.exception))

    def test_need_my_signature_search_follows_the_digital_turn(self):
        request = self._create_digital_request(self.signers)
        user = self.env["res.users"].create(
            {
                "name": "Usuario guardia dos",
                "login": "guardia_dos@test.example",
                "partner_id": self.signers[1].id,
                # Odoo 19: el campo es group_ids (antes groups_id).
                "group_ids": [
                    (4, self.env.ref("sign.group_sign_user").id),
                ],
            }
        )
        user_env = self.env(user=user)
        search = lambda: user_env["sign.request"].search(
            [("need_my_signature", "in", [True])]
        )

        # Ronda serial: el turno es del primer firmante; el segundo queda
        # fuera aunque su item nativo siga 'sent' (semantica del compute).
        self.assertNotIn(request, search())

        # Turno del segundo: la busqueda lo incluye.
        request.quantidia_digital_signature_ids.filtered(
            lambda t: t.partner_id.id == self.signers[1].id
        ).write({"state": "pending"})
        request.quantidia_digital_signature_ids.filtered(
            lambda t: t.partner_id.id == self.signers[0].id
        ).write({"state": "signed"})
        self.assertIn(request, search())

        # Ya firmo: vuelve a quedar fuera.
        request.quantidia_digital_signature_ids.filtered(
            lambda t: t.partner_id.id == self.signers[1].id
        ).write({"state": "signed"})
        self.assertNotIn(request, search())
