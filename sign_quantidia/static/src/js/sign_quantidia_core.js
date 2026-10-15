/* @odoo-module */
/**
 * Nucleo de la integracion Quantidia Sign.
 *
 * Helpers compartidos por el patch del chatter (backend) y por el patch de la
 * pagina de firma (sign.assets_public_sign): carga del SDK, recoleccion de los
 * PDFs origen, apertura del modal y upload del PDF firmado.
 *
 * Depende solo de `rpc` (disponible tanto en web.assets_backend como en
 * sign.assets_public_sign), por lo que este archivo puede cargarse en ambos
 * bundles sin romper.
 */

import { rpc } from "@web/core/network/rpc";

const SDK_CDN =
    "https://cdn.jsdelivr.net/npm/@quantidia/sdk@1/dist/quantidia-sdk.umd.min.js";

let sdkPromise = null;

function loadSdk() {
    if (window.Quantidia) {
        return Promise.resolve(window.Quantidia);
    }
    if (!sdkPromise) {
        sdkPromise = new Promise((resolve, reject) => {
            const script = document.createElement("script");
            script.src = SDK_CDN;
            script.onload = () => {
                if (window.Quantidia) return resolve(window.Quantidia);
                reject(new Error("Quantidia SDK no quedo expuesto en window.Quantidia"));
            };
            script.onerror = () => reject(new Error("No se pudo cargar el SDK Quantidia"));
            document.head.appendChild(script);
        });
    }
    return sdkPromise;
}

/**
 * Decodifica base64 a Blob via fetch sobre data-URL (mas rapido que atob para
 * PDFs grandes y deja un Blob listo para envolver en un File del SDK).
 */
async function base64ToBlob(b64, mime) {
    const response = await fetch(`data:${mime || "application/pdf"};base64,${b64}`);
    return response.blob();
}

/**
 * Pide al backend los PDFs origen del sign.request y los devuelve como File[]
 * listos para `SDK.addDocuments`. Fuente canonica: `template_document_ids`.
 */
async function collectPdfFiles(signRequestId, publicToken) {
    const url = publicToken
        ? "/sign_quantidia/get_source_documents_public"
        : "/sign_quantidia/get_source_documents";
    const params = publicToken
        ? { sign_request_id: signRequestId, access_token: publicToken }
        : { sign_request_id: signRequestId };
    const result = await rpc(url, params);
    if (result && result.error) {
        throw new Error("source_documents:" + result.error);
    }
    if (!result || !Array.isArray(result.documents)) {
        throw new Error("source_documents:bad_response");
    }
    const files = [];
    for (const doc of result.documents) {
        if (!doc.datas) continue;
        const blob = await base64ToBlob(doc.datas, doc.mimetype);
        const name = doc.name && doc.name.toLowerCase().endsWith(".pdf")
            ? doc.name
            : `${doc.name || "document"}.pdf`;
        files.push(new File([blob], name, { type: doc.mimetype || "application/pdf" }));
    }
    return files;
}

/** ArrayBuffer/Uint8Array -> base64, por chunks para no trabar el main thread. */
function bytesToBase64(bytes) {
    const view = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
    let binary = "";
    const CHUNK = 0x8000;
    for (let i = 0; i < view.length; i += CHUNK) {
        binary += String.fromCharCode.apply(null, view.subarray(i, i + CHUNK));
    }
    return btoa(binary);
}

async function uploadSignedDocuments(signRequestId, SDK, publicToken, sessionJwt) {
    const docs = SDK.listSignedDocuments();
    for (let i = 0; i < docs.length; i++) {
        const meta = docs[i];
        const bytes = SDK.getSignedDocumentBytes(meta.id);
        if (!bytes) continue;
        const datas = bytesToBase64(bytes);
        const url = publicToken
            ? "/sign_quantidia/upload_signed_public"
            : "/sign_quantidia/upload_signed";
        const params = { sign_request_id: signRequestId, datas, filename: meta.name || "signed.pdf" };
        if (publicToken) params.access_token = publicToken;
        // D8: la sesion Quantidia (jti) que produjo esta firma se consume
        // single-use del lado servidor.
        if (sessionJwt) params.session_token = sessionJwt;
        // D11: el primer documento del bundle es el principal; el resto son
        // anexos (se guardan aparte, sin tocar el cierre de la ronda).
        if (i > 0) params.annex = true;
        const res = await rpc(url, params);
        if (!res || !res.ok) {
            throw new Error("upload_failed:" + (res?.error || "unknown"));
        }
    }
}

/**
 * Abre el modal de Quantidia sobre los PDFs del sign.request.
 *
 * @param {Object} services - { orm, notification }
 * @param {number} signRequestId - id del sign.request
 * @param {Object} config - { baseUrl, apiBase, environmentId, apiKey, authMode,
 *                            testUsername, testPassword }
 */
async function openQuantidiaSign(services, signRequestId, config) {
    // Loading overlay mientras se prepara todo (token, PDFs, SDK init).
    services.ui?.block();
    let SDK;
    try {
        SDK = await loadSdk();
        const initParams = {
            baseUrl: config.baseUrl,
            apiBase: config.apiBase,
            view: "full",
            flow: "SSO_SIGN",
            quantidiaJava: { force: false },
        };
        console.log("[quantidia] SDK.init params:", JSON.parse(JSON.stringify(initParams)));
        SDK.init(initParams);
    } catch (err) {
        console.error("[quantidia] SDK init fallo:", err);
        services.ui?.unblock();
        services.notification.add(
            "No se pudo inicializar el SDK de Quantidia: " + (err?.message || err),
            { type: "danger", sticky: true }
        );
        return;
    }

    // 1) El backend entrega el token recién emitido en la configuracion.
    // Evitamos releer el record por ORM: durante la vista nativa de Sign esa
    // segunda RPC puede quedar pendiente y dejar bloqueada la UI.
    const accessToken = config.accessToken || null;
    if (!accessToken && config.authMode !== "simple") {
        services.ui?.unblock();
        services.notification.add(
            "No hay token de sesion Quantidia. Inicie el flujo primero.",
            { type: "warning" }
        );
        return;
    }

    // 2) Cargar los PDFs origen como File[] en el SDK.
    let files;
    try {
        files = await collectPdfFiles(signRequestId, config.itemToken);
    } catch (err) {
        const code = (err?.message || "").split(":")[1];
        const msg = code === "access_denied"
            ? "Sin acceso a los documentos del request."
            : code === "sign_request not found"
                ? "No se encontro el request de firma."
                : "No se pudieron obtener los documentos del request.";
        services.ui?.unblock();
        services.notification.add(msg, { type: "danger" });
        return;
    }
    if (!files.length) {
        services.ui?.unblock();
        services.notification.add("No hay PDFs para firmar en este request.", {
            type: "warning",
        });
        return;
    }
    SDK.clearDocuments();
    const docIds = await SDK.addDocuments(files);

    // 3) Abrir el modal: sso -> { access_token }, simple -> { username, password }.
    // Estructura del manual de Quantidia (openSigningWithLogin).
    // authReference con TODOS los campos. Credenciales anidadas.
    // SSO: bearer. Simple: username/password.
    const authLoginInner = config.authMode === "simple"
        ? { username: config.testUsername, password: config.testPassword }
        : { access_token: accessToken };
    console.log(
        "[quantidia] authMode=" + config.authMode +
        " | access_token? " + (!!authLoginInner.access_token) +
        " | environmentId=" + config.environmentId +
        " | companyId=" + config.companyId +
        " | agencyId=" + config.agencyId
    );
    const signingParams = {
        authLogin: {
            authReference: {
                environmentId: config.environmentId,
                companyId: config.companyId,
                agencyId: config.agencyId,
                userId: "",
                subscriptionId: "",
            },
            authLogin: authLoginInner,
        },
        headersOverride: { apiKey: config.apiKey, acceptLanguage: "es-AR" },
        docId: docIds[0],
        docIds,
    };
    // Posicion del recuadro de firma de ESTE firmante (puntos PDF, pagina
    // 1-based, x desde la izquierda e y desde ABAJO — coordenadas PDF
    // estandar). Sin esto, cada firma de la ronda serial cae en el default
    // del SDK y los sellos se superponen.
    if (config.signatureRect) {
        signingParams.signatureRect = config.signatureRect;
    }
    console.log("[quantidia] openSigningWithLogin params:", JSON.parse(JSON.stringify(signingParams, (k, v) => {
        if (k === "access_token" && typeof v === "string") return "<redacted>";
        return v;
    })));
    // Quitar el loading overlay justo antes de que abra el modal del SDK.
    services.ui?.unblock();
    try {
        const result = await SDK.openSigningWithLogin(signingParams);

        // 4) Retirar los bytes del store y subirlos a Odoo (no se suben solos).
        // El sessionJwt (jti) viaja con el upload para su consumo single-use.
        try {
            await uploadSignedDocuments(signRequestId, SDK, config.itemToken, accessToken);
        } catch (uploadErr) {
            // El doc se firmo en el SDK pero fallo la persistencia: avisar y
            // quedarse en la pagina (no navegar) para que pueda reintentar.
            services.notification.add(
                "Se firmo en Quantidia pero no se pudo guardar en Odoo: " +
                    (uploadErr?.message || uploadErr),
                { type: "danger", sticky: true }
            );
            return;
        }
        // Exito: avisar y volver. En backend -> lista de documentos; en el
        // portal (D9) -> recargar la MISMA pagina, que se re-renderiza con
        // el estado actualizado de la ronda digital.
        services.notification.add(
            "Documento firmado por Quantidia correctamente.", { type: "success" }
        );
        if (config.successRedirect) {
            window.location.href = config.successRedirect;
        } else {
            _backToDocuments(services);
        }
        return result;
    } catch (err) {
        // El documento NO se firmo: distinguir cancelacion vs problema del SDK.
        if (err?.status === "cancelled") {
            // Reportar rechazo al backend (Fase 2 - Augment)
            // Esto cancela la ronda digital pero NO la firma electronica (Opcion A)
            reportRejection(signRequestId, config.itemToken, err?.message || "Rechazado por el firmante");
            services.notification.add(
                "Firma digital rechazada. La ronda digital se canceló, pero la firma electrónica se mantiene.",
                { type: "warning" }
            );
        } else {
            services.notification.add(
                "No se pudo firmar el documento (problema con el SDK de Quantidia). " +
                    "El documento no fue firmado. " + (err?.message || err),
                { type: "danger", sticky: true }
            );
        }
        // En cancel/error no navegamos: el usuario queda en la pagina para
        // reintentar o revisar.
    } finally {
        SDK.clearSignedDocuments?.();
    }
}

/** Vuelve a la lista de documentos de Sign. Redirección simple (más robusta
 *  que doAction, que requiere un action dict completo o crashea en _preprocessAction). */
function _backToDocuments(services) {
    window.location.href = "/odoo/sign-documents";
}

/** Reporta el rechazo de un firmante al backend (Fase 2 - Augment). */
async function reportRejection(signRequestId, publicToken, reason) {
    const url = publicToken
        ? "/sign_quantidia/report_rejected_public"
        : "/sign_quantidia/report_rejected";
    const params = publicToken
        ? { sign_request_id: signRequestId, access_token: publicToken, reason }
        : { sign_request_id: signRequestId, reason };
    try {
        await rpc(url, params);
        console.log("[quantidia] rechazo reportado correctamente");
    } catch (err) {
        console.error("[quantidia] error al reportar rechazo:", err);
    }
}

export { openQuantidiaSign, loadSdk };
