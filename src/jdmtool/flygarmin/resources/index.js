
document.addEventListener("DOMContentLoaded", function() {
    let params = new URLSearchParams({
        "service": window.location,
        "source": window.location,
        "gauthHost": window.location,
        "locale": "en_US",
        "id": "gauth-widget",
        "cssUrl": "https://static.garmin.com/apps/fly/files/desktop/flygarmin-desktop-gauth-v3.css",
        "reauth": "false",
        "clientId": "FLY_GARMIN_DESKTOP",
        "rememberMeShown": "false",
        "rememberMeChecked": "false",
        "createAccountShown": "true",
        "openCreateAccount": "false",
        "displayNameShown": "false",
        "consumeServiceTicket": "false",
        "initialFocus": "true",
        "embedWidget": "true",
        "socialEnabled": "false",
        "generateExtraServiceTicket": "false",
        "generateTwoExtraServiceTickets": "false",
        "generateNoServiceTicket": "false",
        "globalOptInShown": "false",
        "globalOptInChecked": "false",
        "mobile": "false",
        "connectLegalTerms": "false",
        "showTermsOfUse": "false",
        "showPrivacyPolicy": "false",
        "showConnectLegalAge": "false",
        "locationPromptShown": "false",
        "showPassword": "true",
        "useCustomHeader": "false",
        "mfaRequired": "false",
        "performMFACheck": "false",
        "permanentMFA": "false",
        "rememberMyBrowserShown": "false",
        "rememberMyBrowserChecked": "false",
    });

    let iframe = document.getElementById("sso");
    let status = document.getElementById("status");

    iframe.src = "https://sso.garmin.com/sso/signin?" + params;

    window.addEventListener("message", async function(evt) {
        if (evt.source === iframe.contentWindow) {
            iframe.style.display = "none";
            status.textContent = "Received ticket; sending to jdmtool...";

            console.log("Received message:", evt.data);

            try {
                await fetch("/login", {
                    method: "POSt",
                    headers: {
                        "Content-Type": "application/json",
                    },
                    body: evt.data,
                });
                status.textContent = "Done! You may close this window.";
            } catch (e) {
                status.textContent = e;
            }
        }
    });
});
