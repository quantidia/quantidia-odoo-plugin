/* @odoo-module */
/**
 * Client action del boton "Firmar anexos" del header del sign.request.
 *
 * El header solo dispara acciones de servidor; esta client action recibe la
 * configuracion que devuelve `action_open_quantidia_sign_header` y abre el
 * SDK de Quantidia (misma logica que el boton del chatter). Al cerrar el
 * modal, vuelve al formulario.
 */

import { openQuantidiaSign } from "@sign_quantidia/js/sign_quantidia_core";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const { Component, onMounted } = owl;

export class QuantidiaSignOpenAction extends Component {
    setup() {
        super.setup();
        this.action = useService("action");
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.ui = useService("ui");
        onMounted(async () => {
            const params = this.props.action.params || {};
            await openQuantidiaSign(
                {
                    orm: this.orm,
                    notification: this.notification,
                    action: this.action,
                    ui: this.ui,
                },
                params.signRequestId,
                params.config
            );
            this.action.doAction({ type: "ir.actions.act_window_close" });
        });
    }
}

QuantidiaSignOpenAction.template = "sign_quantidia.SignOpen";

registry
    .category("actions")
    .add("sign_quantidia.sign_open", QuantidiaSignOpenAction);
