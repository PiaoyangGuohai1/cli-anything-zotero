/* CLI Bridge for Zotero — bootstrap plugin
 *
 * Registers a POST /cli-bridge/eval endpoint on Zotero's built-in HTTP server
 * so that external CLI tools can execute privileged JavaScript without GUI
 * automation (AppleScript / xdotool).
 *
 * Works on macOS, Windows, and Linux — any platform that runs Zotero 7+.
 */

var cliBridgeEndpoint;

function _serializeError(e) {
  var message = null;
  if (e == null) {
    message = "unknown error";
  } else if (typeof e === "string") {
    message = e;
  } else if (typeof e === "number" || typeof e === "boolean") {
    message = String(e);
  } else {
    message =
      (e && (e.message || e.name || (e.toString && e.toString()))) ||
      String(e);
  }
  // Avoid empty / undefined messages which used to collapse to error: "{}"
  if (!message || message === "undefined" || message === "[object Object]") {
    try {
      message = JSON.stringify(e);
    } catch (_jsonErr) {
      message = "unknown error";
    }
  }
  return {
    error: message,
    name: (e && e.name) || null,
    stack: (e && e.stack) ? String(e.stack).slice(0, 2000) : null,
    raw: String(e),
  };
}

function startup({ id, version, rootURI }) {
  cliBridgeEndpoint = function () {};
  cliBridgeEndpoint.prototype = {
    supportedMethods: ["POST"],
    supportedDataTypes: ["text/plain"],
    permitBookmarklet: false,
    init: async function (options) {
      try {
        var result = await eval("(async () => {" + options.data + "})()");
        // undefined is not valid JSON; normalize to null for clients
        if (typeof result === "undefined") {
          result = null;
        }
        return [200, "application/json", JSON.stringify(result)];
      } catch (e) {
        return [500, "application/json", JSON.stringify(_serializeError(e))];
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
