/* CLI Bridge for Zotero — bootstrap plugin
 *
 * Registers a POST /cli-bridge/eval endpoint on Zotero's built-in HTTP server
 * so that external CLI tools can execute privileged JavaScript without GUI
 * automation (AppleScript / xdotool).
 *
 * Works on macOS, Windows, and Linux — any platform that runs Zotero 7+.
 */

var cliBridgeEndpoint;

function startup({ id, version, rootURI }) {
  cliBridgeEndpoint = function () {};
  cliBridgeEndpoint.prototype = {
    supportedMethods: ["POST"],
    supportedDataTypes: ["text/plain"],
    permitBookmarklet: false,
    init: async function (options) {
      try {
        var result = await eval("(async () => {" + options.data + "})()");
        return [200, "application/json", JSON.stringify(result)];
      } catch (e) {
        return [500, "application/json", JSON.stringify({ error: e.message })];
      }
    },
  };
  Zotero.Server.Endpoints["/cli-bridge/eval"] = cliBridgeEndpoint;
  Zotero.debug("[CLI Bridge] /cli-bridge/eval endpoint registered");
}

function shutdown() {
  delete Zotero.Server.Endpoints["/cli-bridge/eval"];
  cliBridgeEndpoint = null;
  Zotero.debug("[CLI Bridge] /cli-bridge/eval endpoint removed");
}

function install() {}
function uninstall() {}
