#!/usr/bin/env python3
"""Genera metodologias_app.html (template.html + app.js + datos embebidos).
Uso: python build_app.py [scrape_out] [salida]
Lee data/clasificacion_tareas.json (DeepSeek, ver classify_tasks.py); si falta un método usa la heurística provisional."""

import base64
import collections
import io
import json
import re
import sys
from pathlib import Path

import classify_tasks as C
from PIL import Image

HERE = Path(__file__).parent
ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "scrape_out")
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else ".")
D = ROOT / "data"

# Jerarquía y colores ORIGINALES de la web (extraídos de su JS): [claro, oscuro]. Descripciones también originales.
JER = {
    "Analisis Interno": {
        "nombre": "Análisis interno",
        "c": ["#9333EA", "#A855F7"],
        "d": "Descompone la realidad en partes para aprender de la fragmentación.",
        "cats": {
            "Descripcion pura": {
                "c": ["#9F46F2", "#B574F7"],
                "d": "Describe fenómenos directamente sin filtros teóricos previos.",
                "subs": {
                    "Analisis naturalista": ["#B574F7", "#CCA3FA"],
                    "Analisis linguistico": ["#DE6EF3", "#E59BF7"],
                    "Conceptualizacion": ["#E59BF7", "#E59BF7"],
                },
            },
            "Descripcion limitada por teoria": {
                "c": ["#6F2ED8", "#7F4FE8"],
                "d": "Utiliza marcos teóricos específicos para guiar la descripción.",
                "subs": {
                    "Analisis naturalista": ["#7F4FE8", "#9E7FF2"],
                    "Analisis linguistico": ["#9E7FF2", "#B6A3F7"],
                    "Conceptualizacion": ["#B6A3F7", "#B6A3F7"],
                },
            },
        },
    },
    "Analisis Formal": {
        "nombre": "Análisis formal",
        "c": ["#0D9488", "#14B8A6"],
        "d": "Sintetiza patrones para revelar estructuras formales y sistemáticas.",
        "cats": {
            "Induccion comparativa desde datos abiertos": {
                "c": ["#14B8A6", "#2DD4BF"],
                "d": "Compara casos para identificar patrones emergentes.",
                "subs": {
                    "Analisis linguistico": ["#2DD4BF", "#5EEAD4"],
                    "Conceptualizacion": ["#5EEAD4", "#99F6E4"],
                    "Metodos causales": ["#22D3EE", "#67E8F9"],
                },
            },
            "Construccion de modelos basados en tecnicas especificas": {
                "c": ["#0891B2", "#06B6D4"],
                "d": "Aplica técnicas formales para modelar estructuras sociales.",
                "subs": {
                    "Analisis linguistico": ["#06B6D4", "#22D3EE"],
                    "Analisis naturalista": ["#10B981", "#34D399"],
                    "Conceptualizacion": ["#6EE7B7", "#6EE7B7"],
                },
            },
        },
    },
}
SUBDESC = {
    "Analisis naturalista": "Examina fenómenos en contextos naturales sin imposición teórica.",
    "Analisis linguistico": "Analiza el uso del lenguaje en contextos reales.",
    "Conceptualizacion": "Identifica temas y conceptos que emergen directamente de los datos.",
    "Metodos causales": "Reconstruye relaciones causales entre fenómenos a partir de los datos.",
}


def img_uri(mid):
    if not mid:
        return None
    f = D / "img" / (mid.replace("-", "_") + ".png")
    if not f.exists():
        return None
    try:
        im = Image.open(f).convert("RGB").resize((128, 128))
    except Exception:
        return None
    b = io.BytesIO()
    im.save(b, "WEBP", quality=70)
    return "data:image/webp;base64," + base64.b64encode(b.getvalue()).decode()


def doi_cabecera(txt):
    m = re.search(r"10\.\d{4,9}/[^\s\"<>,;)\]]+", txt[:6000])
    return m.group(0).rstrip(".") if m else None


metodos = json.load(open(D / "metodos_completos.json", encoding="utf-8"))
refs = json.load(open(D / "pdf_refs.json", encoding="utf-8"))
pdft = json.load(open(D / "pdf_texts.json", encoding="utf-8"))
cl_path = D / "clasificacion_tareas.json"
cl = (
    json.load(open(cl_path, encoding="utf-8"))
    if cl_path.exists()
    else {"fuente": "heuristica", "metodos": {}}
)
ORDEN = [v[0] for v in C.ETAPAS.values()]
out, srcs = [], collections.Counter()
for m in metodos:
    me = m["metadatos_extraidos"]
    ej = m.get("ejemplo") or {}
    ref = (refs.get(m["id"]) or [{}])[0]
    txt = ((pdft.get(m["id"]) or [{}])[0]).get("text", "")
    if ej.get("url"):
        url, origen = ej["url"], "ejemplo de estudio curado"
    elif ref.get("url"):
        url, origen = ref["url"], "documento descargado"
    elif d := doi_cabecera(txt):
        url, origen = "https://doi.org/" + d, "DOI detectado en el PDF aportado"
    else:
        url, origen = None, "PDF aportado manualmente, sin enlace"
    tareas = C.tareas_de(m)
    prev = cl["metodos"].get(m["id"]) if cl.get("fuente") != "heuristica" else None
    if tareas and prev and all(str(t["id"]) in prev for t in tareas):
        lab, src = {int(k): v for k, v in prev.items()}, "ia"
    else:
        lab, src = C.heuristica(tareas), "heuristica"
    if tareas:
        srcs[src] += 1
    etapas, res, ent = [], {k: 0 for k in ORDEN}, collections.Counter()
    for fase, (eid, _, _, _) in C.ETAPAS.items():
        ts = [
            [
                t["id"],
                t["texto"],
                lab[t["id"]]["tipo"],
                lab[t["id"]]["datos"],
                lab[t["id"]]["narrativa"],
                lab[t["id"]]["tono"],
                lab[t["id"]]["conf"],
            ]
            for t in tareas
            if t["fase"] == fase
        ]
        for t in ts:
            ent.update(t[3])
        res[eid] = len(ts)
        if ts:
            etapas.append({"id": eid, "t": ts})
    out.append(
        {
            "id": m["id"],
            "nombre": m["nombre"],
            "familia": m["familia"],
            "categoria": m["categoria"],
            "subcategoria": m["subcategoria"],
            "ideal_para": m["ideal_para"],
            "src": src if tareas else None,
            "res": {**res, "total": len(tareas)},
            "entradas": [{"id": k, "n": n} for k, n in ent.most_common()],
            "etapas": etapas,
            "estudio": {
                "titulo": me.get("titulo") or ej.get("titulo") or "",
                "autores": me.get("autores") or [],
                "anio": me.get("anio_publicacion"),
                "tipo": me.get("tipo_documento"),
                "url": url,
                "origen": origen,
                "confianza": me["confianza"],
                "notas": me.get("notas_limitaciones"),
            },
        }
    )
meta = {
    "etapas": [{"id": v[0], "n": v[1], "c": v[2]} for v in C.ETAPAS.values()],
    "tipos": {
        k: {"n": v[0], "g": v[1], "c": v[2], "d": v[3]} for k, v in C.TIPOS.items()
    },
    "datos": {k: {"n": v[0], "c": v[1]} for k, v in C.DATOS.items()},
    "tonos": C.TONOS,
    "narr": C.NARR,
    "jer": JER,
    "subdesc": SUBDESC,
}
OUT.mkdir(parents=True, exist_ok=True)
emb = {
    **meta,
    "metodos": [{**x, "img": img_uri(x["id"])} for x in out],
}
tpl = (HERE / "template.html").read_text(encoding="utf-8")
js = (HERE / "app.js").read_text(encoding="utf-8")
assert '<script src="app.js"></script>' in tpl and re.search(
    r"/\*DATA\*/\s*null", tpl
), "template.html sin app.js o sin /*DATA*/null"
html = tpl.replace('<script src="app.js"></script>', "<script>" + js + "</script>")
html = re.sub(
    r"/\*DATA\*/\s*null",
    lambda _: json.dumps(emb, ensure_ascii=False, separators=(",", ":")),
    html,
    count=1,
)
(OUT / "metodologias_app.html").write_text(html, encoding="utf-8")
print(
    len(out),
    "métodos |",
    sum(x["res"]["total"] for x in out),
    "tareas |",
    sum(1 for x in emb["metodos"] if x["img"]),
    "con imagen |",
    dict(srcs),
    "| HTML",
    len(html) // 1024,
    "KB",
)
