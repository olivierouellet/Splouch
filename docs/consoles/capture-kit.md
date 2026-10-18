# Capture kit — adapters, cables and taps

What to bring to a pool to wire Splouch to any console output it supports, and to
record it ([Capturing at the pool](../admin.md#capturing-at-the-pool)). Splouch only
**listens**, so sharing a port already in use takes a receive-only tap, not a real
splitter.

Example parts are real catalogue items, checked in October 2026; stock, prices and
part numbers change, so confirm on the distributor's site before ordering. Where no
Digi-Key or Mouser listing could be confirmed, the table says so.

## Adapters

| Item | Qty | Covers | Example parts |
| --- | --- | --- | --- |
| USB-to-RS232 adapter, FTDI chip, DB9 male | 2 | CTS legacy (System 5/6, Gen7 Legacy), OmniSport J5 / J6 | FTDI **CHIPI-X10** (Digi-Key 768-1138-ND, Mouser 895-CHIPI-X10) · FTDI **US232R-100** (1 m, Digi-Key) |
| USB-to-RS485/422 adapter, FTDI chip, with switchable 120 Ω termination | 2 | Gen7 RS-485 outputs, ARES SCB and GP, Quantum (listening on an RS-422 transmit pair works the same) | FTDI **USB-RS485-WE-1800-BT** (wire-ended, 1.8 m) · FTDI **USB-COM485-PLUS1** (DB9) · Advantech **BB-USOPTL4** (isolated, terminal block) — check each datasheet for termination; Waveshare **23949** (jumper 120 Ω, not on Digi-Key/Mouser) |
| Powered USB hub | 1 | Recording two outputs at once (e.g. J5 and Ethernet) | StarTech **ST4200USBM** (Digi-Key, 7–24 V input) |
| Small Ethernet switch + 3 patch cables | 1 | OmniSport Ethernet, Dolphin PC; keeps the console's display network connected | Advantech **EKI-2525** (5-port unmanaged; Digi-Key listing not confirmed) |

## Cables and taps

| Item | Qty | Use | Example parts |
| --- | --- | --- | --- |
| DB9 straight-through cable, male–female, 2–3 m | 2 | OmniSport J5 / J6 to the RS-232 adapter (Daktronics uses a straight-through cable for J6) | Assmann **AK131-2** (2 m, Digi-Key AE1379-ND) · Tripp Lite **P520-006** (1.8 m) |
| DB9 screw-terminal breakouts, 2 male + 2 female | 4 | Build any tap or Y: ARES SCB (pins 4/3/7), ARES GP (3/7/8), Quantum (3/4), odd pinouts | SchmalzTech **ST-DB9-F-R-FT** (female, Digi-Key 3132-ST-DB9-F-R-FT-ND) · SchmalzTech **ST-DB9-M-R-MIN** (male, Digi-Key 3132-ST-DB9-M-R-MIN-ND) · Adafruit **3123** (male) |
| DB9 null-modem adapter | 1 | A port wired the other way | StarTech **NM9MF** (M/F; Digi-Key listing not confirmed) |
| DB9 gender changers, M–M and F–F | 1 each | Mismatched connectors | StarTech **GC9SM** (M/M) · Tripp Lite **P152-000** (M/M), **P150-000** (F/F) · Micro Connectors **G02-104SL** (M/M slimline, Digi-Key) |
| 1/4" mono plug → DB9 female cable (tip → pin 2, sleeve → pin 5) | 1 | CTS legacy scoreboard output | Build from Switchcraft **280** or **184** (1/4" mono plug, Digi-Key) + a female DB9 breakout above |
| 1/4" Y splitter, one plug to two jacks | 1 | Sharing the CTS legacy output with the existing scoreboard | No Digi-Key/Mouser listing confirmed; mono Y adapters such as Surplus Sales **A.25Y**, or build one from two Switchcraft jacks |
| Mating plug or pigtail for the Gen7 RS-485 connector (CONXALL/Switchcraft 4-pin, as on CTS scoreboard cables) | 1–2 | Gen7 Blue Square / Black Diamond outputs | Switchcraft/Conxall **3282-4SG-321** (4-pin cable socket, Digi-Key SC3212-ND) — confirm it mates with the timer's jack before ordering, or use a spare CTS scoreboard cable |

## Per console

| Console / output | Signal | Connector at the console | Adapter | To share a port in use |
| --- | --- | --- | --- | --- |
| CTS System 5 / 6, Gen7 legacy output | RS-232 | 1/4" jack | USB-RS232 + 1/4"→DB9 cable | 1/4" Y splitter |
| Gen7 RS-485 outputs (all protocols) | RS-485 | CONXALL/Switchcraft 4-pin | USB-RS485 | Parallel tap on data+ / data− / GND, termination off |
| OmniSport J5 RTD / CTS, J6 CTS | RS-232 | DB9 | USB-RS232 + DB9 M–F | Receive-only tap: pin 2 + pin 5 only |
| OmniSport Ethernet | UDP | RJ45 | none | Switch |
| OmniSport scoreboard port (OMEGA) | not confirmed | J2 / J3 — check on site | match the signal | — |
| ARES SCB (ERTD, UNT4, GALACTICA) | RS-485 (or 20 mA) | DB9 female, pins 4 / 3 / 7 | USB-RS485 + male DB9 breakout | Parallel tap, termination off |
| ARES GP (OSM6) | RS-485 | DB9 female, pins 3 / 7 / 8 | USB-RS485 + male DB9 breakout | Parallel tap on 3 / 7 / 8, termination off |
| Quantum (OSM6) | RS-422/485 | DB9, pins 3 / 4 | USB-RS485 + breakout | Parallel tap, termination off |
| Seiko PT-8000 | RS-422 | not confirmed — check on site | USB-RS485 + breakout | Parallel tap, termination off |
| Dolphin | TCP | the Dolphin PC's network | none | Switch |

## Sharing a port that is in use

- **RS-232** (CTS legacy, OmniSport J5 / J6): take only the console's transmit line and
  ground to your adapter (a DB9 → pin 2, pin 5 tap). Not an off-the-shelf DB9 Y that
  parallels every pin: on J6 your adapter's transmit would fight Meet Manager's.
- **RS-485 / 422** (Gen7, ARES, Quantum, Seiko PT-8000): connect your adapter in parallel on the data
  pair plus ground, as one more listener. **Termination off** when the line already
  runs to a scoreboard or a Meet Manager PC; on only when Splouch is alone at the end
  of a long cable.
- **Network** (OmniSport Ethernet, Dolphin): nothing to split — a switch; UDP
  broadcasts reach every port.

## Cautions

- **Gen7 RS-485 outputs also carry 12 V power** (up to 2.5 A per the manual). Wire only
  data+, data− and ground; never the power pin to the adapter.
- Not confirmed: the OmniSport J2 / J3 scoreboard outputs (OMEGA mode), the exact pin
  assignment of the Gen7 RS-485 jack, and which Quantum pin is ground. Check on site,
  or from the venue's existing cables, before buying the matching plug.

## Minimum kit for one visit

One USB-RS232 adapter, one USB-RS485 adapter, the DB9 breakouts, a DB9 M–F cable and a
small switch: that covers the OmniSport (J5 and Ethernet), the ARES SCB, and — with the
1/4" cable — the CTS legacy output.
