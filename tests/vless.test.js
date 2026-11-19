"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const yaml = require("js-yaml");
const { parseVlessLink } = require("../payload/vless");
const { normalizeSubscriptionText } = require("../payload/subscriptionFormat");

const UUID = "5783a3e7-e373-51cd-8642-c83782b807c5";

test("VLESS link with REALITY is parsed into a Clash proxy", () => {
  const proxy = parseVlessLink(
    `vless://${UUID}@vpn.example.org:443?type=tcp&security=reality&pbk=public-key&sid=0123456789ab&fp=firefox&sni=www.example.org&flow=xtls-rprx-vision#Node`
  );
  assert.equal(proxy.type, "vless");
  assert.equal(proxy.server, "vpn.example.org");
  assert.equal(proxy.port, 443);
  assert.equal(proxy.uuid, UUID);
  assert.equal(proxy.flow, "xtls-rprx-vision");
  assert.equal(proxy.tls, true);
  assert.equal(proxy.servername, "www.example.org");
  assert.equal(proxy["client-fingerprint"], "firefox");
  assert.equal(proxy["reality-opts"]["public-key"], "public-key");
  assert.equal(proxy["reality-opts"]["short-id"], "0123456789ab");
});

test("VLESS link without TLS stays plain", () => {
  const proxy = parseVlessLink(`vless://${UUID}@plain.example.org:80?type=tcp#Node`);
  assert.equal(proxy.tls, undefined);
  assert.equal(proxy.servername, undefined);
  assert.equal(proxy.port, 80);
});

test("WebSocket transport keeps path and host header", () => {
  const proxy = parseVlessLink(
    `vless://${UUID}@ws.example.org:443?type=ws&security=tls&path=%2Fpath&host=cdn.example.org&sni=ws.example.org#Node`
  );
  assert.equal(proxy.network, "ws");
  assert.equal(proxy["ws-opts"].path, "/path");
  assert.equal(proxy["ws-opts"].headers.Host, "cdn.example.org");
});

test("gRPC transport keeps the service name", () => {
  const proxy = parseVlessLink(`vless://${UUID}@grpc.example.org:443?type=grpc&serviceName=mygrpc&security=tls#Node`);
  assert.equal(proxy.network, "grpc");
  assert.equal(proxy["grpc-opts"]["grpc-service-name"], "mygrpc");
});

test("REALITY without a public key is rejected", () => {
  assert.equal(parseVlessLink(`vless://${UUID}@vpn.example.org:443?type=tcp&security=reality#Node`), null);
});

test("Unknown transport is rejected instead of silently dropped", () => {
  assert.equal(parseVlessLink(`vless://${UUID}@vpn.example.org:443?type=quic#Node`), null);
});

test("Non-VLESS links are rejected", () => {
  assert.equal(parseVlessLink("https://example.org/sub"), null);
});

test("Base64 list of VLESS links converts to a Clash profile", () => {
  const link = `vless://${UUID}@vpn.example.org:443?type=tcp&security=reality&pbk=k&sid=01&fp=firefox&sni=vpn.example.org#Node`;
  const result = normalizeSubscriptionText(Buffer.from(link).toString("base64"));
  assert.equal(result.needsClash, false);
  assert.match(result.format, /VLESS list/);
  const profile = yaml.load(result.text);
  assert.equal(profile.proxies.length, 1);
  assert.equal(profile.proxies[0].type, "vless");
  assert.equal(profile.proxies[0].server, "vpn.example.org");
  assert.equal(profile["proxy-groups"][0].proxies.length, 1);
});
