#!/usr/bin/env node
// Licensed under the PolyForm Noncommercial License 1.0.0.
// The mirror as a Matter device: the screen is a dimmable light (on/off = screen, level = brightness).
// It only talks to MirrorDash's /api/v1, with its own token. Phase 0 spike: pairing with Apple Home and
// Google Home without Bluetooth (the mirror is already on Wi-Fi, so it is found over the network).
//   MIRRORDASH_TOKEN=<token> node index.mjs        (MIRRORDASH_URL, MATTER_STORAGE_PATH optional)
import { Endpoint, Environment, ServerNode } from "@matter/main";
import { DimmableLightDevice } from "@matter/main/devices/dimmable-light";
import { QrCode, VendorId } from "@matter/main/types";

const URL_BASE = (process.env.MIRRORDASH_URL ?? "http://127.0.0.1:8000") + "/api/v1";
const TOKEN = process.env.MIRRORDASH_TOKEN;
if (!TOKEN) throw new Error("MIRRORDASH_TOKEN is missing: create one under Settings > API");
Environment.default.vars.set("storage.path", process.env.MATTER_STORAGE_PATH ?? "/storage/mirrordash/matter");

async function call(path, body) {
    const r = await fetch(URL_BASE + path, {
        method: body ? "POST" : "GET",
        headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json" },
        body: body && JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`${path}: ${r.status} ${await r.text()}`);
    return r.json();
}

// Matter levels are 1-254, the mirror's brightness 10-100
const toLevel = brightness => Math.round(brightness * 254 / 100);
const toBrightness = level => Math.min(100, Math.max(10, Math.round(level * 100 / 254)));

let status = await call("/status");
const node = await ServerNode.create({
    id: "mirrordash",
    network: { port: 5540 },
    commissioning: { passcode: 20202021, discriminator: 3840 },  // ponytail: fixed test codes; a random pair per mirror before release
    productDescription: { name: "MirrorDash", deviceType: DimmableLightDevice.deviceType },
    basicInformation: {
        vendorName: "MirrorDash", vendorId: VendorId(0xfff1),  // a CSA test vendor: the device is uncertified
        productName: "MirrorDash", productLabel: "MirrorDash", nodeLabel: "MirrorDash", productId: 0x8000,
        serialNumber: "mirrordash-1", uniqueId: "mirrordash-1",
    },
});
const screen = new Endpoint(DimmableLightDevice, {
    id: "screen",
    onOff: { onOff: status.screen_on },
    levelControl: { currentLevel: toLevel(status.brightness), minLevel: toLevel(10), maxLevel: 254 },
});
await node.add(screen);

// A change from a home app goes to the mirror; one that only mirrors the mirror's own state doesn't go back
screen.events.onOff.onOff$Changed.on(on => {
    if (on !== status.screen_on) call("/screen", { state: on ? "on" : "off" }).catch(console.error);
});
screen.events.levelControl.currentLevel$Changed.on(level => {
    if (level !== null && toBrightness(level) !== status.brightness)
        call("/brightness", { value: toBrightness(level) }).catch(console.error);
});

// ponytail: polls the local API every 2 s, so a change on the mirror reaches the home apps up to 2 s late;
// a push from the core's event bus would make it instant.
setInterval(async () => {
    try {
        status = await call("/status");
        await screen.set({ onOff: { onOff: status.screen_on }, levelControl: { currentLevel: toLevel(status.brightness) } });
    } catch (e) {
        console.error(e.message);
    }
}, 2000);

await node.start();
if (!node.lifecycle.isCommissioned) {
    const { qrPairingCode, manualPairingCode } = node.state.commissioning.pairingCodes;
    console.log(QrCode.get(qrPairingCode));
    console.log(`Manual pairing code: ${manualPairingCode}`);
}
