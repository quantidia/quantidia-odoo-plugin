/* @odoo-module */
/**
 * Integracion Quantidia Sign - entrada por el chatter del backend.
 *
 * El boton de firma de sign.request vive ahora en el HEADER del formulario
 * (`action_open_quantidia_sign_header` + client action en
 * sign_quantidia_header_button.js). Aca queda solo la entrada (deshabilitada
 * por ahora) al wizard ad hoc para registros con chatter.
 */

import { Chatter } from "@mail/chatter/web_portal/chatter";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";

patch(Chatter.prototype, {
    setup() {
        super.setup();
        this.orm = useService("orm");
        this.notification = useService("notification");
    },
});
