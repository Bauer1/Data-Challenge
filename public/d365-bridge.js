/**
 * Freedom Broker CQA — Microsoft Dynamics 365 Web Resource Adapter Bridge
 * Provides seamless compatibility when the HTML/CSS/JS application is embedded
 * inside a Dynamics 365 Customer Service Workspace form (HTML Web Resource)
 * or run standalone during the hackathon demonstration.
 */
(function (global) {
  "use strict";

  const D365Bridge = {
    isEmbeddedInD365: false,
    shellPreviewActive: false,
    entityId: null,
    entityName: "msdyn_ocliveworkitem",
    customParams: {},

    init: function () {
      // 1. Check if hosted inside a real Dynamics 365 parent frame
      try {
        if (
          global.Xrm ||
          (global.parent && global.parent !== global && global.parent.Xrm)
        ) {
          this.isEmbeddedInD365 = true;
        }
      } catch (_) {
        // Cross-origin frame
      }

      // 2. Parse standard Dynamics 365 Web Resource ?data= query parameter
      const searchParams = new URLSearchParams(global.location.search);
      if (searchParams.get("d365") === "1") {
        this.shellPreviewActive = true;
      }
      const dataParam = searchParams.get("data") || searchParams.get("Data");
      if (dataParam) {
        try {
          const decoded = decodeURIComponent(dataParam);
          decoded.split("&").forEach((pair) => {
            const [k, v] = pair.split("=");
            if (k) this.customParams[k.trim()] = (v || "").trim();
          });
        } catch (_) {}
      }

      if (searchParams.get("conversation_id")) {
        this.customParams.conversation_id = searchParams.get("conversation_id");
      }
      if (searchParams.get("id")) {
        this.entityId = searchParams.get("id");
      }

      return this;
    },

    getInitialConversationId: function () {
      return this.customParams.conversation_id || null;
    },

    notifyParentD365: function (eventType, payload) {
      const eventEnvelope = {
        source: "FreedomBroker.CQA.WebResource",
        eventType: eventType,
        timestamp: new Date().toISOString(),
        payload: payload,
      };
      try {
        if (global.parent && global.parent !== global) {
          global.parent.postMessage(eventEnvelope, "*");
        }
      } catch (_) {}
      global.dispatchEvent(new CustomEvent("cqa:d365-event", { detail: eventEnvelope }));
    },
  };

  global.D365Bridge = D365Bridge.init();
})(window);
