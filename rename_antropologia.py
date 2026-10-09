#!/usr/bin/env python3
"""Renombra 'Antropologia comparada' -> 'Etnografia comparada' en todos los
archivos de datos, el sitemap propuesto y scrape_metodologias.py, y renombra
los archivos PDF e imagen correspondientes.

Uso: python rename_antropologia.py
"""

import json
from pathlib import Path

ROOT = Path(__file__).parent
D = ROOT / "scrape_out" / "data"

OLD_ID = "antropologia-comparada"
NEW_ID = "etnografia-comparada"
OLD_NAME = "Antropologia comparada"
NEW_NAME = "Etnografia comparada"
OLD_IMG = "antropologia_comparada"
NEW_IMG = "etnografia_comparada"

# Archivos de texto/JSON/XML que contienen el id o el nombre
TEXT_FILES = [
    ROOT / "scrape_metodologias.py",
    ROOT / "scrape_out" / "sitemap_propuesto.json",
    ROOT / "scrape_out" / "sitemap_propuesto.xml",
    D / "metodos_completos.json",
    D / "pdf_refs.json",
    D / "pdf_texts.json",
    D / "clasificacion_tareas.json",
    D / "methods.json",
    D / "content.json",
    D / "methods_raw.json",
    D / "candidates.json",
    D / "tree.json",
]

for f in TEXT_FILES:
    if not f.exists():
        print(f"SKIP (no existe): {f}")
        continue
    txt = f.read_text(encoding="utf-8")
    orig = txt
    txt = txt.replace(OLD_ID, NEW_ID)
    txt = txt.replace(OLD_NAME, NEW_NAME)
    txt = txt.replace(OLD_IMG, NEW_IMG)
    if txt != orig:
        f.write_text(txt, encoding="utf-8")
        print(f"OK: {f.relative_to(ROOT)}")
    else:
        print(f"sin cambios: {f.relative_to(ROOT)}")

# Renombrar archivos en disco
moves = [
    (D / "pdfs" / f"{OLD_ID}_0.pdf", D / "pdfs" / f"{NEW_ID}_0.pdf"),
    (D / "img" / f"{OLD_IMG}.png", D / "img" / f"{NEW_IMG}.png"),
]
for src, dst in moves:
    if src.exists():
        src.rename(dst)
        print(f"RENOMBRADO: {src.name} -> {dst.name}")
    else:
        print(f"SKIP (no existe): {src}")

# Verificación: no debe quedar ninguna referencia
import subprocess
import sys

print("\n--- Verificación (grep) ---")
r = subprocess.run(
    [
        sys.executable,
        "-c",
        "import pathlib,re,sys\n"
        "pat=re.compile('antropologia', re.I)\n"
        "hits=[]\n"
        "for p in pathlib.Path('.').rglob('*'):\n"
        "    if p.is_file() and '.git' not in p.parts and '__pycache__' not in p.parts:\n"
        "        try:\n"
        "            t=p.read_text(encoding='utf-8', errors='ignore')\n"
        "        except Exception:\n"
        "            continue\n"
        "        if pat.search(t):\n"
        "            hits.append(str(p))\n"
        "print('\\n'.join(hits) if hits else 'SIN REFERENCIAS RESTANTES')",
    ],
    cwd=ROOT,
    capture_output=True,
    text=True,
)
print(r.stdout.strip())
if r.returncode != 0:
    print(r.stderr)
