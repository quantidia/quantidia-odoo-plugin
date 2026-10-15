# -*- coding: utf-8 -*-
"""Tipo de campo "Firma digital Quantidia" para templates de Odoo Sign.

El campo es SOLO un marcador de posicion: nunca se completa en la fase
electronica (no bloquea la validacion nativa) y su posicion/tamano en la
pagina es lo que se envia a la API de Quantidia como `signatureRect`.
"""

from odoo import api, fields, models

# Valor del item_type propio del modulo (usado tambien desde sign.item y
# sign.request para filtrar/mapear los marcadores).
QUANTIDIA_ITEM_TYPE = "quantidia_signature"

# Dimension por defecto del recuadro (fracciones de pagina, como todo
# sign.item). Un poco mas alto que una firma nativa: el sello Quantidia
# incluye informacion del certificado.
# Defaults del marcador: la estampa del SDK mide ~150x100 pt y NO se
# ajusta al recuadro; el marcador por defecto debe contenerla (A4:
# 0.260*595 ~ 155 pt de ancho, 0.140*842 ~ 118 pt de alto).
QUANTIDIA_DEFAULT_WIDTH = 0.260
QUANTIDIA_DEFAULT_HEIGHT = 0.140


class SignItemType(models.Model):
    _inherit = "sign.item.type"

    item_type = fields.Selection(
        selection_add=[
            (QUANTIDIA_ITEM_TYPE, "Quantidia Digital Signature"),
        ],
        ondelete={QUANTIDIA_ITEM_TYPE: "cascade"},
    )

    @api.depends("field_size", "item_type")
    def _compute_dimensions(self):
        # El compute nativo indexa default_dimension_map[item_type] y
        # reventaria con KeyError para el tipo propio: lo resolvemos aca y
        # dejamos el resto al super().
        quantidia_types = self.filtered(
            lambda t: t.item_type == QUANTIDIA_ITEM_TYPE
        )
        super(SignItemType, self - quantidia_types)._compute_dimensions()
        for record in quantidia_types:
            record.default_width = QUANTIDIA_DEFAULT_WIDTH
            record.default_height = QUANTIDIA_DEFAULT_HEIGHT
