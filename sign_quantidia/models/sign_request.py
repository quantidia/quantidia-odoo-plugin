# -*- coding: utf-8 -*-
import base64
import io
import logging
import math
import zipfile

from markupsafe import Markup

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Posicionamiento de firmas Quantidia (signatureRect del SDK).
#
# El SDK firma dentro de un recuadro {page, x, y, w, h} en puntos PDF
# (pagina 1-based; x desde la izquierda, y desde ABAJO — coordenadas PDF
# estandar, verificado empiricamente en demo). Sin posicion, todos los sellos
# de la ronda serial caen en el default del SDK (x:200, y:200, pag. 1) y se
# superponen (deuda tecnica 2026-08-25).
#
# Grilla automatica (sin marcadores en el template): 3 firmas por linea en
# orden z (izq->der, luego la linea siguiente), anclada al pie de la ultima
# pagina del documento principal.
# ---------------------------------------------------------------------------
QUANTIDIA_GRID_COLUMNS = 3
QUANTIDIA_GRID_MARGIN_X = 36.0
QUANTIDIA_GRID_MARGIN_BOTTOM = 36.0
# La estampa del SDK NO se ajusta al recuadro (no hay parametro de fit):
# se dibuja a su tamano propio (~150x100 pt, medido 141x109 en demo). El
# recuadro debe CONTENER la estampa con holgura: alto 120 y ancho ~158.
QUANTIDIA_GRID_BOX_HEIGHT = 120.0
QUANTIDIA_GRID_GAP_Y = 16.0
QUANTIDIA_GRID_PAD_X = 8.0


class SignRequest(models.Model):
    """Extiende el circuito de firma nativo de Odoo Sign con el estado y la
    trazabilidad de la integracion con Quantidia.

    Heredamos `sign.request` (en lugar de crear tablas propias) para conservar
    el circuito, el chatter y la trazabilidad nativa, y solo anexar lo propio
    de Quantidia.
    """

    _inherit = "sign.request"

    @api.model
    def _quantidia_default_mode(self):
        """Modo de firma default para requests nuevos, configurable en
        Settings (`sign_quantidia.default_mode`). Default seguro
        "electronic": instalar el modulo no debe cambiar el flujo de una
        instalacion que ya usa Odoo Sign nativo."""
        return (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("sign_quantidia.default_mode")
            or "electronic"
        )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        # Flujo B: template sin campos electronicos -> la fase electronica no
        # aporta nada (no hay datos que completar), la ronda digital arranca
        # ya al crear el request. En Flujo A (con campos) la ronda espera al
        # _sign(). Los marcadores "Firma Quantidia" no cuentan como campos:
        # son posiciones para la ronda digital.
        flujo_b = records.filtered(
            lambda r: r.quantidia_mode == "digital"
            and not r.sudo().template_id._quantidia_electronic_item_ids()
        )
        if flujo_b and not self.env.context.get("quantidia_skip_immediate_round"):
            flujo_b._start_digital_round_immediate()
        return records

    def _check_signers_roles_validity(self):
        # Modo digital (Flujo B en particular): la matriz item-por-rol de la
        # fase electronica no aplica — la firma es la ronda Quantidia y el
        # orden lo da el tracking. Se permiten N firmantes con rol default
        # aunque el template no tenga campos colocados.
        digital = self.filtered(
            lambda r: r.quantidia_mode == "digital"
        )
        super(SignRequest, self - digital)._check_signers_roles_validity()

    @api.depends_context("uid")
    @api.depends(
        "quantidia_digital_round",
        "quantidia_digital_signature_ids.state",
        "request_item_ids.state",
    )
    def _compute_need_my_signature(self):
        super()._compute_need_my_signature()
        for sign_request in self:
            if not sign_request.quantidia_digital_round:
                continue
            # Ronda digital activa: "Firmar" nativo solo procede si es mi
            # turno digital (el click abre el SDK via el interceptor). Si ya
            # firme o aun no me toca, no hay nada que firmar por aca.
            tracking = sign_request.quantidia_digital_signature_ids.filtered(
                lambda t: t.partner_id.id == self.env.user.partner_id.id
            )
            sign_request.need_my_signature = bool(
                tracking and tracking.state == "pending"
            )

    @api.model
    def _search_need_my_signature(self, operator, value):
        """Alinea la busqueda con el compute (D0, deuda tecnica).

        El search nativo devuelve los requests con item nativo 'sent' del
        usuario. Con ronda digital activa el compute dice otra cosa:
        "me toca firmar" SOLO si mi tracking esta pending (aunque el item
        nativo ya haya completado), y false si estoy waiting o ya firme
        (aunque el item siga 'sent'). Sin este override los filtros de
        busqueda sobre/super-incluyen respecto del boton visible.
        """
        domain = super()._search_need_my_signature(operator, value)
        if domain is NotImplemented or len(domain) != 1 or domain[0][:2] != ("id", "in"):
            return domain
        request_ids = set(domain[0][2])
        my_partner_id = self.env.user.partner_id.id
        trackings = self.env["quantidia.digital.signature"].sudo().search(
            [("partner_id", "=", my_partner_id)]
        )
        for tracking in trackings:
            sign_request = tracking.sign_request_id
            if not sign_request.quantidia_digital_round:
                continue  # sin ronda activa manda la semantica nativa
            if tracking.state == "pending":
                request_ids.add(sign_request.id)
            else:
                request_ids.discard(sign_request.id)
        return [("id", "in", list(request_ids))]

    quantidia_session_token = fields.Char(
        string="Quantidia Session Token",
        copy=False,
        readonly=True,
        help="access_token Odoo emitido para esta sesion de firma. Lo consume "
        "el SDK y Quantidia lo valida via el endpoint de introspeccion.",
    )
    quantidia_mode = fields.Selection(
        [("digital", "Quantidia"), ("electronic", "Odoo")],
        string="Modo de firma",
        # lambda (no nombre de metodo): Odoo 19 no resuelve defaults por
        # nombre para Selection y lo tomaba como valor literal.
        default=lambda self: self._quantidia_default_mode(),
        tracking=True,
        help="Quantidia = firma digital con SDK Quantidia (PAdES). "
        "Odoo = firma nativa electrónica de Odoo.",
    )
    quantidia_status = fields.Selection(
        [
            ("pending", "Por firmar"),
            ("in_progress", "Firma iniciada"),
            ("signed", "Firmado"),
            ("cancelled", "Cancelado"),
            ("error", "Error"),
        ],
        string="Estado Quantidia",
        default="pending",
        copy=False,
        readonly=True,
        tracking=100,
    )
    quantidia_combined_status = fields.Selection(
        [
            ("pending", "Por firmar"),
            ("in_progress", "Firma iniciada"),
            ("signed_digital", "Firma Quantidia"),
            ("signed_electronic", "Firma Odoo"),
            ("cancelled", "Cancelado"),
            ("expired", "Expirado"),
        ],
        string="Estado",
        compute="_compute_quantidia_combined_status",
        store=False,
    )

    quantidia_is_multi_pdf = fields.Boolean(
        string="Multi-PDF",
        compute="_compute_quantidia_is_multi_pdf",
    )

    def _compute_quantidia_is_multi_pdf(self):
        for r in self:
            # Solo tiene sentido en modo digital: en electronic no debe
            # afectar botones nativos como "Vista previa".
            if r.quantidia_mode != "digital":
                r.quantidia_is_multi_pdf = False
                continue
            try:
                r.quantidia_is_multi_pdf = len(r._quantidia_get_source_documents()) > 1
            except Exception:
                r.quantidia_is_multi_pdf = False

    @api.depends("state", "quantidia_status")
    def _compute_quantidia_combined_status(self):
        for r in self:
            qs = r.quantidia_status
            if qs == "signed":
                r.quantidia_combined_status = "signed_digital"
            elif qs == "in_progress":
                r.quantidia_combined_status = "in_progress"
            elif qs == "cancelled":
                r.quantidia_combined_status = "cancelled"
            elif r.state == "signed":
                r.quantidia_combined_status = "signed_electronic"
            elif r.state == "sent":
                r.quantidia_combined_status = "pending"
            elif r.state == "canceled":
                r.quantidia_combined_status = "cancelled"
            elif r.state == "expired":
                r.quantidia_combined_status = "expired"
            else:
                r.quantidia_combined_status = "pending"

    quantidia_signed_attachment_id = fields.Many2one(
        comodel_name="ir.attachment",
        string="PDF firmado (Quantidia)",
        copy=False,
        readonly=True,
    )
    quantidia_reject_reason = fields.Text(
        string="Motivo de rechazo",
        copy=False,
        readonly=True,
    )
    quantidia_digital_round = fields.Boolean(
        string="Ronda digital activa",
        copy=False,
        readonly=True,
        help="Indica que la ronda digital de Quantidia esta en curso (Fase 2 Augment).",
    )
    quantidia_adhoc = fields.Boolean(
        string="Solicitud ad hoc Quantidia",
        copy=False,
        readonly=True,
        help="La solicitud se originó desde PDFs existentes, no desde un template de negocio.",
    )
    quantidia_digital_signature_ids = fields.One2many(
        comodel_name="quantidia.digital.signature",
        inverse_name="sign_request_id",
        string="Tracking de firmas digitales",
    )
    quantidia_document_ids = fields.Many2many(
        "ir.attachment",
        string="Documentos del pedido",
        compute="_compute_quantidia_document_ids",
        help="PDFs incluidos en la solicitud: principal + anexos. Con la "
        "ronda en marcha o completa son los firmados; antes, los origenes.",
    )

    quantidia_my_turn = fields.Boolean(
        string="Es mi turno",
        compute="_compute_quantidia_my_turn",
    )

    def _compute_quantidia_my_turn(self):
        for r in self:
            partner_id = r.env.user.partner_id.id
            tracking = r.quantidia_digital_signature_ids.filtered(
                lambda t: t.partner_id.id == partner_id
            )
            r.quantidia_my_turn = bool(
                r.quantidia_digital_round
                and any(t.state == "pending" for t in tracking)
            )

    def _compute_quantidia_document_ids(self):
        for r in self:
            r.quantidia_document_ids = r._quantidia_display_documents()

    def _quantidia_display_documents(self):
        """Lista de PDFs que la solicitud comprende (misma fuente que el
        portal: firmados durante/despues de la ronda, origenes antes).
        Si la ronda empezo pero todavia no llego ningun firmado (primer
        firmante pendiente), se muestran los origenes."""
        if self.quantidia_status in ("in_progress", "signed"):
            principal = self.quantidia_signed_attachment_id
            if not principal and self.completed_document_attachment_ids:
                principal = self.completed_document_attachment_ids.sorted(
                    "create_date"
                )[-1:]
            annexes = self.env["ir.attachment"].search([
                ("res_model", "=", self._name),
                ("res_id", "=", self.id),
                ("res_field", "=", "quantidia_annex_signed"),
            ], order="id")
            signed = (principal + annexes).filtered(lambda a: a.datas)
            if signed:
                return signed
        docs = self.template_document_ids.mapped("attachment_id").filtered(
            lambda a: a.mimetype == "application/pdf" and a.datas
        )
        seen = set(docs.ids)
        chatter = self.message_ids.mapped("attachment_ids")
        request_atts = self.env["ir.attachment"].search([
            ("res_model", "=", self._name),
            ("res_id", "=", self.id),
        ])
        extras = (chatter + request_atts).filtered(
            lambda a: a.mimetype == "application/pdf"
            and a.datas
            and not a.res_field
            and a.id not in seen
            # Copias firmadas posteadas al chatter del request (sin
            # res_field): son duplicados del principal ya listado arriba.
            and not (a.name or "").endswith("_signed.pdf")
        )
        return docs + extras

    # ------------------------------------------------------------------
    # Hook post-_sign() para ronda digital (Fase 2 - Augment)
    # ------------------------------------------------------------------
    def _sign(self):
        """Override para iniciar ronda digital post-firma electronica.

        En modo digital (Augment), despues de que Odoo completa la firma
        electronica (_sign nativo), iniciamos la ronda digital paralela.

        Con el contexto `quantidia_digital_completion` (cierre de la ronda
        digital via _quantidia_finalize_native) el request pasa a signed
        SIN las publicaciones nativas: ni el mensaje "se firmo un documento
        y se adjunto una copia" al registro origen ni los mails de cierre
        nativos. La publicacion la hace _quantidia_publish_signed_to_source
        (un solo mensaje con TODOS los PDFs firmados) y el mail de cierre
        lo manda el template Quantidia.
        """
        if self.env.context.get("quantidia_digital_completion"):
            for request in self:
                if request.state == "sent" and all(
                    it.state == "completed" for it in request.request_item_ids
                ):
                    request.sudo().write({"state": "signed"})
            return True
        res = super()._sign()
        for request in self:
            # Solo si nunca hubo ronda: al finalizar la ronda digital,
            # _quantidia_finalize_native completa los items y Odoo vuelve a
            # llamar _sign(); sin este guard la ronda se reiniciaria tras
            # cada cierre.
            if (
                request.quantidia_mode == "digital"
                and not request.quantidia_digital_signature_ids
            ):
                request._start_digital_round()
        return res

    def _start_digital_round(self):
        """Inicia la ronda digital de Quantidia (Flujo A - post-_sign).

        Se llama despues de _sign() cuando el ultimo firmante completa
        electronicamente. Crea tracking por firmante y notifica.

        Incluye a TODOS los firmantes del request (tengan o no campos),
        porque en modo Augment TODOS deben firmar digitalmente con Quantidia.

        La ronda es SERIAL: solo el primero con sequence=1 tiene turno activo.
        """
        self.ensure_one()
        # Solo arrancar si no esta ya activa (idempotente)
        if self.quantidia_digital_round:
            return

        _logger.info("quantidia: iniciando ronda digital SERIAL para request %s", self.id)
        self.write({
            "quantidia_digital_round": True,
            "quantidia_status": "in_progress",
        })

        # Crear tracking para TODOS los firmantes con secuencia
        tracking_vals = []
        for seq, item in enumerate(self.request_item_ids, start=1):
            tracking_vals.append({
                "sign_request_id": self.id,
                "request_item_id": item.id,
                "state": "pending" if seq == 1 else "waiting",  # Solo el primero tiene turno
                "sequence": seq,
            })
        if tracking_vals:
            self.env["quantidia.digital.signature"].create(tracking_vals)
            # Fase 2b: notificar al primer firmante (turno 1)
            self.quantidia_digital_signature_ids.filtered(
                lambda t: t.state == "pending"
            )._notify_turn()
        _logger.info("quantidia: ronda digital iniciada con %s firmantes (serial)", len(tracking_vals))

    def _activate_next_signer(self, current_sequence):
        """Activa al siguiente firmante en la secuencia serial.

        Cuando un firmante firma digitalmente, activa al siguiente en orden
        de sequence para que pueda firmar.

        Args:
            current_sequence: Secuencia del firmante que acaba de firmar
        """
        self.ensure_one()
        # Buscar al siguiente firmante en estado "waiting"
        next_tracking = self.quantidia_digital_signature_ids.filtered(
            lambda t: t.state == "waiting" and t.sequence > current_sequence
        ).sorted("sequence")[:1]

        if next_tracking:
            _logger.info("quantidia: activando firmante %s (sequence %s)", next_tracking.id, next_tracking.sequence)
            next_tracking.write({"state": "pending"})
            # Fase 2b: notificar que llegó su turno
            next_tracking._notify_turn()
        else:
            _logger.info("quantidia: no hay siguiente firmante (sequence %s fue el último)", current_sequence)

    # ------------------------------------------------------------------
    # Acciones del flujo
    # ------------------------------------------------------------------

    def _start_digital_round_immediate(self):
        """Inicia la ronda digital inmediata (Flujo B - sin campos).

        Se llama al crear el request cuando el template no tiene campos
        para cargar. No espera la firma electronica.
        """
        self.ensure_one()
        if self.quantidia_digital_round:
            return

        _logger.info("quantidia: iniciando ronda digital inmediata para request %s", self.id)
        self.write({
            "quantidia_digital_round": True,
            "quantidia_status": "in_progress",
        })

        # Crear tracking por firmante: serial (turno 1 pending, resto waiting)
        tracking_vals = []
        for seq, item in enumerate(self.request_item_ids, start=1):
            tracking_vals.append({
                "sign_request_id": self.id,
                "request_item_id": item.id,
                "state": "pending" if seq == 1 else "waiting",
                "sequence": seq,
            })
        if tracking_vals:
            self.env["quantidia.digital.signature"].create(tracking_vals)
            # Fase 2b: notificar al primer firmante (turno 1)
            self.quantidia_digital_signature_ids.filtered(
                lambda t: t.state == "pending"
            )._notify_turn()

        _logger.info("quantidia: ronda digital inmediata iniciada con %s firmantes", len(tracking_vals))

    def _quantidia_register_upload(self, request_item_id):
        """Registra un upload del firmante de turno y cierra su firma solo
        con el ULTIMO documento del grupo.

        El SDK sube los documentos de a uno (principal primero, anexos
        despues). El tracking pasa a signed — y la ronda avanza/cierra —
        recien cuando subio tantos documentos como tenia su bundle, no con
        el primero.
        """
        tracking = self.quantidia_digital_signature_ids.filtered(
            lambda t: t.request_item_id.id == request_item_id
        )
        if not tracking:
            _logger.warning(
                "quantidia: no se encontro tracking para item %s", request_item_id
            )
            return
        tracking = tracking[:1]
        if not tracking.expected_documents:
            tracking.expected_documents = len(self._quantidia_get_source_documents())
        tracking.upload_count += 1
        _logger.info(
            "quantidia: upload %s/%s del firmante del item %s (request %s)",
            tracking.upload_count, tracking.expected_documents,
            request_item_id, self.id,
        )
        if (
            tracking.state == "pending"
            and tracking.upload_count >= tracking.expected_documents
        ):
            tracking.action_mark_signed()
        else:
            # Sin este flush, lecturas en la misma transaccion no ven el
            # avance (cache del recordset).
            tracking.flush_recordset()

    def _check_digital_round_complete(self):
        """Verifica si todos los firmantes completaron la ronda digital.

        Si todos los tracking estan 'signed', cierra la ronda.
        """
        self.ensure_one()
        if not self.quantidia_digital_round:
            return

        # Verificar si todos los firmantes firmaron (signed) o si la ronda fue cancelada/rechazada
        all_signed = all(t.state in ("signed", "cancelled", "rejected") for t in self.quantidia_digital_signature_ids)
        if all_signed:
            _logger.info("quantidia: ronda digital completa para request %s", self.id)
            self.write({
                "quantidia_digital_round": False,
                "quantidia_status": "signed",
            })
            # Fase 2b: mail de cierre a los firmantes con el PDF final
            self._notify_round_completed()
            # El registro origen (actividad/firma desde su chatter) recibe los
            # PDFs firmados en SU chatter: el formulario de Sign no muestra
            # chatter y el usuario trabaja sobre su registro, no sobre el
            # request.
            self._quantidia_publish_signed_to_source()
            # En Flujo B (sin electronica previa), tambien marcar state=signed
            # si el template no tenia campos
            if self.state != "signed":
                self._check_flow_b_complete()

    def _quantidia_publish_signed_to_source(self):
        """Deja el PDF principal firmado en el chatter del registro origen.

        El flujo ad hoc nace de una actividad sobre un registro (factura,
        contrato, ...). El formulario de Sign no muestra chatter, asi que
        publicar solo en el request deja el documento inaccesible.

        Con el conteo de uploads por firmante, la ronda cierra recien con
        el ULTIMO documento del grupo: al publicar, principal y anexos
        estan todos en su version final acumulada. El camino de anexo
        tardio queda como red de seguridad para uploads fuera de orden.
        """
        self.ensure_one()
        record = self.reference_doc
        if not record:
            return
        signed = self.quantidia_signed_attachment_id
        annexes = self.env["ir.attachment"].search([
            ("res_model", "=", self._name),
            ("res_id", "=", self.id),
            ("res_field", "=", "quantidia_annex_signed"),
        ])
        docs = (signed + annexes).filtered(lambda a: a.datas)
        if not docs:
            return
        copy_vals = [{
            "name": att.name,
            "datas": att.datas,
            "res_model": record._name,
            "res_id": record.id,
            "mimetype": "application/pdf",
        } for att in docs]
        copies = self.env["ir.attachment"].create(copy_vals)
        record.message_post(
            body=Markup(
                "Firma Quantidia completada (%s). Documentos firmados: %s"
                % (self.reference, ", ".join(copies.mapped("name")))
            ),
            attachment_ids=copies.ids,
        )

    def _notify_round_completed(self):
        """Fase 2b: mail de cierre a cada firmante con el PDF final adjunto."""
        template = self.env.ref(
            "sign_quantidia.mail_template_quantidia_completed", raise_if_not_found=False
        )
        if not template:
            return
        # Adjuntos del cierre: principal firmado + TODOS los anexos firmados
        # (el mensaje al registro origen publica el mismo conjunto).
        final_atts = self.quantidia_signed_attachment_id
        final_atts += self.env["ir.attachment"].search([
            ("res_model", "=", self._name),
            ("res_id", "=", self.id),
            ("res_field", "=", "quantidia_annex_signed"),
        ])
        final_atts = final_atts.filtered(lambda a: a.datas)
        for tracking in self.quantidia_digital_signature_ids.filtered(
            lambda t: t.state == "signed" and t.partner_id.email
        ):
            mail_id = template.sudo().send_mail(tracking.id, force_send=False)
            if final_atts and mail_id:
                self.env["mail.mail"].sudo().browse(mail_id).write(
                    {"attachment_ids": [(4, att.id) for att in final_atts]}
                )

    def _check_flow_b_complete(self):
        """Marca el request como signed si corresponde (Flujo B)."""
        # En Flujo B, no hubo firma electronica previa, asi que cuando
        # la ronda digital completa, cerramos tambien el circuito nativo:
        # completar los items pendientes y pasar el request a signed.
        if all(t.state == "signed" for t in self.quantidia_digital_signature_ids):
            self._quantidia_finalize_native()

    def _cancel_digital_round(self, reason=None):
        """Cancela la ronda digital por rechazo de un firmante.

        Cuando un firmante rechaza firmar digitalmente, se cancela toda la
        ronda digital. Los demas firmantes no pueden seguir firmando.

        Args:
            reason: Motivo del rechazo (opcional, para el chatter).
        """
        self.ensure_one()
        if not self.quantidia_digital_round:
            return

        _logger.info("quantidia: cancelando ronda digital para request %s (rechazo)", self.id)
        self.write({
            "quantidia_digital_round": False,
            "quantidia_status": "cancelled",
            "quantidia_reject_reason": reason or "Firma digital rechazada por un firmante",
        })

        # Marcar todos los tracking pendientes como cancelados
        pending = self.quantidia_digital_signature_ids.filtered(lambda t: t.state == "pending")
        if pending:
            pending.write({"state": "cancelled"})

        # Notificar en el chatter
        self.sudo().message_post(
            body="Ronda digital cancelada: un firmante rechazó la firma. "
                 "Motivo: %s" % (reason or "No especificado")
        )

    def action_restart_digital_round(self):
        """Reinicia la ronda digital solo para los firmantes que faltaron.

        Despues de un rechazo, permite volver a enviar el request a los
        firmantes que siguen en estado pending/rejected (no los que ya firmaron).

        Permisos:
        - Managers: pueden reiniciar cualquier request
        - Usuarios avanzados: solo sus propios requests (creados por ellos)
        """
        self.ensure_one()

        # Verificar permisos: manager o usuario avanzado propietario
        is_manager = self.env.user.has_group("sign.group_sign_manager")
        is_advanced = self.env.user.has_group("sign_quantidia.group_sign_user_advanced")
        if not is_manager and (not is_advanced or self.create_uid.id != self.env.user.id):
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "message": "Solo managers o el creador del request pueden reiniciar la ronda.",
                    "type": "danger",
                }
            }

        # Verificar que la ronda este cancelada
        if self.quantidia_status != "cancelled":
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "message": "Solo se puede reiniciar una ronda cancelada.",
                    "type": "warning",
                }
            }

        # Buscar firmantes que faltaron (pending + rejected)
        missing = self.quantidia_digital_signature_ids.filtered(
            lambda t: t.state in ["pending", "rejected"]
        )
        if not missing:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "message": "No hay firmantes faltantes para reiniciar.",
                    "type": "info",
                }
            }

        _logger.info("quantidia: reiniciando ronda digital para %s firmantes faltantes", len(missing))
        # Reactivar ronda digital
        self.write({
            "quantidia_digital_round": True,
            "quantidia_status": "in_progress",
            "quantidia_reject_reason": False,  # limpiar motivo anterior
        })
        # Resetear tracking de los faltantes a pending
        missing.write({"state": "pending"})

        # Notificar en el chatter
        self.sudo().message_post(
            body="Ronda digital reiniciada para %s firmante(s) faltante(s)." % len(missing)
        )

        # Fase 2b: reenviar notificacion de turno a los faltantes
        missing._notify_turn()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "message": "Ronda digital reiniciada. Notificaciones enviadas a los firmantes faltantes.",
                "type": "success",
            }
        }

    def cancel(self):
        """Cancela el pedido completo, ronda digital Quantidia incluida.

        Un solo boton "Cancelar": el nativo cierra tambien la ronda digital
        si estaba activa, marcando el tracking pendiente como cancelado y
        dejando registro en el chatter.
        """
        active_round = self.filtered(lambda r: r.quantidia_digital_round)
        super().cancel()
        for request in active_round:
            pending = request.quantidia_digital_signature_ids.filtered(
                lambda t: t.state in ("pending", "waiting")
            )
            if pending:
                pending.write({"state": "cancelled"})
            request.write({
                "quantidia_digital_round": False,
                "quantidia_status": "cancelled",
                "quantidia_reject_reason": "Pedido cancelado por el usuario",
            })
            request.sudo().message_post(
                body="Ronda de firma digital Quantidia cancelada junto con el pedido."
            )

    # ------------------------------------------------------------------
    # Acciones del flujo
    # ------------------------------------------------------------------
    def action_start_quantidia_sign(self):
        """Inicia el flujo de firma Quantidia desde el chatter.

        Emite un access_token de corta duracion ligado al usuario/empresa y lo
        deja disponible para que el frontend lo pase al SDK.
        """
        self.ensure_one()
        token_service = self.env["sign.quantidia.token"]
        self.quantidia_session_token = token_service.issue_token(sign_request=self)
        self.quantidia_status = "in_progress"
        # La apertura real del modal la dispara el JS del chatter leyendo
        # el token desde el data-attribute del boton de accion.
        return True

    def action_open_quantidia_sign_header(self):
        """Boton "Firmar anexos" del header del formulario.

        Devuelve una client action (`sign_quantidia.sign_open`) que abre el
        SDK con la misma configuracion que usaba el boton del chatter.
        """
        self.ensure_one()
        config = self.action_prepare_quantidia_sign()
        return {
            "type": "ir.actions.client",
            "tag": "sign_quantidia.sign_open",
            "target": "new",
            "params": {"signRequestId": self.id, "config": config},
        }

    def action_prepare_quantidia_sign(self):
        """Prepara la configuracion frontend para abrir Quantidia desde UI.

        Este metodo es el punto de entrada del boton "Firmar anexos" del
        chatter. Emite/refresca el token de sesion y devuelve solo la
        configuracion necesaria para inicializar el SDK en el browser.
        """
        self.ensure_one()
        self.action_start_quantidia_sign()
        get_param = self.env["ir.config_parameter"].sudo().get_param
        return {
            "baseUrl": get_param("sign_quantidia.sdk_base_url") or "",
            "apiBase": get_param("sign_quantidia.sdk_api_base") or "",
            "environmentId": get_param("sign_quantidia.environment_id") or "",
            "companyId": get_param("sign_quantidia.company_id") or "",
            "agencyId": get_param("sign_quantidia.agency_id") or "",
            "apiKey": get_param("sign_quantidia.api_key") or "",
            # Evita una segunda llamada ORM desde el cliente para releer el
            # token que acabamos de emitir.
            "accessToken": self.quantidia_session_token,
            # Recuadro de firma (signatureRect) del firmante actual: cada
            # firmante de la ronda recibe su posicion para no superponerse.
            "signatureRect": self._quantidia_get_signature_rect(
                self._quantidia_signer_item_for_current_user()
            ),
            # Modo de auth del SDK:
            #   "sso"    -> authLogin.access_token (Odoo = IdP, recomendado prod)
            #   "simple" -> authLogin.username/password (solo test, creds sample)
            "authMode": get_param("sign_quantidia.auth_mode") or "sso",
            "testUsername": get_param("sign_quantidia.test_username") or "",
            "testPassword": get_param("sign_quantidia.test_password") or "",
        }

    def _quantidia_signer_item_for_current_user(self):
        """Item del firmante actual (o del turno pendiente como fallback).

        El recuadro de firma depende del firmante que abre el SDK: primero el
        item del usuario actual; si no es firmante (p.ej. manager que abre el
        flujo), el del turno pendiente de la ronda.
        """
        self.ensure_one()
        item = self.request_item_ids.filtered(
            lambda it: it.partner_id.id == self.env.user.partner_id.id
        )[:1]
        if not item:
            pending = self.quantidia_digital_signature_ids.filtered(
                lambda t: t.state == "pending"
            ).sorted("sequence")[:1]
            item = pending.request_item_id or self.request_item_ids[:1]
        return item

    def action_download_quantidia_signed(self):
        """Descarga el PDF firmado por Quantidia (quantidia_signed_attachment_id).

        Boton claro en el header del request: evita que el usuario descargue por
        error el PDF nativo de Odoo (completed document) en vez del firmado
        criptograficamente por Quantidia.
        """
        self.ensure_one()
        att = self.quantidia_signed_attachment_id
        if not att:
            return False
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=true" % att.id,
            "target": "self",
        }

    def send_signature_accesses(self):
        """'Reenviar' en un pedido con ronda digital: re-notifica el turno.

        El flujo nativo reenvia la invitacion electronica; con la ronda
        activa ese mail esta suprimido (el override del item no manda nada).
        Lo util aca es re-mandar el mail de turno Quantidia al firmante
        pendiente actual.
        """
        remaining = self.filtered(lambda r: not r.quantidia_digital_round)
        if remaining:
            return super(SignRequest, remaining).send_signature_accesses()
        for request in self - remaining:
            pending = request.quantidia_digital_signature_ids.filtered(
                lambda t: t.state == "pending"
            )
            if pending:
                pending._notify_turn()
        return True

    def get_sign_request_documents(self):
        """En un pedido multi-PDF, "Descargar" entrega un ZIP.

        El download nativo solo baja el PDF principal (completado/firmado);
        con anexos el usuario esperaria poder llevarse todo lo firmado de
        una. En pedidos de un solo documento se conserva el comportamiento
        nativo.
        """
        res = super().get_sign_request_documents()
        if len(self) != 1:
            return res
        annexes = self.env["ir.attachment"].search([
            ("res_model", "=", self._name),
            ("res_id", "=", self.id),
            ("res_field", "=", "quantidia_annex_signed"),
        ], order="id")
        if not annexes:
            return res
        principal = self.quantidia_signed_attachment_id
        if not principal and self.completed_document_attachment_ids:
            principal = self.completed_document_attachment_ids.sorted("create_date")[-1:]
        files = [
            (att.name, base64.b64decode(att.datas))
            for att in (principal + annexes)
            if att.datas
        ]
        if not files:
            return res
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in files:
                zf.writestr(name, data)
        zip_attachment = self.env["ir.attachment"].create({
            "name": "sign_request_%s_signed.zip" % self.id,
            "datas": base64.b64encode(buffer.getvalue()),
            "res_model": self._name,
            "res_id": self.id,
            "res_field": "quantidia_signed_zip",
            "mimetype": "application/zip",
        })
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=true" % zip_attachment.id,
            "target": "self",
        }

    def _quantidia_is_last_signer(self, item=None):
        """True si queda exactamente un item pendiente Y es del firmante actual.

        D2: antes devolvia ``len(pending) <= 1`` sin validar quien consulta:
        daba True con 0 pendientes (request ya firmado) y para firmantes que
        no eran el pendiente. Ahora exige exactamente 1 pendiente y, si se
        pasa el ``item`` del firmante actual, que sea ese.

        Args:
            item: sign.request.item del firmante que consulta (opcional; los
                controllers lo resuelven por token o por partner del usuario).
        """
        self.ensure_one()
        pending = self.request_item_ids.filtered(lambda it: it.state == "sent")
        if len(pending) != 1:
            return False
        if item is not None:
            return bool(item) and item.id == pending.id
        return True

    def _quantidia_sign_phase(self):
        """Fase semantica del proceso de firma, para mensajes de UI.

        Unica fuente de verdad: los frontends mapean fase -> mensaje sin
        recombinar flags. Orden de evaluacion:
        complete/cancelled -> ronda activa -> electronica en curso
        (con campos = carga de datos, sin campos = firma pura).
        """
        self.ensure_one()
        if self.quantidia_mode != "digital":
            return "electronic"
        if self.quantidia_status == "signed":
            return "complete"
        if self.quantidia_status == "cancelled":
            return "cancelled"
        if self.quantidia_digital_round:
            return "digital_round"
        has_fields = bool(self.sudo().template_id._quantidia_electronic_item_ids())
        return "data_collection" if has_fields else "electronic_signing"

    # ------------------------------------------------------------------
    # Posicionamiento de la firma digital (signatureRect del SDK)
    # ------------------------------------------------------------------
    def _quantidia_get_signature_rect(self, request_item):
        """Recuadro de firma del SDK para el firmante de ``request_item``.

        Prioridad:

        1. Marcadores "Firma digital Quantidia" del template: match por rol
           y, para los firmantes que queden sin marcador, orden z entre los
           marcadores libres.
        2. Grilla automatica de 3 firmas por linea (orden z), anclada debajo
           del ultimo marcador usado o, si no hay marcadores, al pie de la
           ultima pagina del documento principal.

        Returns:
            dict ``{page, x, y, w, h}`` en puntos PDF (pagina 1-based, ``y``
            con origen ABAJO-izquierda como lo interpreta el SDK de
            Quantidia — verificado en demo 2026-09-29: su README dice origen
            arriba, pero manda espejado), o ``None`` para dejar el default
            del SDK (PDF ilegible, request sin items, etc.).
        """
        self.ensure_one()
        items = self.request_item_ids
        if not items or request_item.id not in items._ids:
            return None

        documents = self.sudo()._quantidia_get_source_documents()
        if not documents:
            return None
        try:
            # odoo.tools.pdf vendoriza pypdf (no siempre hay modulo `pypdf`
            # instalable en el entorno: la imagen oficial no lo trae).
            from odoo.tools.pdf import PdfFileReader

            reader = PdfFileReader(
                io.BytesIO(base64.b64decode(documents[0]["datas"])), False
            )
            page_sizes = []
            for page in reader.pages:
                box = page.mediabox
                width, height = float(box.width), float(box.height)
                if int(page.rotation or 0) % 180:
                    width, height = height, width
                page_sizes.append((width, height))
        except Exception as exc:
            _logger.warning(
                "quantidia: no se pudo medir el PDF del request %s (%s); "
                "la firma usara la posicion default del SDK", self.id, exc,
            )
            return None
        if not page_sizes:
            return None

        slots = self._quantidia_match_signature_slots()
        slot = slots.get(request_item.id)
        if slot:
            rect = self._quantidia_rect_from_item(slot, page_sizes)
        else:
            grid_items = items.filtered(lambda it: it.id not in slots)
            grid_index = list(grid_items._ids).index(request_item.id)
            page, anchor_y = self._quantidia_grid_anchor(slots, page_sizes, len(grid_items))
            page_w = page_sizes[page - 1][0]
            rect = self._quantidia_grid_rect(page, anchor_y, page_w, grid_index)
        # Internamente calculamos con el origen arriba-izquierda de
        # sign.item (posY desde arriba); el SDK de Quantidia interpreta `y`
        # con origen abajo-izquierda: se entrega espejado.
        rect["y"] = round(page_sizes[rect["page"] - 1][1] - rect["y"], 2)
        return rect

    def _quantidia_match_signature_slots(self):
        """Asigna marcadores Quantidia a firmantes (por rol, luego orden z).

        Returns:
            dict ``{request_item.id: sign.item}`` con los marcadores tomados.
        """
        self.ensure_one()
        q_items = self.sudo().template_id._quantidia_signature_item_ids()
        items = self.request_item_ids
        slots = {}
        taken_ids = set()
        # 1) match por rol: el marcador del rol del firmante es SU lugar.
        for item in items:
            for q_item in q_items:
                if q_item.id in taken_ids:
                    continue
                if q_item.responsible_id and q_item.responsible_id.id == item.role_id.id:
                    slots[item.id] = q_item
                    taken_ids.add(q_item.id)
                    break
        # 2) los firmantes sin marcador toman los marcadores libres en orden z.
        free_signers = items.filtered(lambda it: it.id not in slots)
        free_items = q_items.filtered(lambda q: q.id not in taken_ids)
        for item, q_item in zip(free_signers, free_items):
            slots[item.id] = q_item
            taken_ids.add(q_item.id)
        return slots

    @staticmethod
    def _quantidia_rect_from_item(item, page_sizes):
        """Convierte el marcador (fracciones de pagina) a puntos PDF."""
        page = min(max(item.page or 1, 1), len(page_sizes))
        page_w, page_h = page_sizes[page - 1]
        return {
            "page": page,
            "x": round(item.posX * page_w, 2),
            "y": round(item.posY * page_h, 2),
            "w": round(item.width * page_w, 2),
            "h": round(item.height * page_h, 2),
        }

    @staticmethod
    def _quantidia_grid_anchor(slots, page_sizes, total_grid):
        """Pagina y borde superior de la primera celda de la grilla.

        Con marcadores: debajo del ultimo marcador usado (orden z), con tope
        para que la grilla entre en la pagina. Sin marcadores: bloque al pie
        de la ultima pagina que crece hacia arriba a medida que agregan
        lineas (la ultima linea queda siempre pegada al margen inferior).
        """
        cell_h = QUANTIDIA_GRID_BOX_HEIGHT + QUANTIDIA_GRID_GAP_Y
        rows = max(1, math.ceil(total_grid / QUANTIDIA_GRID_COLUMNS))
        used = [q for q in slots.values()]
        if used:
            last_slot = sorted(
                used, key=lambda q: (q.page, q.posY, q.posX)
            )[-1]
            page = min(max(last_slot.page or 1, 1), len(page_sizes))
            page_h = page_sizes[page - 1][1]
            anchor_y = (last_slot.posY + last_slot.height) * page_h + QUANTIDIA_GRID_GAP_Y
            # Si la grilla no entra hasta el pie, se corre hacia arriba hasta
            # el minimo sensato (podria rozar el marcador, pero nunca cae
            # fuera de la pagina).
            anchor_y = min(anchor_y, page_h - QUANTIDIA_GRID_MARGIN_BOTTOM - rows * cell_h)
            return page, max(anchor_y, QUANTIDIA_GRID_MARGIN_BOTTOM)
        page = len(page_sizes)
        page_h = page_sizes[page - 1][1]
        return page, page_h - QUANTIDIA_GRID_MARGIN_BOTTOM - rows * cell_h

    @staticmethod
    def _quantidia_grid_rect(page, anchor_y, page_w, grid_index):
        """Recuadro del firmante ``grid_index`` en la grilla de 3 por linea.

        Orden z: llena de izquierda a derecha y baja a la linea siguiente al
        agotar la tercera columna.
        """
        cell_w = (page_w - 2 * QUANTIDIA_GRID_MARGIN_X) / QUANTIDIA_GRID_COLUMNS
        cell_h = QUANTIDIA_GRID_BOX_HEIGHT + QUANTIDIA_GRID_GAP_Y
        column = grid_index % QUANTIDIA_GRID_COLUMNS
        row = grid_index // QUANTIDIA_GRID_COLUMNS
        return {
            "page": page,
            "x": round(QUANTIDIA_GRID_MARGIN_X + column * cell_w + QUANTIDIA_GRID_PAD_X, 2),
            "y": round(anchor_y + row * cell_h, 2),
            "w": round(cell_w - 2 * QUANTIDIA_GRID_PAD_X, 2),
            "h": QUANTIDIA_GRID_BOX_HEIGHT,
        }

    def _quantidia_get_source_documents(self):
        """Devuelve los PDFs origen del request listos para el SDK de Quantidia.

        La fuente canonica del documento a firmar en Odoo Sign son los
        `template_document_ids` (`sign.document` -> `ir.attachment`): son los
        mismos PDFs que Odoo sirve en `/sign/download/.../origin` y que alimenta
        la vista nativa de firma. Reutilizar esa fuente evita inventar un origen
        paralelo y mantiene una sola definicion del documento del request.

        En Fase 2 (Augment), cuando la ronda digital esta activa, devuelve el
        PDF completado por Odoo (con todos los datos de los firmantes) en lugar
        del template original.

        Cada item trae el base64 del attachment (lista para decodificar en el
        browser a un `File`), su nombre y mime. Solo se exige acceso de lectura
        al `sign.request`: quien puede ver el request puede ver sus documentos.

        Returns:
            list[dict]: ``[{id, name, datas, mimetype}]`` por cada documento.
        """
        self.ensure_one()
        documents = []

        # En ronda digital (Augment), usar el PDF completado por Odoo. Si la
        # ronda esta activa pero aun no hay completed (Flujo B recien nacido),
        # se usa el template (else) — y los anexos se suman siempre al final.
        if self.quantidia_digital_round and self.completed_document_attachment_ids:
            # Devolver el completed PDF mas reciente (acumulativo)
            for attachment in self.completed_document_attachment_ids.sorted("create_date", reverse=True)[:1]:
                if not attachment.datas:
                    continue
                mimetype = attachment.mimetype or "application/pdf"
                if mimetype != "application/pdf":
                    continue
                documents.append({
                    "id": attachment.id,
                    "name": attachment.name,
                    "datas": attachment.datas,
                    "mimetype": mimetype,
                })
        else:
            # Fuente original: template_document_ids (Flujo B o Replace)
            for document in self.template_document_ids:
                attachment = document.attachment_id
                if not attachment or not attachment.datas:
                    continue
                # El SDK de Quantidia firma PDFs: no coaccionamos mime ausente a PDF
                # (lo ocultaba como exito). Se ignoran los documentos que no sean PDF.
                mimetype = attachment.mimetype or ""
                if mimetype != "application/pdf":
                    continue
                documents.append(
                    {
                        "id": document.id,
                        "name": attachment.name or document.name,
                        "datas": attachment.datas,
                        "mimetype": mimetype,
                    }
                )

        # Anexos (D11): PDFs adjuntados al chatter o al request que se firman
        # en la misma ronda, digital-only (sin campos electronicos). Se
        # excluyen los que ya van en el bundle para no duplicar: documentos
        # del template, completed documents y acumulados de la ronda (que ya
        # viven en completed_document_attachment_ids).
        already_in_bundle = set(
            self.template_document_ids.mapped("attachment_id").ids
        ) | set(self.completed_document_attachment_ids.ids)
        # Los adjuntos del chatter viven en ``mail.message.attachment_ids``;
        # mail.thread/sign.request no define un campo ``message_attachment_ids``.
        # Los archivos vinculados directamente al request tampoco viven en un
        # campo ``attachment_ids``, por lo que se buscan por modelo/registro.
        chatter_attachments = self.message_ids.mapped("attachment_ids")
        request_attachments = self.env["ir.attachment"].search([
            ("res_model", "=", self._name),
            ("res_id", "=", self.id),
        ])
        annex_candidates = (chatter_attachments + request_attachments).filtered(
            lambda a: a.mimetype == "application/pdf"
            and a.datas
            and a.id not in already_in_bundle
        )
        # Los anexos ya firmados por una ronda anterior se guardan con
        # res_field oculto, asi no re-entran al bundle (no estan en los
        # o2m/m2m de arriba, pero por si se linkean manualmente):
        annex_candidates = annex_candidates.filtered(lambda a: not a.res_field)
        # ACUMULACION de anexos (multi-firma serial): con la ronda en marcha,
        # el firmante N+1 debe recibir el anexo FIRMADO por el firmante N
        # (para que las firmas se sumen), no el original. Los anexos firmados
        # viven con res_field oculto; se identifican con su original por el
        # nombre base (X.pdf / X_signed.pdf / X_signed_signed.pdf ...).
        signed_annexes = {}
        if self.quantidia_digital_round:
            for sa in self.env["ir.attachment"].search([
                ("res_model", "=", self._name),
                ("res_id", "=", self.id),
                ("res_field", "=", "quantidia_annex_signed"),
            ], order="id"):
                signed_annexes[self._quantidia_annex_base(sa.name)] = sa
            if signed_annexes:
                annex_candidates = annex_candidates.filtered(
                    lambda a: self._quantidia_annex_base(a.name) not in signed_annexes
                )
        # Un archivo distinto puede compartir nombre con el PDF principal o
        # con otro anexo. La identidad para deduplicar es el attachment, no
        # el nombre que se mostrara al firmante.
        seen_attachment_ids = already_in_bundle.copy()
        annex_docs = list(annex_candidates) + list(signed_annexes.values())
        for attachment in annex_docs:
            name = attachment.name or ("annex_%s.pdf" % attachment.id)
            if attachment.id in seen_attachment_ids:
                continue
            seen_attachment_ids.add(attachment.id)
            documents.append(
                {
                    "id": attachment.id,
                    "name": name,
                    "datas": attachment.datas,
                    "mimetype": "application/pdf",
                }
            )
        return documents

    @staticmethod
    def _quantidia_annex_base(name):
        """Nombre base de un anexo: sin extension y sin sufijos _signed
        repetidos ("X_signed_signed.pdf" -> "X"). Identifica el anexo a
        traves de sus versiones firmadas acumuladas."""
        n = (name or "").rsplit(".", 1)[0]
        while n.endswith("_signed"):
            n = n[: -len("_signed")]
        return n or (name or "")

    def _quantidia_store_signed_pdf(self, pdf_bytes, filename=None, request_item_id=None, annex=False):
        """Persiste el PDF firmado que el SDK devolvio en el browser.

        El SDK NO sube el documento automaticamente: el frontend debe retirar
        los bytes del store en memoria (`getSignedDocumentBytes`) y postearlos
        a `/sign_quantidia/upload_signed`, que llama a este metodo.

        Args:
            pdf_bytes: Bytes del PDF firmado.
            filename: Nombre del archivo (opcional).
            request_item_id: ID del item del firmante (opcional, para tracking).
            annex: True si es un ANEXO (D11): solo se guarda como attachment
                oculto con nota en el chatter — sin tocar el puntero del PDF
                principal ni correr la logica de cierre de la ronda.
        """
        self.ensure_one()
        if not pdf_bytes:
            return False

        # Anexo (D11): adjunto oculto + nota en chatter. res_field lo deja
        # fuera del chatter y de la coleccion de anexos de la proxima ronda.
        if annex:
            # Acumulacion: la version nueva (con las firmas de todos los
            # firmantes hasta ahora) reemplaza a la anterior del MISMO anexo,
            # identificada por nombre base. Nombre normalizado a una sola
            # terminacion "_signed" (evita "_signed_signed_signed...").
            base = self._quantidia_annex_base(filename or "annex_%s.pdf" % self.id)
            self.env["ir.attachment"].search([
                ("res_model", "=", self._name),
                ("res_id", "=", self.id),
                ("res_field", "=", "quantidia_annex_signed"),
            ]).filtered(
                lambda a: self._quantidia_annex_base(a.name) == base
            ).unlink()
            attachment = self.env["ir.attachment"].create(
                {
                    "name": "%s_signed.pdf" % base,
                    "datas": base64.b64encode(pdf_bytes),
                    "res_model": self._name,
                    "res_id": self.id,
                    "res_field": "quantidia_annex_signed",
                    "mimetype": "application/pdf",
                }
            )
            # Link de descarga en la nota: el adjunto es oculto (res_field),
            # sin esto no habria forma de bajarlo desde la interfaz.
            download_url = "/web/content/%s?download=true" % attachment.id
            self.sudo().message_post(
                body=Markup(
                    "Anexo firmado digitalmente por Quantidia: "
                    "<a href='%s' target='blank'>%s</a>"
                    % (download_url, attachment.name)
                )
            )
            # La ronda se cierra con el upload del documento PRINCIPAL; los
            # anexos llegan despues, cuando la publicacion al registro
            # origen ya corrio. Publicar tambien este anexo alla.
            # Estado ANTES de registrar: si ESTE upload es el ultimo del
            # grupo, registrar cierra la ronda dentro de esta misma llamada
            # y la publicacion de cierre ya incluye este anexo. Publicarlo
            # tambien por el camino tardio duplicaria la copia.
            round_was_active = bool(self.quantidia_digital_round)
            if round_was_active and request_item_id:
                self._quantidia_register_upload(request_item_id)
            if not round_was_active and self.quantidia_status == "signed":
                record = self.reference_doc
                if record:
                    copy = self.env["ir.attachment"].create({
                        "name": attachment.name,
                        "datas": attachment.datas,
                        "res_model": record._name,
                        "res_id": record.id,
                        "mimetype": "application/pdf",
                    })
                    record.message_post(
                        body=Markup("Anexo firmado (Quantidia): %s" % attachment.name),
                        attachment_ids=copy.ids,
                    )
            return attachment

        # Verificar que el firmante tenga el turno (ronda serial)
        if request_item_id and self.quantidia_digital_round:
            tracking = self.quantidia_digital_signature_ids.filtered(
                lambda t: t.request_item_id.id == request_item_id
            )
            if not tracking or tracking.state != "pending":
                _logger.warning("quantidia: firmante %s no tiene turno (state: %s)",
                                  request_item_id, tracking.state if tracking else "not_found")
                raise ValueError("No es tu turno de firma digital. Espera a que los firmantes anteriores firmen.")

        attachment = self.env["ir.attachment"].create(
            {
                "name": filename or ("signed_%s.pdf" % self.id),
                "datas": base64.b64encode(pdf_bytes),
                "res_model": self._name,
                "res_id": self.id,
                "mimetype": "application/pdf",
            }
        )

        # En modo Augment (ronda digital), actualizar el PDF completado vigente
        # (acumulativo: cada firma sobrescribe el anterior)
        if self.quantidia_digital_round:
            self.write({"quantidia_signed_attachment_id": attachment.id})
            # Actualizar el completed document vigente
            completed_vals = []
            for document in self.template_document_ids[:1] or []:
                completed_vals.append({
                    "sign_request_id": self.id,
                    "document_id": document.id,
                    "file": base64.b64encode(pdf_bytes),
                })
            if completed_vals:
                existing = self.completed_document_ids.filtered(
                    lambda c: c.document_id.id == completed_vals[0]["document_id"]
                )
                if existing:
                    existing.write({"file": completed_vals[0]["file"]})
                else:
                    self.env["sign.completed.document"].create(completed_vals)
                # tambien linkear el attachment como completed para el dropdown/chatter
                self.write({"completed_document_attachment_ids": [(4, attachment.id)]})

            # Contar el upload del firmante: el tracking se marca signed y
            # la ronda avanza SOLO cuando llego el ultimo documento del
            # grupo (ver _quantidia_register_upload). Cerrar con el
            # principal publicaba anexos con firmas parciales.
            if request_item_id:
                self._quantidia_register_upload(request_item_id)
            else:
                _logger.warning(
                    "quantidia: upload sin request_item_id en request %s "
                    "(no se registra avance del firmante)", self.id,
                )
                self._check_digital_round_complete()

            self.sudo().message_post(
                body="PDF firmado digitalmente por Quantidia (acumulativo): %s" % attachment.name
            )
        else:
            # Modo Replace: comportamiento original
            self.write(
                {
                    "quantidia_signed_attachment_id": attachment.id,
                    "quantidia_status": "signed",
                    # Forzar state='signed' para 1 firmante (no hay ronda digital)
                    "state": "signed",
                }
            )
            # Registrar el PDF firmado por Quantidia como el "completed document"
            # del request: asi aparece en el dropdown nativo de documentos
            # completados (la vista natural del usuario), sin boton aparte.
            completed_vals = []
            for document in self.template_document_ids[:1] or []:
                completed_vals.append({
                    "sign_request_id": self.id,
                    "document_id": document.id,
                    "file": base64.b64encode(pdf_bytes),
                })
            if completed_vals:
                existing = self.completed_document_ids.filtered(
                    lambda c: c.document_id.id == completed_vals[0]["document_id"]
                )
                if existing:
                    existing.write({"file": completed_vals[0]["file"]})
                else:
                    self.env["sign.completed.document"].create(completed_vals)
                # tambien linkear el attachment como completed para el dropdown/chatter
                self.write({"completed_document_attachment_ids": [(4, attachment.id)]})
            self.sudo().message_post(
                body="Documento firmado mediante Quantidia: %s" % attachment.name
            )
            # Finaliza el circuito nativo de Odoo Sign para que la UI/server dejen
            # de mostrar "Validar y enviar" y el request quede "signed". En modo
            # Replace Quantidia firma por el documento entero, asi que completamos
            # el/los item(s) pendiente(s) y pasamos el request a signed. No llamamos
            # a `sign.request._sign()` nativo para evitar el envio de mails y la
            # generacion del PDF completado nativo (que chocaria con el de Quantidia).
            try:
                self._quantidia_finalize_native()
            except Exception as exc:  # no romper el upload por la finalizacion nativa
                _logger.warning("quantidia: finalize native fallo: %s", exc)

        return attachment

    def _quantidia_finalize_native(self):
        """Completar el item del firmante y pasar el request a 'signed'.

        Replica lo minimo de la finalizacion nativa (sign.request.item.
        _post_fill_request_item + state='signed') para que Odoo refleje el
        cierre, sin disparar el envio de mails ni la generacion del completed
        nativo (que chocaria con el PDF firmado por Quantidia).
        """
        self.ensure_one()
        pending_items = self.request_item_ids.filtered(lambda it: it.state == "sent")
        # Contexto quantidia_digital_completion: el _sign() que dispara la
        # completitud de items no publica nada (ver override).
        for item in pending_items.sudo().with_context(
            quantidia_digital_completion=True
        ):
            # _post_fill_request_item: log + signing_date + state='completed'
            item._post_fill_request_item()
        if self.state == "sent" and all(
            it.state == "completed" for it in self.request_item_ids
        ):
            self.sudo().with_context(
                quantidia_digital_completion=True
            ).write({"state": "signed"})
