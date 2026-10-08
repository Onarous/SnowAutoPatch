"use strict";

// Matches the supplied Xray structure, using synthetic connection credentials.
module.exports = [{
  remarks: "Independent VLESS",
  dns: { queryStrategy: "UseIP", servers: [{ address: "8.8.8.8", skipFallback: false }], tag: "dns_out" },
  inbounds: [{ listen: "127.0.0.1", port: 10808, protocol: "socks", settings: { auth: "noauth", udp: true, userLevel: 8 } }],
  log: { loglevel: "warning" },
  outbounds: [{
    protocol: "vless",
    tag: "proxy",
    streamSettings: {
      network: "tcp",
      security: "reality",
      tcpSettings: { header: { type: "none" } },
      realitySettings: {
        fingerprint: "firefox",
        mldsa65Verify: "",
        publicKey: Buffer.alloc(32, 1).toString("base64url"),
        serverName: "www.example.org",
        shortId: "0123456789ab",
        show: false,
        spiderX: "/example",
      },
    },
    settings: {
      address: "vpn.example.org",
      port: 443,
      id: "5783a3e7-e373-51cd-8642-c83782b807c5",
      encryption: "none",
      flow: "xtls-rprx-vision",
    },
  }, { protocol: "freedom", tag: "direct" }, { protocol: "blackhole", tag: "block" }],
}];
