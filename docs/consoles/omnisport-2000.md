# Daktronics OmniSport 2000

> **Not tested on real hardware.** Implemented from the OmniSport 2000 manual (ED-13312), Daktronics' RTD field template (`OS2-Swimming.itf`) and a published capture of the RTD port. Adjustments may be needed once validated against a live console.

The OmniSport has several outputs. Pick the console, then the output you wired, in **Settings → Timing**.

| Output (second menu) | Port | Console menu (swimming, from Main Menu) | Decoder |
| --- | --- | --- | --- |
| **J2/J3 Scoreboard outputs set to OMEGA** | J2 or J3 (1/4" jack) | 4-4-1 Scoreboard Outputs → OMEGA, 1-1 Scoreboard ON | Swiss Timing scoreboard lines (no event/heat) |
| **J5 RTD — Results set to OMNI2000** | J5 RTD | 4-4-2 RTD Port → RTD, 4-4-3 Results Port → OMNI 2000 | Daktronics RTD, lines from offset 222 |
| **J5 RTD — Results set to OMNI6000** | J5 RTD | 4-4-2 RTD Port → RTD, 4-4-3 Results Port → OMNI 6000 | Daktronics RTD, lines from offset 200 |
| **J5 RTD port set to CTS** | J5 RTD | 4-4-2 RTD Port → CTS | Colorado legacy (unconfirmed) |
| **J6 Results port set to CTS** | J6 Results | 4-4-3 Results Port → CTS | Colorado legacy (unconfirmed) |
| **Ethernet — RTD over the network** | ETHERNET jack | 4-7-1 Network Enable → ON | Daktronics RTD over UDP broadcast (layout is an option) |

The Results Port setting (4-4-3) chooses the RTD item numbers sent on J5 too ("Selecting Omni 6000 transmits Omni 6000 RTD item numbers on the RTD output", ED-13312), hence two J5 entries.

The J5 RTD and Ethernet outputs are the recommended ones. J6 is the Results Port for meet management software: its default settings, OMNI 2000 and OMNI 6000, are bi-directional protocols Splouch does not decode; set to CTS it sends "the standard CTS scoreboard information" (ED-13312), which Splouch reads — Meet Manager must then read CTS on that port too.

**Not supported:** J2/J3 set to MULTIDROP or MULTILINE (Daktronics scoreboard protocols, not published), and J6 set to OMNI 2000 or OMNI 6000 (the bi-directional link with Meet Manager, not documented). Use J5 RTD or Ethernet instead. A `.raw` capture of one of these outputs is welcome: see [Capturing at the pool](#capturing-at-the-pool).

## Ethernet

Consoles shipped from June 2013 have an ETHERNET jack. With *Setup Network → Network Enable* ON, the console broadcasts RTD as UDP packets. Its standard IP address is 192.168.0.55–58 (OMNI1–4) and its standard RTD port 21000–21003 (OMNI1–4); 3002 is used for Galaxy message centers. Put the Splouch server on the same network and subnet; no cable or adapter is needed.

## Hardware

| Item | Purpose |
| --- | --- |
| DB9 cable | Connect to the J5 RTD Port (or J6 Results) directly on the console, not a wall plate |
| USB-to-RS232 adapter | DB9 → Pi USB |
| 20 mA current loop converter | OMEGA only: J2/J3 are current loop outputs (tip +, ring −), not RS-232 |

## Protocol

- RTD: RS-232 — 19 200 baud, 8-N-1, "Enhanced" protocol, 4096-byte buffer (ED-13312 appendix D), with the **Swimming** sport mode loaded.
- Ethernet: UDP broadcast on the console's RTD port number.
- CTS mode (J5 or J6): "the standard CTS scoreboard information". Serial settings aren't given in the manual; 9600 with even parity by default, parity is an option.
- OMEGA: serial settings undocumented — baud rate and format are options (9600, 7-E-1 by default). Lines are read in rank mode by default.

Full protocol reference: [`console_decoders/omnisport_2000_serial.md`](../../server/console_decoders/omnisport_2000_serial.md) and [`swiss_scoreboard_serial.md`](../../server/console_decoders/swiss_scoreboard_serial.md)

## Settings

In **Settings → Timing**:

- **Timing Equipment:** `OmniSport 2000 (Daktronics)`
- **Console output:** the output you wired (table above)
- **Serial port:** `/dev/ttyUSB0` (or whichever device the adapter appears as)

Options shown for the RTD outputs:

- **RTD offset** — must match the console's *RTD Offset* menu (0K, 5K, 10K, 15K). Leave at 0K unless the venue changed it.
- Ethernet only: **RTD network port** (the console's *RTD Port #*, 21000 by default) and **RTD layout** (lines at 222 for Results set to OMNI2000, at 200 for OMNI6000).

## Capturing at the pool

Follow the [general procedure](../admin.md#capturing-at-the-pool). Photograph the console's *Setup Comm Ports*, *Setup RTD/Matrix Scoreboard* (RTD Offset) and *Setup Network* menus.

### J2/J3 Scoreboard outputs set to OMEGA

1. **Console:** 4-4-1 Scoreboard Outputs → OMEGA. The scoreboard output must be on: 1-1 *SCBD IS ON*. While it is OFF, J2/J3 stay blank until the next race starts.
2. **Wire:** 1/4" plug into J2 or J3 (tip = current loop +, ring = current loop −, sleeve = GND) to a 20 mA current loop converter, then to the Pi. The other jack can still feed the pool's scoreboard.
3. **Splouch:** `OmniSport 2000` → `J2/J3 Scoreboard outputs set to OMEGA`.
4. **Check:** packets start `01 14 53 30 32 02 08` and end `04`. Garbage → **Baud rate** / **Serial format**; lanes and places swapped → **Scoreboard line order**.
5. **Record:** `.raw`.

### J5 RTD — Results set to OMNI2000 / OMNI6000

1. **Console:** RTD Port → RTD; Results Port → OMNI 2000 (or OMNI 6000 for the other entry); Swimming loaded.
2. **Wire:** DB9 to J5 RTD Port, USB-to-RS232 adapter.
3. **Splouch:** `OmniSport 2000` → the matching `J5 RTD — Results set to …`; **RTD offset** as on the console.
4. **Check:** *Connected*. Packets start `16 30 30 30 30 30 30 30 30 01 30 30 34 32 31` (SYN, `00000000`, SOH, `00421`); during a race the running time packet repeats several times a second. Running time but no lane times → check the Results setting matches the entry.
5. **Record:** `.raw`, then `.serial` once lane times show.

### J5 RTD port set to CTS / J6 Results port set to CTS

1. **Console:** RTD Port → CTS (J5) or Results Port → CTS (J6).
2. **Wire:** DB9 to that port, USB-to-RS232 adapter. On J6, Meet Manager loses that link unless it reads CTS too.
3. **Splouch:** the matching `… set to CTS` entry; **Serial parity** even by default.
4. **Check:** short packets, each a byte `80`–`FF` followed by a few below `80`, scrolling constantly. Garbage → **Serial parity** None.
5. **Record:** `.raw`.

### Ethernet — RTD over the network

1. **Console:** Setup Network → Network Enable ON; note the IP address and **RTD Port #**.
2. **Network:** the console on the Pi's **Ethernet** port (`eth0`), same subnet. Splouch only listens there, not on Wi-Fi. With the console's standard address (192.168.0.55–58) the Pi needs a 192.168.0.x address; broadcasts do not cross a router.
3. **Splouch:** `OmniSport 2000` → `Ethernet — RTD over the network`; **RTD network port** = the console's RTD Port #; **RTD layout** to match the Results setting.
4. **Check:** *Listening: UDP port 21000 on eth0 (192.168.0.x)*; *no IPv4 address on eth0* → the Ethernet cable or its address; the Serial Monitor shows the same `16 30 30 … 30 30 34 32 31` packets as J5.
5. **Record:** `.raw` (each datagram ends with a blank line). As a backup, on the Pi: `sudo tcpdump -i eth0 -w omnisport.pcap udp port 21000`.
