#!/usr/bin/env node
// Licensed under the PolyForm Noncommercial License 1.0.0.
// The mirror as a Matter device: the screen is an on/off light. It only talks to MirrorDash's /api/v1,
// with its own token. Paired over the network (the mirror is already on Wi-Fi), no Bluetooth.
// Run by mirrordash-matter.service, which the core starts and stops (Settings > Matter, ARCHITECTURE #35):
//   MIRRORDASH_TOKEN=<token> MATTER_PASSCODE=<n> MATTER_DISCRIMINATOR=<n> node index.mjs
//   (MIRRORDASH_URL, MATTER_UNIQUE_ID, MATTER_STORAGE_PATH optional)
import { renameSync, writeFileSync } from "node:fs";
import { Endpoint, Environment, ServerNode } from "@matter/main";
import { OnOffLightDevice } from "@matter/main/devices/on-off-light";
import { VendorId } from "@matter/main/types";

const URL_BASE = (process.env.MIRRORDASH_URL ?? "http://127.0.0.1:8000") + "/api/v1";
const TOKEN = process.env.MIRRORDASH_TOKEN;
if (!TOKEN) throw new Error("MIRRORDASH_TOKEN is missing: turn Matter on under Settings > Matter");
// One per mirror, made by the core (a new one when the pairing is reset): two mirrors aren't one device
const UNIQUE_ID = process.env.MATTER_UNIQUE_ID ?? "mirrordash-1";
const STORAGE = process.env.MATTER_STORAGE_PATH ?? "/storage/mirrordash/data/matter";
Environment.default.vars.set("storage.path", STORAGE);

async function call(path, body) {
    const r = await fetch(URL_BASE + path, {
        method: body ? "POST" : "GET",
        headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json" },
        body: body && JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`${path}: ${r.status} ${await r.text()}`);
    return r.json();
}

let status = await call("/status");
const node = await ServerNode.create({
    id: "mirrordash",
    network: { port: 5540 },
    // Made by the core when Matter is first turned on, one pair per mirror
    commissioning: { passcode: Number(process.env.MATTER_PASSCODE ?? 20202021),
                     discriminator: Number(process.env.MATTER_DISCRIMINATOR ?? 3840) },
    productDescription: { name: "MirrorDash", deviceType: OnOffLightDevice.deviceType },
    basicInformation: {
        vendorName: "MirrorDash", vendorId: VendorId(0xfff1),  // a CSA test vendor: the device is uncertified
        productName: "MirrorDash", productLabel: "MirrorDash", nodeLabel: "MirrorDash", productId: 0x8000,
        serialNumber: UNIQUE_ID, uniqueId: UNIQUE_ID,
    },
});
const screen = new Endpoint(OnOffLightDevice, { id: "screen", onOff: { onOff: status.screen_on } });
await node.add(screen);

// A change from a home app goes to the mirror; one that only mirrors the mirror's own state doesn't go back
let lastCommand = 0, sending = 0;  // a poll that started before this answers with the state from before the command
screen.events.onOff.onOff$Changed.on(on => {
    if (on === status.screen_on) return;
    lastCommand = Date.now();
    sending++;
    call("/screen", { state: on ? "on" : "off" }).catch(console.error).finally(() => { sending--; lastCommand = Date.now(); });
});

// ponytail: polls the local API every 2 s, so a change on the mirror reaches the home apps up to 2 s late;
// a push from the core's event bus would make it instant. unref: on SIGTERM matter.js closes the node, and the
// poll mustn't keep the process alive after it (systemctl stop would wait 90 s).
setInterval(async () => {
    try {
        const started = Date.now();
        const polled = await call("/status");
        if (sending || started <= lastCommand) return;
        status = polled;
        await screen.set({ onOff: { onOff: status.screen_on } });
    } catch (e) {
        console.error(e.message);
    }
}, 2000).unref();

// The admin page's Matter card shows these (features/matter)
function writePairing() {
    const { qrPairingCode, manualPairingCode } = node.state.commissioning.pairingCodes;
    const file = `${STORAGE}/pairing.json`;
    writeFileSync(file + ".tmp", JSON.stringify({ qr: qrPairingCode, manual: manualPairingCode,
                                                  commissioned: node.lifecycle.isCommissioned }));
    renameSync(file + ".tmp", file);
}
node.lifecycle.commissioned.on(writePairing);
node.lifecycle.decommissioned.on(writePairing);

await node.start();
writePairing();
console.log(`Matter is up; manual pairing code ${node.state.commissioning.pairingCodes.manualPairingCode}`);
