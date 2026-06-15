"""Pure-Python .po -> .mo compiler (used because GNU gettext tools are not
installed). Supports simple msgid/msgstr pairs with multi-line string
concatenation. Entries with an empty msgstr are skipped so that gettext
falls back to the msgid at runtime.

Usage: python msgfmt.py input.po output.mo
"""

import array
import struct
import sys


def unescape(s):
    s = s.strip()
    if s.startswith('"') and s.endswith('"'):
        s = s[1:-1]
    return (
        s.replace('\\\\', '\x00')
        .replace('\\n', '\n')
        .replace('\\t', '\t')
        .replace('\\"', '"')
        .replace('\x00', '\\')
    )


def parse_po(path):
    messages = {}
    msgid = []
    msgstr = []
    section = None

    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()

            if not line or line.startswith("#"):
                continue

            if line.startswith("msgid "):
                if section == "msgstr" and msgid:
                    messages["".join(msgid)] = "".join(msgstr)
                msgid = [unescape(line[len("msgid "):])]
                msgstr = []
                section = "msgid"
            elif line.startswith("msgstr "):
                msgstr = [unescape(line[len("msgstr "):])]
                section = "msgstr"
            elif line.startswith('"'):
                if section == "msgid":
                    msgid.append(unescape(line))
                elif section == "msgstr":
                    msgstr.append(unescape(line))

    if section == "msgstr" and msgid:
        messages["".join(msgid)] = "".join(msgstr)

    return messages


def write_mo(messages, path):
    keys = sorted(k for k, v in messages.items() if k == "" or v)

    offsets = []
    ids = b""
    strs = b""

    for key in keys:
        value = messages[key]
        key_bytes = key.encode("utf-8")
        value_bytes = value.encode("utf-8")
        offsets.append((len(ids), len(key_bytes), len(strs), len(value_bytes)))
        ids += key_bytes + b"\0"
        strs += value_bytes + b"\0"

    keystart = 7 * 4 + 16 * len(keys)
    valuestart = keystart + len(ids)

    koffsets = []
    voffsets = []
    for o1, l1, o2, l2 in offsets:
        koffsets += [l1, o1 + keystart]
        voffsets += [l2, o2 + valuestart]

    offsets_data = koffsets + voffsets

    output = struct.pack(
        "Iiiiiii",
        0x950412de,
        0,
        len(keys),
        7 * 4,
        7 * 4 + len(keys) * 8,
        0,
        7 * 4 + 2 * len(keys) * 8,
    )
    output += array.array("i", offsets_data).tobytes()
    output += ids
    output += strs

    with open(path, "wb") as f:
        f.write(output)


def main():
    if len(sys.argv) != 3:
        print("usage: msgfmt.py input.po output.mo")
        sys.exit(1)

    messages = parse_po(sys.argv[1])
    write_mo(messages, sys.argv[2])
    print(f"wrote {len(messages)} entries to {sys.argv[2]}")


if __name__ == "__main__":
    main()
