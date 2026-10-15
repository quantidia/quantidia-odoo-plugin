# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class QuantidiaSigner(models.Model):
    """Perfil Quantidia de un partner (opcional, 1:1 con res.partner).

    No es mandatorio: un partner puede firmar digital sin registro. Si existe,
    el introspect toma de aca los claims que Odoo no tiene separados
    (given_name / family_name) y usa el **id de esta tabla** como
    `reference.userId` para Quantidia. Si no existe, el introspect cae al split
    del partner.name y userId va vacio.

    Los datos de ciclo de vida Quantidia (certificado, etc.) se agregan cuando
    se integre la API de ellos; por ahora el modelo es solo identidad.
    """

    _name = "quantidia.signer"
    _description = "Perfil Quantidia de firmante"
    _inherit = ["mail.thread"]
    _order = "partner_id"
    _rec_name = "name_complete"

    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Partner",
        required=False,  # opcional: se asocia si corresponde a un partner Odoo
        ondelete="restrict",
        index=True,
        tracking=True,
    )
    user_id = fields.Many2one(
        comodel_name="res.users",
        compute="_compute_user_id",
        string="Usuario Odoo",
        store=False,
    )
    # Claims OIDC que ve Quantidia (Odoo no los tiene separados en res.partner).
    given_name = fields.Char(string="Nombre", required=True, tracking=True)
    family_name = fields.Char(string="Apellido", required=True, tracking=True)
    username = fields.Char(
        string="Username",
        required=True,
        tracking=True,
        help="Username propio para Quantidia. Se pre-llena con el login de Odoo "
        "si el partner tiene usuario, o con el email si no.",
    )
    email = fields.Char(
        string="Email",
        required=True,
        tracking=True,
        help="Email del firmante para Quantidia. Se pre-llena con el email del "
        "partner, pero es editable (puede diferir del de Odoo).",
    )
    name_complete = fields.Char(compute="_compute_name_complete")

    active = fields.Boolean(default=True)
    note = fields.Text(string="Notas")

    def _auto_init(self):
        # Indices unicos parciales (Odoo 19 depreca _sql_constraints; los
        # parciales ademas permiten multiples NULLs/empty correctamente).
        res = super()._auto_init()
        self.env.cr.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS quantidia_signer_username_uniq "
            "ON quantidia_signer (username) WHERE username IS NOT NULL AND username <> ''"
        )
        self.env.cr.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS quantidia_signer_partner_uniq "
            "ON quantidia_signer (partner_id) WHERE partner_id IS NOT NULL"
        )
        return res

    @api.depends("given_name", "family_name", "partner_id.name")
    def _compute_name_complete(self):
        for rec in self:
            rec.name_complete = (
                rec.partner_id.name or (rec.given_name or "") + " " + (rec.family_name or "")
            ).strip()

    @api.depends("partner_id")
    def _compute_user_id(self):
        for rec in self:
            rec.user_id = rec.partner_id.user_ids[:1] if rec.partner_id.user_ids else False

    @api.constrains("partner_id")
    def _check_partner_unique(self):
        for rec in self:
            if rec.partner_id:
                existing = self.search(
                    [("partner_id", "=", rec.partner_id.id), ("id", "!=", rec.id)],
                    limit=1,
                )
                if existing:
                    raise ValidationError(
                        _("Ese partner ya tiene perfil Quantidia: %s") % rec.partner_id.display_name
                    )

    @api.onchange("partner_id")
    def _onchange_partner_id(self):
        # Al elegir un partner, pre-llenar given/family partiendo partner.name y
        # username con el login de Odoo (si el partner tiene usuario). Solo si
        # estan vacios, para no pisar lo cargado a mano.
        if self.partner_id:
            if self.partner_id.name:
                parts = self.partner_id.name.strip().split(" ", 1)
                if not self.given_name:
                    self.given_name = parts[0] if parts else ""
                if not self.family_name:
                    self.family_name = parts[1] if len(parts) > 1 else (parts[0] if parts else "")
            user = self.partner_id.user_ids[:1]
            if not self.username:
                if user and user.login:
                    self.username = user.login
                elif self.partner_id.email:
                    self.username = self.partner_id.email
            if not self.email and self.partner_id.email:
                self.email = self.partner_id.email

    def action_open_partner(self):
        """Smart-button: abre el partner del signer."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "res.partner",
            "res_id": self.partner_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def write(self, vals):
        # partner_id no es modificable una vez creado el signer (link permanente).
        vals.pop("partner_id", None)
        res = super().write(vals)
        # Sync email: si cambio el email del signer, actualizar el del partner.
        if vals.get("email"):
            for rec in self:
                if rec.partner_id and rec.partner_id.email != rec.email:
                    rec.partner_id.write({"email": rec.email})
        return res

    @api.model_create_multi
    def create(self, vals_list):
        # El partner_id es mandatorio en el resultado: si el usuario lo asigno,
        # se usa y se completa la identidad desde el; si no lo asigno, se crea
        # un partner persona (is_company=False) desde given/family/email.
        Partner = self.env["res.partner"]
        for vals in vals_list:
            partner = Partner.browse(vals.get("partner_id")).exists() if vals.get("partner_id") else Partner
            if partner:
                if partner.name and not vals.get("given_name") and not vals.get("family_name"):
                    parts = partner.name.strip().split(" ", 1)
                    vals.setdefault("given_name", parts[0])
                    vals.setdefault("family_name", parts[1] if len(parts) > 1 else parts[0])
                if not vals.get("email"):
                    if partner.email:
                        vals.setdefault("email", partner.email)
                    else:
                        # si el partner no tiene email, probar el del usuario Odoo
                        user = partner.user_ids[:1]
                        if user and user.email:
                            vals.setdefault("email", user.email)
                if not vals.get("username"):
                    user = partner.user_ids[:1]
                    if user and user.login:
                        vals.setdefault("username", user.login)
                    elif partner.email:
                        vals.setdefault("username", partner.email)
            else:
                name = (
                    (vals.get("given_name") or "") + " " + (vals.get("family_name") or "")
                ).strip() or "Quantidia Signer"
                partner = Partner.create(
                    {
                        "name": name,
                        "email": vals.get("email") or False,
                        "is_company": False,
                        "type": "contact",
                        # tz=False para no heredar el tz del contexto (que puede
                        # traer un valor invalido para el Selection de res.partner.tz).
                        "tz": False,
                    }
                )
                vals["partner_id"] = partner.id
        return super().create(vals_list)
