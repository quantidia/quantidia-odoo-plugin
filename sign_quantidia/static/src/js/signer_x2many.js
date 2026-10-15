/** @odoo-module **/
/**
 * Badge "Quantidia" en el widget signer_x2many (wizard de nueva solicitud).
 *
 * Es un COMPONENTE PROPIO (QuantidiaBadge) que recibe el record (DataPoint) y
 * lee partner_id directamente. Asi OWL lo re-renderiza solo cuando cambia el
 * partner del firmante (antes era un span hermano que dependia del re-render
 * de la fila, que no se disparaba al cambiar el Many2One).
 */

import { SignerX2Many, signerX2Many } from "@sign/fields/signer_x2many";
import { Component, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { patch } from "@web/core/utils/patch";
import { rpc } from "@web/core/network/rpc";

patch(signerX2Many, {
    relatedFields() {
        return [
            ...super.relatedFields(),
            { name: "quantidia_digital_ready", type: "boolean", readonly: true },
        ];
    },
});

export class QuantidiaBadge extends Component {
    static template = "sign_quantidia.QuantidiaBadge";
    static props = { record: Object };

    setup() {
        // lista (reactiva) de partner ids con perfil Quantidia activo
        this.state = useState({ partnerIds: [] });
        this._loadQuantidiaSigners();
    }

    async _loadQuantidiaSigners() {
        try {
            const res = await rpc("/sign_quantidia/signer_partner_ids", {});
            this.state.partnerIds = res.partner_ids || [];
        } catch (e) {
            this.state.partnerIds = [];
        }
    }

    /** Id del partner del firmante de esta linea (o null si no hay). */
    get partnerId() {
        const pid = this.props.record && this.props.record.data.partner_id;
        if (!pid) return null;
        // En Odoo 19 el many2one en record.data es un objeto {id, display_name}
        // (Proxy), no un array [id, name]. Soportar ambas formas.
        if (Array.isArray(pid)) return pid[0];
        if (typeof pid === "object") return pid.id || null;
        return pid || null;
    }

    /** True si el partner tiene perfil Quantidia activo. */
    get ready() {
        return this.partnerId != null && this.state.partnerIds.includes(this.partnerId);
    }
}

// Registrar el componente para usarlo en el template heredado de SignerX2Many.
SignerX2Many.components = { ...(SignerX2Many.components || {}), QuantidiaBadge };

/**
 * Variante para solicitudes ad hoc: el widget nativo renderiza firmantes ya
 * definidos por un template, pero este flujo parte sin template. Sumamos el
 * botón de alta conservando exactamente los campos y el aspecto de Odoo Sign.
 */
export class QuantidiaAdhocSignerX2Many extends SignerX2Many {
    static template = "sign_quantidia.AdhocSignerX2Many";

    setup() {
        super.setup();
        this.quantidiaState = useState({ partnerIds: [] });
        this._loadQuantidiaSigners();
    }

    async _loadQuantidiaSigners() {
        try {
            const res = await rpc("/sign_quantidia/signer_partner_ids", {});
            this.quantidiaState.partnerIds = res.partner_ids || [];
        } catch (e) {
            this.quantidiaState.partnerIds = [];
        }
    }

    get partnerIdFieldInfo() {
        const fieldInfo = super.partnerIdFieldInfo;
        return {
            ...fieldInfo,
            additionalProps: {
                ...fieldInfo.additionalProps,
                domain: [["id", "in", this.quantidiaState.partnerIds]],
            },
        };
    }

    async addSigner() {
        await this.onAdd({ editable: true });
        const signer = this.list.records.at(-1);
        if (signer) {
            await signer.update({ mail_sent_order: this.list.records.length });
        }
    }

    async moveSigner(record, direction) {
        const records = this.list.records;
        const currentIndex = records.indexOf(record);
        const targetIndex = currentIndex + direction;
        if (currentIndex < 0 || targetIndex < 0 || targetIndex >= records.length) {
            return;
        }

        const reordered = [...records];
        [reordered[currentIndex], reordered[targetIndex]] = [
            reordered[targetIndex],
            reordered[currentIndex],
        ];
        records.splice(0, records.length, ...reordered);
        for (const [index, signer] of records.entries()) {
            await signer.update({ mail_sent_order: index + 1 });
        }
    }
}

QuantidiaAdhocSignerX2Many.components = {
    ...(SignerX2Many.components || {}),
    QuantidiaBadge,
};

registry.category("fields").add("quantidia_adhoc_signer_x2many", {
    ...signerX2Many,
    component: QuantidiaAdhocSignerX2Many,
    relatedFields: signerX2Many.relatedFields,
});
