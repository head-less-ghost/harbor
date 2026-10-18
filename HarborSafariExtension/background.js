const DOWNLOAD_MENU_ID = "download-with-harbor";
const DOWNLOAD_MENU = { title: "Download with Harbor", contexts: ["link"] };

// A background wake must not delete a menu that Safari is already displaying.
// Update the existing item, or create it if Safari cleared it on restart.
const menuReady = browser.contextMenus.update(DOWNLOAD_MENU_ID, DOWNLOAD_MENU).catch(() => new Promise((resolve, reject) => {
    browser.contextMenus.create({ id: DOWNLOAD_MENU_ID, ...DOWNLOAD_MENU }, () => {
        const error = browser.runtime.lastError;
        if (error) reject(new Error(error.message));
        else resolve();
    });
}));
menuReady.catch(console.error);

// Register at script load so Safari can wake the non-persistent background at startup.
// TODO: Verify menu restoration after restart and extension toggles in Safari 27.0.1.
browser.runtime.onStartup.addListener(() => {});

browser.contextMenus.onClicked.addListener((info, tab) => {
    browser.tabs.update(tab.id, { url: harborURL(info.linkUrl) });
});

browser.runtime.onMessage.addListener((message, sender) => {
    if (message.type !== "get-interception-site") return;
    const pageURL = sender.tab?.url ? new URL(sender.tab.url) : null;
    const isWebPage = pageURL?.protocol === "http:" || pageURL?.protocol === "https:";
    return Promise.resolve(isWebPage ? pageURL.hostname : null);
});
