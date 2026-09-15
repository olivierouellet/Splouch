#!/usr/bin/env python3
"""Convert a binary console capture (`.cap`) to the hex-text `.raw` this repo keeps.

The two are the same bytes. `.cap` is what a capture tool writes straight off the
serial line; `.raw` is that stream as lower-case hex, sixteen bytes to a line. The
Test tab lists and plays `.raw` only — a `.cap` beside its `.raw` showed every
capture twice in the operator's session list, as two rows that played identically.

So this is the one step a future `.cap` needs before it can be used:

    python3 server/console_recordings/cap-to-raw.py session.cap

writes `session.raw` beside it. Give it `-o` to put the result somewhere else, or
`-` to write to stdout. Nothing is overwritten without `--force`.

Going the other way is a one-liner if it is ever needed:

    xxd -r -p session.raw > session.cap
"""
import argparse
import os
import sys

# Sixteen bytes a line, which is what the existing `.raw` files use and what makes
# a diff between two captures legible.
PER_LINE = 16


def to_hex(data: bytes, per_line: int = PER_LINE) -> str:
    """The byte stream as the `.raw` files spell it: lower-case hex, space-separated.

    `worker._play_cts_file` reads a `.raw` with a regex that takes any run of two
    hex digits and ignores everything else, so the line width and the spacing are
    for the reader, not the parser.
    """
    lines = []
    for start in range(0, len(data), per_line):
        chunk = data[start:start + per_line]
        lines.append(' '.join(f'{byte:02x}' for byte in chunk))
    return '\n'.join(lines) + ('\n' if lines else '')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog='cap-to-raw',
        description='Convert a binary .cap console capture to hex-text .raw.')
    parser.add_argument('source', help='the .cap file to read')
    parser.add_argument('-o', '--output',
                        help="where to write (default: alongside, as .raw; '-' for stdout)")
    parser.add_argument('--force', action='store_true',
                        help='overwrite the output if it already exists')
    parser.add_argument('--per-line', type=int, default=PER_LINE,
                        help=f'bytes per line (default {PER_LINE})')
    args = parser.parse_args(argv)

    try:
        with open(args.source, 'rb') as handle:
            data = handle.read()
    except OSError as error:
        print(f'cap-to-raw: {error}', file=sys.stderr)
        return 1

    if not data:
        print(f'cap-to-raw: {args.source} is empty', file=sys.stderr)
        return 1

    text = to_hex(data, max(1, args.per_line))

    if args.output == '-':
        sys.stdout.write(text)
        return 0

    target = args.output or (os.path.splitext(args.source)[0] + '.raw')
    if os.path.exists(target) and not args.force:
        print(f'cap-to-raw: {target} exists (use --force)', file=sys.stderr)
        return 1
    try:
        with open(target, 'w', encoding='utf-8') as handle:
            handle.write(text)
    except OSError as error:
        print(f'cap-to-raw: {error}', file=sys.stderr)
        return 1

    print(f'{args.source} -> {target}  ({len(data)} bytes)')
    # A capture carries no start lists. Without a companion `.lxf` the replay runs
    # with blank names, which reads as a broken recording rather than a missing one.
    companion = os.path.splitext(target)[0] + '.lxf'
    if not os.path.exists(companion):
        print(f'note: no {os.path.basename(companion)} beside it — the replay will '
              f'have no swimmer names. See this folder\'s README.', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
