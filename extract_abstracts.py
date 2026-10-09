#!/usr/bin/env python3
"""Extrae el inicio (abstract/intro) de cada PDF de pdf_texts.json a un
archivo compacto para que los agentes redacten 'patrones_emergentes'."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent
D = ROOT / "scrape_out" / "data"

t = json.load(open(D / "pdf_texts.json", encoding="utf-8"))
m = json.load(open(D / "metodos_completos.json", encoding="utf-8"))

# Orden de los métodos según metodos_completos.json
order = [x["id"] for x in m]

JSTOR = re.compile(
    r"(This content downloaded from.*?Terms and Conditions|"
    r"JSTOR is a not-for-profit service.*?access to American Sociological Review\.|"
    r"http://www\.jstor\.org.*?$|"
    r"All use subject to JSTOR Terms and Conditions)",
    re.S | re.I,
)

out = []
for mid in order:
    txt = t.get(mid, [{}])[0].get("text", "")
    txt = JSTOR.sub(" ", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    out.append(f"### {mid}\n{txt[:2600]}\n")

(ROOT / "patrones_abstracts.txt").write_text("\n".join(out), encoding="utf-8")
print("escrito patrones_abstracts.txt con", len(order), "métodos")
