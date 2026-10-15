/* @odoo-module */
/**
 * Integracion Quantidia Sign - pagina de firma (sign.assets_public_sign).
 *
 * Intercepta el "Validar y enviar documento completado" nativo
 * (Document.signDocuments) segun el estado del request:
 *
 * - Ronda digital ACTIVA (Flujo B desde el nacimiento, Flujo A post-firma):
 *   la firma nativa NO debe completar electronicamente. Si es tu turno se
 *   abre el SDK de Quantidia directo; si no, aviso y no se completa nada.
 * - Replace (digital, 1 solo firmante): Quantidia directo sin electronica.
 * - Resto (Flujo A con campos, electronic): flujo nativo intacto.
 *
 * `Document.prototype` cubre AMBOS flujos (portal SignableDocument y client
 * action backend). Se carga en `sign.assets_public_sign`.
 */

import { Document } from "@sign/components/sign_request/document_signable";
import { openQuantidiaSign } from "@sign_quantidia/js/sign_quantidia_core";
import { rpc } from "@web/core/network/rpc";

// `signDocuments` vive en la clase base `Document` (no en SignableDocument).
const _originalSignDocuments = Document.prototype.signDocuments;

Document.prototype.signDocuments = async function quantidiaSignDocuments() {
    const signRequestId = this.signInfo.get("documentId");
    const itemToken = this.signInfo.get("signRequestToken"); // presente solo en pagina publica
    const isPublic = !!itemToken;

    let mode = null;
    let numSigners = 0;
    let roundActive = false;
    let myTurn = false;
    let myState = "";
    try {
        const check = signRequestId
            ? await rpc(
                  isPublic ? "/sign_quantidia/is_last_signer_public" : "/sign_quantidia/is_last_signer",
                  isPublic
                      ? { sign_request_id: signRequestId, access_token: itemToken }
                      : { sign_request_id: signRequestId }
              )
            : null;
        mode = check && check.mode;
        numSigners = (check && check.num_signers) || 0;
        roundActive = !!(check && check.round_active);
        myTurn = !!(check && check.my_turn);
        myState = (check && check.my_state) || "";
    } catch (e) {
        mode = null;
        numSigners = 0;
    }

    // Ronda digital activa: nunca completar electronicamente. La firma es
    // Quantidia (serial); completar el item aca confundiria el estado del
    // request ("firmado" nativo sin firma digital real).
    if (mode === "digital" && roundActive) {
        if (myState === "signed") {
            this.env.services.notification.add(
                "Ya firmaste digitalmente este documento. Cuando todos los participantes firmen recibirás el PDF final por correo.",
                { type: "info" }
            );
            this.validateBanner?.removeAttribute?.("disabled");
            return;
        }
        if (!myTurn) {
            this.env.services.notification.add(
                "Todavía no es tu turno de firma digital. Recibirás un correo cuando llegue tu turno.",
                { type: "warning" }
            );
            this.validateBanner?.removeAttribute?.("disabled");
            return;
        }
        try {
            let config;
            if (isPublic) {
                config = await rpc("/sign_quantidia/prepare_public", {
                    sign_request_id: signRequestId, access_token: itemToken,
                });
                config.itemToken = itemToken;
                // Al terminar, recargar la misma pagina para ver el estado.
                config.successRedirect = window.location.pathname;
            } else {
                config = await this.orm.call(
                    "sign.request", "action_prepare_quantidia_sign", [[signRequestId]]
                );
            }
            if (config.error) {
                throw new Error(config.error);
            }
            await openQuantidiaSign(
                { orm: this.orm, notification: this.env.services.notification, action: this.env.services.action, ui: this.env.services.ui },
                signRequestId,
                config
            );
        } catch (e) {
            this.env.services.notification.add(
                "No se pudo iniciar la firma digital: " + (e?.message || e),
                { type: "danger" }
            );
        } finally {
            this.validateBanner?.removeAttribute?.("disabled");
        }
        return;
    }

    // Replace (digital, 1 solo firmante): Quantidia directo, sin electronica.
    // Multi-firmante SIN ronda activa (Flujo A): dejar el flujo nativo — la
    // ronda arranca sola al terminar la fase electronica.
    const shouldIntercept = mode === "digital" && numSigners === 1;

    if (shouldIntercept) {
        try {
            let config;
            if (isPublic) {
                config = await rpc("/sign_quantidia/prepare_public", {
                    sign_request_id: signRequestId, access_token: itemToken,
                });
                config.itemToken = itemToken;
            } else {
                config = await this.orm.call(
                    "sign.request", "action_prepare_quantidia_sign", [[signRequestId]]
                );
            }
            await openQuantidiaSign(
                { orm: this.orm, notification: this.env.services.notification, action: this.env.services.action, ui: this.env.services.ui },
                signRequestId,
                config
            );
        } catch (e) {
            this.env.services.notification.add(
                "No se pudo iniciar la firma Quantidia: " + (e?.message || e),
                { type: "danger" }
            );
        } finally {
            this.validateBanner?.removeAttribute?.("disabled");
        }
        return;
    }

    return _originalSignDocuments.call(this);
};
