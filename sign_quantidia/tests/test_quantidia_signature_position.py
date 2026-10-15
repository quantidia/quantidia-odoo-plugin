# -*- coding: utf-8 -*-
"""Posicionamiento de la firma digital Quantidia (signatureRect del SDK).

Cubre las dos fuentes de posicion:

1. El marcador "Firma digital Quantidia" colocado en el template (match por
   rol y, para el resto, orden z entre marcadores libres).
2. La grilla automatica de 3 firmas por linea (orden z) cuando no hay
   marcadores (documentos sin template, ad hoc).
"""

import io

from reportlab.pdfgen import canvas

from odoo.tests.common import TransactionCase

# A4 en puntos PDF (lo que produce reportlab por defecto).
A4_WIDTH = 595.2755905511812
A4_HEIGHT = 841.8897637795277


class TestQuantidiaSignaturePosition(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.quantidia_type = cls.env.ref(
            "sign_quantidia.sign_item_type_quantidia_signature"
        )
        cls.default_role = cls.env.ref("sign.sign_item_role_default")
        cls.signers = cls.env["res.partner"].create(
            [
                {"name": "Firmante uno", "email": "uno@example.com"},
                {"name": "Firmante dos", "email": "dos@example.com"},
                {"name": "Firmante tres", "email": "tres@example.com"},
                {"name": "Firmante cuatro", "email": "cuatro@example.com"},
            ]
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @classmethod
    def _create_pdf_attachment(cls, name, pages=1):
        output = io.BytesIO()
        pdf = canvas.Canvas(output)
        for page in range(pages):
            pdf.drawString(72, 720, "%s pag %s" % (name, page + 1))
            pdf.showPage()
        pdf.save()
        return cls.env["ir.attachment"].create(
            {
                "name": name,
                "mimetype": "application/pdf",
                "raw": output.getvalue(),
            }
        )

    def _create_template(self, attachment, markers=()):
        template = self.env["sign.template"].create(
            {"name": "Template %s" % attachment.name, "active": False}
        )
        document = self.env["sign.document"].create(
            {
                "attachment_id": attachment.id,
                "sequence": 1,
                "template_id": template.id,
            }
        )
        for marker in markers:
            self.env["sign.item"].create(
                {
                    "document_id": document.id,
                    "type_id": self.quantidia_type.id,
                    "page": marker.get("page", 1),
                    "posX": marker.get("posX", 0.1),
                    "posY": marker.get("posY", 0.1),
                    "width": marker.get("width", 0.2),
                    "height": marker.get("height", 0.08),
                    "responsible_id": marker.get("responsible_id").id
                    if marker.get("responsible_id")
                    else False,
                }
            )
        return template

    def _create_request(self, template, signers):
        """Crea el request digital con un item por (partner, role)."""
        return self.env["sign.request"].create(
            {
                "template_id": template.id,
                "reference": "Test posicion",
                "quantidia_mode": "digital",
                "request_item_ids": [
                    (
                        0,
                        0,
                        {"partner_id": partner.id, "role_id": role.id},
                    )
                    for partner, role in signers
                ],
            }
        )

    def _assert_rects_do_not_overlap(self, rects):
        for i, first in enumerate(rects):
            for second in rects[i + 1:]:
                if first["page"] != second["page"]:
                    continue
                overlaps = (
                    first["x"] < second["x"] + second["w"]
                    and second["x"] < first["x"] + first["w"]
                    and first["y"] < second["y"] + second["h"]
                    and second["y"] < first["y"] + first["h"]
                )
                self.assertFalse(
                    overlaps,
                    "Los recuadros se superponen: %s vs %s" % (first, second),
                )

    def _assert_rect_inside_page(self, rect, page_size=(A4_WIDTH, A4_HEIGHT)):
        page_w, page_h = page_size
        self.assertGreaterEqual(rect["x"], 0)
        self.assertGreaterEqual(rect["y"], 0)
        self.assertLessEqual(rect["x"] + rect["w"], page_w + 0.01)
        self.assertLessEqual(rect["y"] + rect["h"], page_h + 0.01)

    # ------------------------------------------------------------------
    # Marcador del template
    # ------------------------------------------------------------------
    def test_marker_of_my_role_defines_my_rect(self):
        role = self.env["sign.item.role"].create({"name": "Director"})
        attachment = self._create_pdf_attachment("con-marcador.pdf", pages=2)
        template = self._create_template(
            attachment,
            markers=[{
                "page": 2,
                "posX": 0.25,
                "posY": 0.3,
                "width": 0.2,
                "height": 0.08,
                "responsible_id": role,
            }],
        )
        request = self._create_request(
            template, [(self.signers[0], role), (self.signers[1], self.default_role)]
        )

        rect = request._quantidia_get_signature_rect(request.request_item_ids[0])

        self.assertEqual(rect["page"], 2)
        self.assertAlmostEqual(rect["x"], 0.25 * A4_WIDTH, places=1)
        # posY 0.3 desde ARRIBA -> el SDK recibe y espejado (origen abajo).
        self.assertAlmostEqual(rect["y"], 0.7 * A4_HEIGHT, places=1)
        self.assertAlmostEqual(rect["w"], 0.2 * A4_WIDTH, places=1)
        self.assertAlmostEqual(rect["h"], 0.08 * A4_HEIGHT, places=1)
        self._assert_rect_inside_page(rect)

    def test_signer_without_marker_lands_on_grid_below_the_last_marker(self):
        role = self.env["sign.item.role"].create({"name": "Presidente"})
        attachment = self._create_pdf_attachment("grilla-tras-marcador.pdf")
        template = self._create_template(
            attachment,
            markers=[{"posX": 0.4, "posY": 0.6, "responsible_id": role}],
        )
        request = self._create_request(
            template, [(self.signers[0], role), (self.signers[1], self.default_role)]
        )

        marker_rect = request._quantidia_get_signature_rect(request.request_item_ids[0])
        grid_rect = request._quantidia_get_signature_rect(request.request_item_ids[1])

        # La grilla arranca debajo del marcador, en la misma pagina (con
        # origen abajo-izquierda, "debajo" es y menor).
        self.assertEqual(grid_rect["page"], marker_rect["page"])
        self.assertLessEqual(grid_rect["y"], marker_rect["y"] - marker_rect["h"])
        self._assert_rect_inside_page(grid_rect)
        self._assert_rects_do_not_overlap([marker_rect, grid_rect])

    def test_free_markers_fill_in_z_order(self):
        """Sin match de rol, los marcadores se asignan en orden de lectura."""
        attachment = self._create_pdf_attachment("orden-z.pdf")
        template = self._create_template(
            attachment,
            markers=[
                {"posX": 0.55, "posY": 0.1},  # arriba a la derecha
                {"posX": 0.1, "posY": 0.4},   # mas abajo a la izquierda
            ],
        )
        # Ambos marcadores sin responsable y roles default: quedan libres y
        # se reparten en orden z (posY ascendente: el de arriba primero).
        request = self._create_request(
            template,
            [(self.signers[0], self.default_role), (self.signers[1], self.default_role)],
        )

        first = request._quantidia_get_signature_rect(request.request_item_ids[0])
        second = request._quantidia_get_signature_rect(request.request_item_ids[1])

        # Orden z: menor posY (mas arriba) primero; y espejado (origen
        # abajo): posY 0.1 -> 0.9 de la altura.
        self.assertAlmostEqual(first["y"], 0.9 * A4_HEIGHT, places=1)
        self.assertAlmostEqual(second["y"], 0.6 * A4_HEIGHT, places=1)
        self._assert_rects_do_not_overlap([first, second])

    # ------------------------------------------------------------------
    # Grilla automatica (sin marcadores)
    # ------------------------------------------------------------------
    def test_grid_without_markers_is_three_per_line_in_z_order(self):
        attachment = self._create_pdf_attachment("sin-marcador.pdf")
        template = self._create_template(attachment)
        request = self._create_request(
            template,
            [
                (partner, self.default_role)
                for partner in (self.signers[0], self.signers[1], self.signers[2], self.signers[3])
            ],
        )

        rects = [
            request._quantidia_get_signature_rect(item)
            for item in request.request_item_ids
        ]

        self.assertTrue(all(rects))
        for rect in rects:
            self.assertEqual(rect["page"], 1)
            self._assert_rect_inside_page(rect)
            self.assertAlmostEqual(rect["h"], 120.0, places=1)
        # Linea 1 (abajo): firmantes 1-3 comparten Y, X creciente.
        self.assertAlmostEqual(rects[0]["y"], rects[1]["y"], places=1)
        self.assertAlmostEqual(rects[1]["y"], rects[2]["y"], places=1)
        self.assertLess(rects[0]["x"], rects[1]["x"])
        self.assertLess(rects[1]["x"], rects[2]["x"])
        # Linea 2 (orden z): el 4to arranca a la izquierda y una linea
        # arriba (con origen abajo, arriba = y menor).
        self.assertAlmostEqual(rects[3]["x"], rects[0]["x"], places=1)
        self.assertAlmostEqual(rects[3]["y"], rects[0]["y"] - rects[0]["h"] - 16.0, places=1)
        self._assert_rects_do_not_overlap(rects)

    def test_grid_on_last_page_for_multi_page_document(self):
        attachment = self._create_pdf_attachment("multipagina.pdf", pages=3)
        template = self._create_template(attachment)
        request = self._create_request(
            template, [(self.signers[0], self.default_role)]
        )

        rect = request._quantidia_get_signature_rect(request.request_item_ids[0])

        self.assertEqual(rect["page"], 3)
        self._assert_rect_inside_page(rect)

    def test_unreadable_pdf_falls_back_to_sdk_default(self):
        attachment = self._create_pdf_attachment("roto.pdf")
        template = self._create_template(attachment)
        request = self._create_request(
            template, [(self.signers[0], self.default_role)]
        )
        # sign.document valida el PDF al crearse: se corrompe DESPUES, que es
        # el escenario real (archivo roto entre la carga y la firma).
        attachment.write({"raw": b"not-a-real-pdf"})

        self.assertIsNone(
            request._quantidia_get_signature_rect(request.request_item_ids[0])
        )

    # ------------------------------------------------------------------
    # Integracion con el circuito nativo
    # ------------------------------------------------------------------
    def test_marker_only_template_starts_immediate_round(self):
        """Un template cuyo unico campo es el marcador se comporta como sin campos."""
        attachment = self._create_pdf_attachment("solo-marcador.pdf")
        template = self._create_template(
            attachment, markers=[{"responsible_id": self.default_role}]
        )
        request = self._create_request(
            template, [(self.signers[0], self.default_role)]
        )

        self.assertTrue(request.quantidia_digital_round)
        self.assertEqual(request.quantidia_status, "in_progress")

    def test_marker_is_never_required(self):
        attachment = self._create_pdf_attachment("opcional.pdf")
        template = self._create_template(attachment)
        document = template.document_ids

        marker = self.env["sign.item"].create(
            {
                "document_id": document.id,
                "type_id": self.quantidia_type.id,
                "page": 1,
                "posX": 0.1,
                "posY": 0.1,
                "width": 0.2,
                "height": 0.08,
                "required": True,
            }
        )
        self.assertFalse(marker.required)

        marker.write({"required": True})
        self.assertFalse(marker.required)
