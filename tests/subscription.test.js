"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const yaml = require("js-yaml");
const { normalizeSubscriptionText: normalize } = require("../payload/subscriptionFormat");
const vless = require("./xray-fixture.cjs");
const hysteria = require("./hysteria-fixture.cjs");

test("Clash configuration stays unchanged", () => {
  const text = "proxies: []\nproxy-groups: []\nrules: []\n";
  assert.equal(normalize(text).text, text);
});

test("Xray VLESS direct settings and REALITY are preserved", () => {
  const result = normalize(JSON.stringify(vless));
  assert.equal(result.needsClash, false);
  const proxy = yaml.load(result.text).proxies[0];
  assert.equal(proxy.type, "vless");
  assert.equal(proxy.server, "vpn.example.org");
  assert.equal(proxy.port, 443);
  assert.equal(proxy.uuid, vless[0].outbounds[0].settings.id);
  assert.equal(proxy.flow, "xtls-rprx-vision");
  assert.equal(proxy["reality-opts"]["public-key"], vless[0].outbounds[0].streamSettings.realitySettings.publicKey);
});

test("Hysteria2 auth and Salamander secrets stay separate", () => {
  const result = normalize(JSON.stringify([hysteria]));
  assert.equal(result.needsClash, false);
  const proxy = yaml.load(result.text).proxies[0];
  assert.equal(proxy.type, "hysteria2");
  assert.equal(proxy.password, "test-hysteria-auth");
  assert.equal(proxy.obfs, "salamander");
  assert.equal(proxy["obfs-password"], "test-salamander-key");
  assert.equal(proxy.sni, "hy.example.org");
});

test("Mixed and base64 profiles retain all nodes", () => {
  const input = JSON.stringify([...vless, hysteria]);
  const result = normalize(Buffer.from(input).toString("base64"));
  assert.equal(result.needsClash, false);
  assert.deepEqual(yaml.load(result.text).proxies.map(p => p.type), ["vless", "hysteria2"]);
});

test("Unsupported node rejects the list instead of dropping a server", () => {
  const unknown = { outbounds: [{protocol:"future-protocol",settings:{address:"invalid.example",port:443}}] };
  const result = normalize(JSON.stringify([...vless, unknown]));
  assert.equal(result.needsClash, true);
  assert.match(result.reason, /future-protocol/);
});
