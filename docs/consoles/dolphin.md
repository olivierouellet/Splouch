# Colorado Time Systems — Dolphin

> **Not tested on real hardware.** Implemented from the Dolphin user instructions (F912, "TCP/IP Streaming"). Adjustments may be needed once validated against a live system.

The Dolphin wireless stopwatches report to a base plugged into a PC running the **Dolphin software**. Splouch does not talk to the base: it connects to the Dolphin software over the network, through its TCP/IP streaming API — the same way a meet manager does.

What the board gets:

- the current event and heat, whenever Dolphin changes them;
- each lane's final time, ranked into places, and DQs — **once the race is over and reset** on the watches or starter.

What it does not get: a running clock or live touches. Dolphin sends nothing while a race is running.

## Hardware

| Item | Purpose |
| --- | --- |
| PC running the Dolphin software | Receives the watches through the Dolphin base (USB) |
| Network | The Dolphin PC and the Splouch server on the same network |

No serial adapter is needed.

## Set up Dolphin

1. In Dolphin, open **Settings** (F4).
2. In **TCP/IP Streaming**, click the **TCP/IP** label to turn it on. Note the port (13382 by default).
3. Make sure the PC's firewall lets incoming connections reach that port.

## Settings

In **Settings → Timing**:

- **Timing Equipment:** `Dolphin (Colorado Timing System)`
- **Console host:** the IP address of the Dolphin PC

Options:

- **Dolphin TCP/IP port** — the port shown in Dolphin's TCP/IP Streaming box.
- **Results hold before the next heat** — Dolphin can move to the next heat as soon as a race is saved; the race's times stay on the board this long first (10 s by default).

Full protocol reference: [`console_decoders/dolphin_tcp.md`](../../server/console_decoders/dolphin_tcp.md)

## Capturing at the pool

Follow the [general procedure](../admin.md#capturing-at-the-pool).

1. **Dolphin:** TCP/IP Streaming on; note the port and the PC's IP address. Allow it through the PC's firewall.
2. **Network:** the Pi and the Dolphin PC on the same network.
3. **Splouch:** `Dolphin` → **Console host** = the PC's IP; **Dolphin TCP/IP port** = Dolphin's port.
4. **Check:** *Connected: host:port*; Dolphin's *Connections* count goes to 1. The Serial Monitor shows a `CurrentEventAndHeat,…` line right away, then nothing until a race is reset.
5. **Record:** `.serial` or `.raw` — the stream is text either way. Run a heat with the watches, reset, and let Dolphin move to the next heat. As a backup, on the Pi: `sudo tcpdump -i eth0 -w dolphin.pcap tcp port 13382`.
