# -*- coding: utf-8 -*-
from odoo import models

from .sign_item_type import QUANTIDIA_ITEM_TYPE


class SignTemplate(models.Model):
    _inherit = "sign.template"

    def _quantidia_signature_item_ids(self):
        """Marcadores "Firma digital Quantidia" del template, en orden z.

        El orden de lectura (pagina, posY, posX — arriba/izquierda primero)
        es el orden en que los marcadores se asignan como slots de firma.
        """
        self.ensure_one()
        return self.sign_item_ids.filtered(
            lambda item: item.type_id.item_type == QUANTIDIA_ITEM_TYPE
        ).sorted(lambda item: (item.page, item.posY, item.posX))

    def _quantidia_electronic_item_ids(self):
        """Campos del template que NO son marcadores Quantidia.

        Definen si hay fase electronica de carga de datos (Flujo A) o si la
        ronda digital arranca inmediata (Flujo B): un template cuyos unicos
        campos son marcadores Quantidia se comporta como "sin campos".
        """
        self.ensure_one()
        return self.sign_item_ids.filtered(
            lambda item: item.type_id.item_type != QUANTIDIA_ITEM_TYPE
        )

    def open_sign_send_dialog(self):
        """Mantiene el flujo estándar salvo para la actividad Quantidia ad hoc."""
        activity_id = self.env.context.get("default_activity_id")
        activity = self.env["mail.activity"].browse(activity_id).exists()
        if activity and activity.activity_type_id.quantidia_adhoc:
            return self.env["quantidia.adhoc.sign.wizard"].action_open_from_activity(activity)
        return super().open_sign_send_dialog()
