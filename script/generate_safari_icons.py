#!/usr/bin/env python3
"""Export the Safari icons from the app's ship layers. Requires rsvg-convert."""

from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parent.parent
SVG = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG)
icon = ET.Element(f"{{{SVG}}}svg", {
    "viewBox": "112 108.5 800 800",
    "style": "fill-rule:evenodd;clip-rule:evenodd;stroke-linejoin:round;stroke-miterlimit:2",
})

for layer in ("BT1", "BT2b", "BT31", "BT32"):
    source = (ROOT / "Harbor/Harbor.icon/Assets" / f"{layer}.svg").read_text()
    # Each exported layer uses the same gradient ID. Keep references distinct.
    source = source.replace('id="_Linear1"', f'id="{layer}-gradient"')
    source = source.replace("url(#_Linear1)", f"url(#{layer}-gradient)")
    for child in ET.fromstring(source):
        icon.append(child)

with tempfile.TemporaryDirectory() as directory:
    source = Path(directory) / "ship.svg"
    ET.ElementTree(icon).write(source, encoding="utf-8", xml_declaration=True)
    for size in (16, 32, 64, 128, 256, 512):
        subprocess.run([
            "rsvg-convert", "--width", str(size), "--height", str(size),
            "--output", str(ROOT / "HarborSafariExtension" / f"icon-{size}.png"),
            str(source),
        ], check=True)
