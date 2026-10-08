"use strict";

const yaml = require("js-yaml");

function profileFromProxies(proxies) {
  const names = new Set();
  const reserved = new Set(["DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE", "GLOBAL"]);
  const normalized = proxies.map((proxy, index) => {
    const node = { ...proxy };
    const base = String(node.name || node.server || "Сервер " + (index + 1));
    let name = base;
    let suffix = 2;
    while (names.has(name) || reserved.has(name)) name = base + " (" + suffix++ + ")";
    node.name = name;
    names.add(name);
    return node;
  });
  let groupName = "Выбор сервера";
  while (names.has(groupName)) groupName += " VPN";
  return yaml.dump({
    proxies: normalized,
    "proxy-groups": [{ name: groupName, type: "select", proxies: [...names] }],
    rules: ["MATCH," + groupName],
  }, { lineWidth: -1 });
}

function isClashProxy(value) {
  return value && typeof value === "object" && !Array.isArray(value) &&
    typeof value.type === "string" && typeof value.server === "string" &&
    Number(value.port) > 0 && Number(value.port) < 65536;
}

function unsupported(reason) {
  return { conversionError: reason };
}

function safeType(value) {
  return String(value || "не указан").slice(0, 40).replace(/[^a-zA-Z0-9 _+-]/g, "?");
}

function xrayVlessProxy(outbound, profileName, parseVless) {
  if (!outbound || outbound.protocol !== "vless") return unsupported("протокол " + safeType(outbound && outbound.protocol));
  const settings = outbound.settings || {};
  let server = settings;
  let user = settings;
  if (settings.vnext) {
    if (!Array.isArray(settings.vnext) || settings.vnext.length !== 1) return unsupported("ожидается один сервер vnext");
    server = settings.vnext[0];
    if (!Array.isArray(server.users) || server.users.length !== 1) return unsupported("ожидается один пользователь vnext");
    user = server.users[0];
  }
  if (!server.address || !Number.isInteger(Number(server.port)) ||
      Number(server.port) <= 0 || Number(server.port) >= 65536 || !user.id) return unsupported("не хватает адреса, корректного порта или id");
  if (user.encryption && user.encryption !== "none") return unsupported("VLESS Encryption");
  const stream = outbound.streamSettings || {};
  if (outbound.proxySettings && outbound.proxySettings.tag || stream.sockopt && stream.sockopt.dialerProxy ||
      stream.finalmask && Object.keys(stream.finalmask).length ||
      stream.finalMask && Object.keys(stream.finalMask).length ||
      user.reverse && Object.keys(user.reverse).length) return unsupported("цепочка прокси, FinalMask или reverse");
  if (outbound.mux && outbound.mux.enabled) return unsupported("включён Xray Mux");
  const proxy = {
    name: profileName || server.address,
    type: "vless",
    server: String(server.address),
    port: Number(server.port),
    uuid: String(user.id),
    udp: true,
    "packet-encoding": "xudp",
  };
  if (user.flow) proxy.flow = user.flow;
  const security = stream.security || "none";
  if (security === "reality") {
    const reality = stream.realitySettings || {};
    if (!reality.publicKey) return unsupported("нет публичного ключа REALITY");
    if (reality.mldsa65Verify) return unsupported("обязательная проверка mldsa65Verify");
    proxy.tls = true;
    proxy.servername = reality.serverName || server.address;
    if (reality.fingerprint) proxy["client-fingerprint"] = reality.fingerprint;
    proxy["reality-opts"] = {
      "public-key": reality.publicKey,
      "short-id": reality.shortId || "",
    };
  } else if (security === "tls") {
    const tls = stream.tlsSettings || {};
    proxy.tls = true;
    proxy.servername = tls.serverName || server.address;
    if (tls.fingerprint) proxy["client-fingerprint"] = tls.fingerprint;
    if (tls.alpn) proxy.alpn = tls.alpn;
    if (tls.allowInsecure !== undefined) proxy["skip-cert-verify"] = !!tls.allowInsecure;
  } else if (security !== "none") return unsupported("защита " + safeType(security));
  const network = stream.network || stream.method || "tcp";
  if (network === "tcp" || network === "raw") {
    const tcp = stream.tcpSettings || stream.rawSettings || {};
    if (tcp.header && tcp.header.type && tcp.header.type !== "none") return unsupported("TCP header " + safeType(tcp.header.type));
  } else if (network === "ws" || network === "websocket") {
    const ws = stream.wsSettings || {};
    if (ws.maxEarlyData || ws.earlyDataHeaderName) return unsupported("WebSocket Early Data");
    proxy.network = "ws";
    proxy["ws-opts"] = { path: ws.path || "/" };
    if (ws.headers) proxy["ws-opts"].headers = ws.headers;
    if (ws.host) proxy["ws-opts"].headers = { ...ws.headers, Host: ws.host };
  } else if (network === "grpc") {
    const grpc = stream.grpcSettings || {};
    if (grpc.multiMode) return unsupported("gRPC multiMode");
    proxy.network = "grpc";
    proxy["grpc-opts"] = { "grpc-service-name": grpc.serviceName || "" };
  } else if (network === "xhttp" || network === "splithttp") {
    if (!parseVless) return unsupported("транспорт XHTTP: отсутствует парсер");
    const xhttp = stream.xhttpSettings || stream.splithttpSettings || {};
    const host = String(server.address).replace(/^\[|\]$/g, "");
    const uri = new URL("vless://" + encodeURIComponent(user.id) + "@" + (host.includes(":") ? "[" + host + "]" : host) + ":" + server.port);
    uri.searchParams.set("type", "xhttp");
    uri.searchParams.set("path", xhttp.path || "/");
    uri.searchParams.set("mode", xhttp.mode || "auto");
    if (xhttp.host) uri.searchParams.set("host", xhttp.host);
    if (xhttp.extra) {
      if (xhttp.extra.downloadSettings) return unsupported("XHTTP downloadSettings");
      uri.searchParams.set("extra", JSON.stringify(xhttp.extra));
    }
    const parsed = parseVless(uri.href);
    if (!parsed) return unsupported("параметры XHTTP не распознаны");
    proxy.network = "xhttp";
    proxy["xhttp-opts"] = parsed["xhttp-opts"];
  } else return unsupported("транспорт " + safeType(network));
  return proxy;
}

function xrayHysteriaProxy(outbound, profileName) {
  const settings = outbound.settings || {};
  const stream = outbound.streamSettings || {};
  const transport = stream.hysteriaSettings || {};
  if (Number(settings.version) !== 2 || Number(transport.version) !== 2) {
    return unsupported("ожидается Hysteria версии 2");
  }
  if (!settings.address || !Number.isInteger(Number(settings.port)) ||
      Number(settings.port) <= 0 || Number(settings.port) >= 65536) {
    return unsupported("не хватает адреса или корректного порта Hysteria2");
  }
  if ((stream.method || stream.network) !== "hysteria" || stream.security !== "tls") {
    return unsupported("Hysteria2 требует транспорт hysteria и TLS");
  }
  if (outbound.proxySettings && outbound.proxySettings.tag || stream.sockopt && stream.sockopt.dialerProxy) {
    return unsupported("цепочка прокси Hysteria2");
  }
  const tls = stream.tlsSettings || {};
  const proxy = {
    name: profileName || settings.address,
    type: "hysteria2",
    server: String(settings.address),
    port: Number(settings.port),
    password: String(transport.auth == null ? "" : transport.auth),
    sni: tls.serverName || settings.address,
  };
  if (tls.allowInsecure !== undefined) proxy["skip-cert-verify"] = !!tls.allowInsecure;
  if (tls.alpn) proxy.alpn = tls.alpn;
  // TLS fingerprint in Xray is a client hello setting, not a certificate pin.
  const mask = stream.finalmask || stream.finalMask || {};
  if (mask.tcp && mask.tcp.length) return unsupported("TCP FinalMask для Hysteria2");
  if (mask.udp && mask.udp.length) {
    if (mask.udp.length !== 1 || mask.udp[0].type !== "salamander") return unsupported("UDP FinalMask для Hysteria2");
    const obfs = mask.udp[0].settings || {};
    if (obfs.packetSize) return unsupported("Hysteria2 Gecko packetSize");
    proxy.obfs = "salamander";
    proxy["obfs-password"] = String(obfs.password || "");
  }
  const quic = mask.quicParams || {};
  for (const [sourceKey, targetKey] of [["brutalUp", "up"], ["brutalDown", "down"]]) {
    if (quic[sourceKey] == null || quic[sourceKey] === 0 || quic[sourceKey] === "0") continue;
    const rate = /^([\d.]+)\s*([kmgt]?)(?:b(?:ps)?)?$/i.exec(String(quic[sourceKey]).trim());
    if (!rate) return unsupported("скорость Hysteria2 " + sourceKey);
    const multiplier = { "": 1, k: 1e3, m: 1e6, g: 1e9, t: 1e12 }[rate[2].toLowerCase()];
    proxy[targetKey] = String(Number(rate[1]) * multiplier / 1e6) + " Mbps";
  }
  if (quic.udpHop && quic.udpHop.ports) {
    proxy.ports = String(quic.udpHop.ports);
    if (quic.udpHop.interval) proxy["hop-interval"] = quic.udpHop.interval;
  }
  for (const [sourceKey, targetKey] of [
    ["initStreamReceiveWindow", "initial-stream-receive-window"],
    ["maxStreamReceiveWindow", "max-stream-receive-window"],
    ["initConnectionReceiveWindow", "initial-connection-receive-window"],
    ["maxConnectionReceiveWindow", "max-connection-receive-window"],
  ]) {
    if (quic[sourceKey]) proxy[targetKey] = quic[sourceKey];
  }
  return proxy;
}

function convertXray(profiles, text, parseVless) {
  const proxies = [];
  for (const [profileIndex, profile] of profiles.entries()) {
    if (!profile || !Array.isArray(profile.outbounds)) return { text, needsClash: true };
    let count = 0;
    for (const [outboundIndex, outbound] of profile.outbounds.entries()) {
      if (!outbound || typeof outbound !== "object") return { text, needsClash: true };
      if (["freedom", "blackhole", "dns"].includes(outbound.protocol)) continue;
      const proxy = outbound.protocol === "hysteria"
        ? xrayHysteriaProxy(outbound, profile.remarks || profile.name)
        : xrayVlessProxy(outbound, profile.remarks || profile.name, parseVless);
      if (proxy.conversionError) return { text, needsClash: true, reason: "Профиль " + (profileIndex + 1) + ", соединение " + (outboundIndex + 1) + ": " + proxy.conversionError };
      proxies.push(proxy);
      count++;
    }
    if (!count) return { text, needsClash: true };
  }
  return { text: profileFromProxies(proxies), needsClash: false, format: "Xray JSON" };
}

function inspectText(text, parseVless) {
  let document;
  try { document = yaml.load(text); } catch (_) { return null; }
  if (document && typeof document === "object" && !Array.isArray(document)) {
    if (document.outbounds) return convertXray([document], text, parseVless);
    if (document.outbound) return { text, needsClash: true };
    return { text, needsClash: false };
  }
  if (Array.isArray(document)) {
    if (document.length && document.every(value => value && Array.isArray(value.outbounds))) {
      return convertXray(document, text, parseVless);
    }
    if (document.length && document.every(isClashProxy)) {
      return { text: profileFromProxies(document), needsClash: false, format: "Clash proxy list" };
    }
    if (document.length && document.every(value => typeof value === "string")) {
      return convertLinks(document, text, parseVless);
    }
    return { text, needsClash: true };
  }
  if (typeof document === "string") {
    const lines = text.trim().split(/\r?\n/).map(line => line.trim()).filter(Boolean);
    if (lines.length && lines.every(line => /^[a-z][a-z0-9+.-]*:\/\//i.test(line))) {
      return convertLinks(lines, text, parseVless);
    }
  }
  return null;
}

function convertLinks(links, text, parseVless) {
  const proxies = links.map(link => /^vless:\/\//i.test(link) && parseVless ? parseVless(link) : null);
  if (proxies.every(Boolean)) {
    return { text: profileFromProxies(proxies), needsClash: false, format: "VLESS list" };
  }
  return { text, needsClash: true };
}

function normalizeSubscriptionText(value, parseVless) {
  const text = String(value || "").replace(/^\uFEFF/, "");
  const inspected = inspectText(text, parseVless);
  if (inspected) return inspected;
  const base64 = text.replace(/\s/g, "");
  if (base64.length >= 16 && /^[A-Za-z0-9+/_-]+={0,2}$/.test(base64) && base64.length % 4 !== 1) {
    const decoded = Buffer.from(base64, "base64").toString("utf8");
    if (!decoded.includes("\uFFFD")) {
      const result = inspectText(decoded, parseVless);
      if (result) return { ...result, format: "Base64 " + (result.format || "profile") };
    }
  }
  // Preserve malformed YAML and server messages for the existing validation.
  return { text, needsClash: false };
}

module.exports = { normalizeSubscriptionText };
