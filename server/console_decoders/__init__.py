import importlib.util
import os
import sys

from .base import ConsoleDecoder, SerialConfig
from .swiss_timing_ares21 import Ares21Decoder
from .cts_gen6 import CTSGen6Decoder
from .cts_gen7 import CTSGen7Decoder
from .manual import ManualDecoder
from .omnisport_2000 import Omnisport2000Decoder
from .quantum import QuantumDecoder

# Each entry: (settings key, human-readable label, decoder class key)
# The settings key is what gets stored in settings.json as console_type.
# Multiple console models can share the same decoder when the protocol is identical.
CONSOLE_OPTIONS: list[tuple[str, str, str]] = [
    ('cts_gen5',        'System 5 (Colorado Timing System)',         'cts_gen6'),
    ('cts_gen6',        'System 6 (Colorado Timing System)',         'cts_gen6'),
    ('cts_gen7_legacy', 'Gen7 Legacy (Colorado Timing System)',      'cts_gen6'),
    ('cts_gen7',        'Gen7 Serial (Colorado Timing System)',      'cts_gen7'),
    ('dak_2000',        'Omnisport 2000 (Daktronics)',               'dak_2000'),
    ('omega_ares21',    'Ares 21 (Swiss Timing Omega)',              'omega_ares21'),
    ('omega_quantum',   'Quantum (Swiss Timing Omega)',              'omega_quantum'),
    # Last, after every real console: not a console at all, but the answer for a meet
    # that has none — the operator sets event and heat by hand from /manual.
    ('manual',          'Manual — no timing console',                'manual'),
]

DECODERS: dict[str, type[ConsoleDecoder]] = {
    'cts_gen6':        CTSGen6Decoder,
    'cts_gen7':        CTSGen7Decoder,
    'dak_2000':        Omnisport2000Decoder,
    'omega_ares21':    Ares21Decoder,
    'omega_quantum':   QuantumDecoder,
    'manual':          ManualDecoder,
}

# Base URL for the full per-console setup guides on GitHub.
DOCS_BASE_URL = 'https://github.com/olivierouellet/Splouch/blob/master/docs/consoles/'

# Curated hardware/wiring summary per decoder, surfaced in Settings → Timing.
# Keyed by decoder key (the three CTS models share one decoder, hence one entry).
# The full per-console guide lives in docs/consoles/<doc>.
CONSOLE_INFO: dict[str, dict] = {
    'cts_gen6': {
        'adapter':  'USB-to-RS232 (DB9)',
        'wiring':   '1/4" Y-cable — tip → DB9 pin 2 (RX), sleeve → pin 5 (GND)',
        'protocol': 'RS-232 · 9600 baud · 8-E-1',
        'tested':   True,
        'doc':      'cts-gen6.md',
    },
    'cts_gen7': {
        'adapter':  'USB-to-RS485',
        'wiring':   'Connect to the RS-485 port on the Gen7 console',
        'protocol': 'RS-485 · 115200 baud · 8-N-1',
        'tested':   False,
        'doc':      'cts-gen7.md',
    },
    'dak_2000': {
        'adapter':  'USB-to-RS232 (DB9)',
        'wiring':   'DB9 to the J6 Results Port (or J5 RTD Port)',
        'protocol': 'RS-232 · 19200 baud · 8-N-1',
        'tested':   False,
        'doc':      'omnisport-2000.md',
    },
    'omega_ares21': {
        'adapter':  'USB-to-RS485',
        'wiring':   'Non-standard DB9 pinout (RS-485) — see guide',
        'protocol': 'RS-485 · 9600 baud · 8-N-1',
        'tested':   False,
        'doc':      'ares-21.md',
    },
    'omega_quantum': {
        'adapter':  'USB-to-RS485',
        'wiring':   'Quantum pin 3 → A- · pin 4 → B+',
        'protocol': 'RS-485 · 9600 baud · 8-N-1',
        'tested':   False,
        'doc':      'quantum.md',
    },
    'manual': {
        'adapter':  'None',
        'wiring':   'Nothing is connected to the server',
        'protocol': 'None — event and heat are set by hand from /manual',
        'tested':   True,
        'doc':      'manual.md',
    },
}


def console_info_for(console_type: str) -> dict | None:
    """Return the hardware/wiring summary for *console_type*, or None if unknown.

    Resolves the console key to its decoder key (via CONSOLE_OPTIONS) and looks
    up CONSOLE_INFO. The returned dict adds the human label and a full doc_url
    so templates can render a summary card plus a link to the complete guide.
    """
    match = next(
        ((label, dec) for key, label, dec in CONSOLE_OPTIONS if key == console_type),
        None,
    )
    if match is None:
        return None
    label, decoder_key = match
    info = CONSOLE_INFO.get(decoder_key)
    if info is None:
        return None
    # Read off the class, not an instance: Settings needs to know whether this console
    # has a wire before any decoder for it has been built, and the flag lives on the
    # decoder so a local-only plugin gets the same treatment without touching this
    # table. Default True for a plugin written against the older contract.
    cls = DECODERS.get(decoder_key)
    return {**info, 'label': label, 'doc_url': DOCS_BASE_URL + info['doc'],
            'requires_serial': getattr(cls, 'requires_serial', True)}


def load_custom_decoders(folder: str) -> None:
    """Load decoder plugins from *folder* and merge them into CONSOLE_OPTIONS / DECODERS.

    Each .py file in the folder must define:
        CONSOLE_OPTIONS = [('settings_key', 'Human Label', 'decoder_key'), ...]
        DECODERS        = {'decoder_key': MyDecoderClass, ...}

    Keys already present in the built-in registries are silently skipped so
    that calling this function multiple times is safe.
    """
    if not os.path.isdir(folder):
        return

    existing_keys    = {k     for k, _, _  in CONSOLE_OPTIONS}
    existing_decoders = set(DECODERS)

    for fname in sorted(os.listdir(folder)):
        if not fname.endswith('.py') or fname.startswith('_'):
            continue
        path        = os.path.join(folder, fname)
        module_name = f'_splouch_custom_decoder_{fname[:-3]}'
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            mod  = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = mod
            spec.loader.exec_module(mod)

            for entry in getattr(mod, 'CONSOLE_OPTIONS', []):
                if entry[0] not in existing_keys:
                    CONSOLE_OPTIONS.append(entry)
                    existing_keys.add(entry[0])

            for key, cls in getattr(mod, 'DECODERS', {}).items():
                if key not in existing_decoders:
                    DECODERS[key] = cls
                    existing_decoders.add(key)

        except Exception as e:
            print(f'[custom decoder] failed to load {fname}: {e}', flush=True)


def make_decoder(console_type: str, cfg: dict) -> ConsoleDecoder:
    # Resolve console key → decoder key, falling back to console_type itself
    # so that existing settings with a direct decoder key still work.
    decoder_key = next(
        (dec for key, _, dec in CONSOLE_OPTIONS if key == console_type),
        console_type,
    )
    cls = DECODERS.get(decoder_key, CTSGen6Decoder)
    return cls(cfg)
