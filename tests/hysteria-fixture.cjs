"use strict";

// Structure of the supplied Hysteria2 node, with synthetic addresses/passwords.
module.exports = {
  remarks: "Independent Hysteria2",
  outbounds: [{
    protocol: "hysteria",
    tag: "proxy",
    streamSettings: {
      finalmask: { udp: [{ settings: { password: "test-salamander-key" }, type: "salamander" }] },
      hysteriaSettings: { auth: "test-hysteria-auth", udpIdleTimeout: 60, version: 2 },
      network: "hysteria",
      security: "tls",
      tlsSettings: { alpn: ["h3", "h2", "http/1.1"], fingerprint: "chrome", serverName: "hy.example.org" },
    },
    settings: { address: "hy.example.org", port: 54164, version: 2 },
  }, { protocol: "freedom", tag: "direct" }],
};
