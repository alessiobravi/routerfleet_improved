// Passkeys (WebAuthn) for RouterFleet Improved.
//
// No library: the server sends the options as JSON with binary values in
// base64url; this turns them into what navigator.credentials expects, and the
// credential the browser returns back into JSON. Every request carries the
// CSRF token.
(function () {
    'use strict';

    function toBuffer(value) {
        var base64 = value.replace(/-/g, '+').replace(/_/g, '/');
        base64 += '='.repeat((4 - base64.length % 4) % 4);
        var binary = atob(base64);
        var bytes = new Uint8Array(binary.length);
        for (var i = 0; i < binary.length; i++) {
            bytes[i] = binary.charCodeAt(i);
        }
        return bytes.buffer;
    }

    function toBase64url(buffer) {
        var bytes = new Uint8Array(buffer);
        var binary = '';
        for (var i = 0; i < bytes.length; i++) {
            binary += String.fromCharCode(bytes[i]);
        }
        return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    }

    function csrfToken() {
        var field = document.querySelector('input[name="csrfmiddlewaretoken"]');
        return field ? field.value : '';
    }

    function post(url, body) {
        return fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {'X-CSRFToken': csrfToken(), 'Content-Type': 'application/json'},
            body: JSON.stringify(body || {})
        }).then(function (response) {
            return response.json().catch(function () { return {}; }).then(function (data) {
                if (!response.ok) {
                    throw new Error(data.error || 'The request failed.');
                }
                return data;
            });
        });
    }

    function supported() {
        return !!(window.PublicKeyCredential && navigator.credentials && window.isSecureContext);
    }

    function register(name) {
        return post('/user/settings/passkeys/options/').then(function (options) {
            options.challenge = toBuffer(options.challenge);
            options.user.id = toBuffer(options.user.id);
            (options.excludeCredentials || []).forEach(function (item) { item.id = toBuffer(item.id); });
            return navigator.credentials.create({publicKey: options});
        }).then(function (credential) {
            var response = credential.response;
            return post('/user/settings/passkeys/add/', {
                name: name,
                credential: {
                    id: credential.id,
                    rawId: toBase64url(credential.rawId),
                    type: credential.type,
                    response: {
                        clientDataJSON: toBase64url(response.clientDataJSON),
                        attestationObject: toBase64url(response.attestationObject),
                        transports: response.getTransports ? response.getTransports() : []
                    },
                    clientExtensionResults: credential.getClientExtensionResults ? credential.getClientExtensionResults() : {},
                    authenticatorAttachment: credential.authenticatorAttachment || null
                }
            });
        });
    }

    function signIn() {
        return post('/accounts/passkey/options/').then(function (options) {
            options.challenge = toBuffer(options.challenge);
            (options.allowCredentials || []).forEach(function (item) { item.id = toBuffer(item.id); });
            return navigator.credentials.get({publicKey: options});
        }).then(function (credential) {
            var response = credential.response;
            return post('/accounts/passkey/login/', {
                id: credential.id,
                rawId: toBase64url(credential.rawId),
                type: credential.type,
                response: {
                    clientDataJSON: toBase64url(response.clientDataJSON),
                    authenticatorData: toBase64url(response.authenticatorData),
                    signature: toBase64url(response.signature),
                    userHandle: response.userHandle ? toBase64url(response.userHandle) : null
                },
                clientExtensionResults: credential.getClientExtensionResults ? credential.getClientExtensionResults() : {},
                authenticatorAttachment: credential.authenticatorAttachment || null
            });
        });
    }

    window.RouterFleetPasskeys = {supported: supported, register: register, signIn: signIn};
})();
