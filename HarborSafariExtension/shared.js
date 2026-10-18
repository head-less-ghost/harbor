// Shared by the background script, the content script and the popup.

// Safari can inject this file again when website access changes.
var DEFAULT_SETTINGS = { interceptEnabled: true, disabledSites: [] };

// Harbor takes http(s) URLs through harbor://download?url=… and registers magnet: itself.
function harborURL(url) {
    return url.startsWith("magnet:") ? url : "harbor://download?url=" + encodeURIComponent(url);
}
