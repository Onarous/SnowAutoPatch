"use strict";

// Разбор VLESS-ссылки (vless://...) в Clash-прокси.
//
// Нужен для Linux-клиента: в его коде нет ни parseVlessLink, ни какого-либо
// упоминания vless, поэтому список VLESS-ссылок без этого парсера пришлось бы
// отбрасывать. На Windows аналогичный парсер есть в коде клиента, и там
// приоритет отдаётся ему (см. subscriptionFormat.js).
//
// Поддерживается подмножество, которое реально встречается в выгрузках панелей:
// tcp / ws / grpc / httpupgrade / xhttp, TLS и REALITY.

function bad(reason) {
  return null;
}

function num(value) {
  const n = Number(value);
  return Number.isInteger(n) && n > 0 && n < 65536 ? n : null;
}

function parseVlessLink(input) {
  let url;
  try {
    url = new URL(String(input).trim());
  } catch (_) {
    return bad("not a URL");
  }
  if (String(url.protocol).toLowerCase() !== "vless:") return bad("not a vless link");
  const uuid = decodeURIComponent(url.username || "");
  if (!uuid) return bad("no uuid");
  const host = url.hostname.replace(/^\[|\]$/g, "");
  const port = num(url.port);
  if (!host || !port) return bad("no address or port");

  const q = url.searchParams;
  const type = String(q.get("type") || "tcp").toLowerCase();
  const security = String(q.get("security") || "none").toLowerCase();
  const sni = q.get("sni") || q.get("peer") || "";
  const fp = q.get("fp") || "";
  const alpn = q.get("alpn") || "";
  const allowInsecure = q.get("allowInsecure") || q.get("insecure") || "";
  const flow = q.get("flow") || "";
  const pbk = q.get("pbk") || "";
  const sid = q.get("sid") || "";

  const proxy = {
    name: "",
    type: "vless",
    server: host,
    port,
    uuid,
    udp: true,
    "packet-encoding": "xudp",
  };
  if (flow) proxy.flow = flow;

  if (security === "reality") {
    if (!pbk) return bad("no REALITY public key");
    proxy.tls = true;
    proxy.servername = sni || host;
    if (fp) proxy["client-fingerprint"] = fp;
    proxy["reality-opts"] = { "public-key": pbk, "short-id": sid || "" };
  } else if (security === "tls") {
    proxy.tls = true;
    proxy.servername = sni || host;
    if (fp) proxy["client-fingerprint"] = fp;
    if (alpn) proxy.alpn = alpn.split(",").map((value) => value.trim()).filter(Boolean);
    if (allowInsecure !== "") proxy["skip-cert-verify"] = allowInsecure === "1" || allowInsecure === "true";
  } else if (security !== "none") {
    return bad("security " + security);
  }

  if (type === "tcp" || type === "raw") {
    const headerType = String(q.get("headerType") || "none").toLowerCase();
    if (headerType && headerType !== "none") return bad("TCP header " + headerType);
  } else if (type === "ws" || type === "websocket") {
    proxy.network = "ws";
    const path = q.get("path") || "/";
    const hostHeader = q.get("host") || "";
    proxy["ws-opts"] = { path };
    if (hostHeader) proxy["ws-opts"].headers = { Host: hostHeader };
  } else if (type === "httpupgrade") {
    proxy.network = "ws";
    proxy["ws-opts"] = { path: q.get("path") || "/", "v2ray-http-upgrade": true };
    const hostHeader = q.get("host") || "";
    if (hostHeader) proxy["ws-opts"].headers = { Host: hostHeader };
  } else if (type === "grpc") {
    proxy.network = "grpc";
    proxy["grpc-opts"] = { "grpc-service-name": q.get("serviceName") || q.get("path") || "" };
  } else if (type === "xhttp" || type === "splithttp") {
    proxy.network = "xhttp";
    proxy["xhttp-opts"] = {
      path: q.get("path") || "/",
      mode: q.get("mode") || "auto",
    };
    const hostHeader = q.get("host") || "";
    if (hostHeader) proxy["xhttp-opts"].host = hostHeader;
    const extra = q.get("extra");
    if (extra) {
      try {
        proxy["xhttp-opts"].extra = JSON.parse(extra);
      } catch (_) {
        return bad("XHTTP extra is not JSON");
      }
    }
  } else {
    return bad("transport " + type);
  }

  return proxy;
}

module.exports = { parseVlessLink };
