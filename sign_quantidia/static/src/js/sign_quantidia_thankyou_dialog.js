/* @odoo-module */
/**
 * Parche del dialogo ThankYouDialog para modo digital (Augment) multi-firmante.
 *
 * El mensaje nativo ("recibira el documento firmado por correo") solo aplica
 * cuando la firma ya cerro. En modo digital con varios firmantes el proceso
 * CONTINUA: faltan firmantes por completar electronicamente y despues arranca
 * la ronda digital de Quantidia. Se consulta el avance al backend y se ajusta
 * el mensaje antes del primer render (willStart corre via onWillStart).
 *
 * Override directo del prototype (sin helper `patch`) para no depender de que
 * @web/core/utils/patch este presente en sign.assets_public_sign, igual que
 * sign_quantidia_public_sign.js. Este archivo carga en AMBOS bundles.
 */

import { ThankYouDialog } from "@sign/dialogs/thank_you_dialog";
import { rpc } from "@web/core/network/rpc";

console.log("[quantidia] thankyou: patch registrado");
const _originalWillStart = ThankYouDialog.prototype.willStart;

ThankYouDialog.prototype.willStart = async function quantidiaWillStart() {
    await _originalWillStart.call(this);
    if (this.props.isRefused) {
        console.log("[quantidia] thankyou: rechazo, mensaje nativo");
        return;
    }
    const signRequestId = this.signInfo.get("documentId");
    const itemToken = this.signInfo.get("signRequestToken"); // presente solo en pagina publica
    console.log("[quantidia] thankyou: documentId=", signRequestId, "| token?", !!itemToken);
    if (!signRequestId) {
        console.warn("[quantidia] thankyou: sin documentId, mensaje nativo");
        return;
    }
    let progress = null;
    try {
        progress = await rpc(
            itemToken ? "/sign_quantidia/sign_progress_public" : "/sign_quantidia/sign_progress",
            itemToken
                ? { sign_request_id: signRequestId, access_token: itemToken }
                : { sign_request_id: signRequestId }
        );
    } catch (e) {
        console.warn("[quantidia] thankyou: sign_progress fallo, mensaje nativo", e);
        return;
    }
    console.log("[quantidia] thankyou: progress=", JSON.stringify(progress));
    if (!progress || progress.mode !== "digital") {
        return;
    }
    // La fase la computa el backend (unica fuente de verdad); aca solo se
    // mapea fase -> mensaje.
    const phase = progress.phase;
    const remaining = progress.remaining_signers || 0;
    const plural = remaining === 1 ? "" : "n";
    const qty = remaining === 1 ? "1 participante" : remaining + " participantes";
    if (phase === "data_collection") {
        this.message =
            "Completaste la carga de tus datos. Falta" + plural + " " + qty +
            " por completar los suyos. Cuando todos hayan terminado, comenzará la ronda de " +
            "firma digital Quantidia y recibirás un correo cuando sea tu turno para firmar.";
    } else if (phase === "electronic_signing") {
        this.message =
            "Firmaste el documento. Falta" + plural + " " + qty +
            " por firmar. Cuando todos hayan firmado, comenzará la ronda de firma digital " +
            "Quantidia y recibirás un correo cuando sea tu turno para firmar.";
    } else if (phase === "digital_round") {
        if (progress.my_turn) {
            this.message =
                "La ronda de firma digital Quantidia ya comenzó y es tu turno: " +
                "firmá desde el botón «Firmar digitalmente» de tu enlace de firma " +
                "(te llegó por correo).";
        } else {
            this.message =
                "Todos los participantes completaron su parte. Comenzó la ronda de firma digital " +
                "Quantidia: recibirás un correo cuando sea tu turno para firmar.";
        }
    } else {
        // complete / cancelled / electronic: el mensaje nativo ya es correcto.
        return;
    }
    console.log("[quantidia] thankyou: mensaje ajustado (phase=" + phase + ", remaining=" + remaining + ")");
};
