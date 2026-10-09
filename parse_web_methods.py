#!/usr/bin/env python3
"""
Extrae COMPLETAMENTE la información de los métodos de la web.
No toca OpenAlex ni nada externo. Solo parsea lo que ya descargamos.

Usa el chunk JS que contiene la base de datos de los 48 métodos.
Saca cada método con TODA su información: descripción, ejemplo, hallazgos, links, etc.

Uso:
  python parse_web_methods.py --scrape-dir scrape_out
  
Genera:
  - metodos_completos.json: cada método con todos sus campos
  - metodos_por_familia.json: árbol jerarquizado
  - metodos_links.json: solo los links de cada método
"""
import argparse
import json
import re
import subprocess
from pathlib import Path


def brace_match(s, start):
    """Encuentra la llave de cierre balanceada."""
    depth, q, esc = 0, None, False
    for i in range(start, len(s)):
        c = s[i]
        if q:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == q:
                q = None
        elif c in '"\'`':
            q = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("llave de cierre no encontrada")


def js_object_to_python(lit):
    """Convierte objeto JS (claves sin comillas, comillas simples) a Python dict."""
    # Intenta con Node.js si está disponible (más confiable)
    try:
        subprocess.run(["node", "--version"], capture_output=True, check=True)
        js_code = f"console.log(JSON.stringify({lit}))"
        result = subprocess.run(
            ["node", "-e", js_code],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return json.loads(result.stdout.strip())
    except Exception:
        pass

    # Fallback: regex-based (menos confiable pero funciona para la mayoría)
    lit = re.sub(r'(\w+):', r'"\1":', lit)  # claves sin comillas -> con comillas
    lit = lit.replace("'", '"')  # comillas simples -> dobles
    try:
        return json.loads(lit)
    except json.JSONDecodeError as e:
        raise ValueError(f"No se pudo parsear: {e}")


def extract_methods_from_chunk(chunk_path):
    """Lee el chunk JS y extrae el objeto de métodos."""
    txt = chunk_path.read_text(encoding="utf-8", errors="ignore")
    
    # Busca el objeto e7 que contiene la estructura de métodos
    k = txt.find('"ANALISIS INTERNO"')
    if k < 0:
        raise ValueError("No encontré la base de datos en el chunk")
    
    start = txt.rfind("{", 0, k)
    end = brace_match(txt, start)
    obj_str = txt[start:end + 1]
    
    # Parsea el objeto
    data = js_object_to_python(obj_str)
    return data


def normalize_methods_flat(data):
    """Convierte la estructura jerarquizada en una lista plana de métodos."""
    methods = []
    for familia, categorias in data.items():
        for categoria, subcategorias in categorias.items():
            for subcategoria, items in subcategorias.items():
                for item in items:
                    # Cada item tiene: metodo, descripcion, good_for, ejemplo_aplicacion, url, resumen_hallazgos, image_prompt
                    methods.append({
                        "familia": familia,
                        "categoria": categoria,
                        "subcategoria": subcategoria,
                        "nombre": item.get("metodo"),
                        "descripcion_caso": item.get("descripcion"),
                        "ideal_para": item.get("good_for"),
                        "ejemplo": {
                            "titulo": item.get("ejemplo_aplicacion"),
                            "url": item.get("url"),
                        },
                        "hallazgos": item.get("resumen_hallazgos"),
                        "image_prompt": item.get("image_prompt"),
                    })
    return methods


def build_hierarchy(methods):
    """Reconstruye la jerarquía familia > categoría > subcategoría > métodos."""
    hierarchy = {}
    for m in methods:
        fam = m["familia"]
        cat = m["categoria"]
        sub = m["subcategoria"]
        if fam not in hierarchy:
            hierarchy[fam] = {}
        if cat not in hierarchy[fam]:
            hierarchy[fam][cat] = {}
        if sub not in hierarchy[fam][cat]:
            hierarchy[fam][cat][sub] = []
        hierarchy[fam][cat][sub].append({
            "nombre": m["nombre"],
            "descripcion_caso": m["descripcion_caso"],
            "ideal_para": m["ideal_para"],
            "ejemplo": m["ejemplo"],
            "hallazgos": m["hallazgos"],
        })
    return hierarchy


def extract_links(methods):
    """Extrae solo los links de cada método."""
    links = {}
    for m in methods:
        nombre = m["nombre"]
        url = m["ejemplo"].get("url")
        if url:
            links[nombre] = {
                "ejemplo": url,
                "titulo": m["ejemplo"].get("titulo"),
                "familia": m["familia"],
                "categoria": m["categoria"],
            }
    return links


def parse_web_methods(scrape_dir="scrape_out"):
    """Orquesta la extracción completa."""
    base = Path(scrape_dir)
    chunks_dir = base / "site" / "assets" / "_next" / "static" / "chunks"
    
    if not chunks_dir.exists():
        raise SystemExit(f"No encontré chunks en {chunks_dir}. ¿Corriste 'crawl' primero?")
    
    # Encuentra el chunk con los métodos (el más grande, contiene e7)
    chunks = sorted(chunks_dir.glob("*.js"), key=lambda p: -p.stat().st_size)
    for chunk in chunks:
        try:
            print(f"Probando {chunk.name}...", end=" ", flush=True)
            data = extract_methods_from_chunk(chunk)
            print("✓ encontrado")
            break
        except (ValueError, KeyError):
            print("✗")
    else:
        raise SystemExit("No encontré la base de datos en ningún chunk")
    
    # Extrae métodos en formato plano
    methods = normalize_methods_flat(data)
    print(f"\n{len(methods)} métodos extraídos")
    
    # Guarda versiones
    out = base / "data"
    out.mkdir(parents=True, exist_ok=True)
    
    # 1. Plano con todos los detalles
    out_flat = out / "metodos_completos.json"
    out_flat.write_text(json.dumps(methods, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ {out_flat}")
    
    # 2. Jerarquía
    hierarchy = build_hierarchy(methods)
    out_hier = out / "metodos_por_familia.json"
    out_hier.write_text(json.dumps(hierarchy, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ {out_hier}")
    
    # 3. Links
    links = extract_links(methods)
    out_links = out / "metodos_links.json"
    out_links.write_text(json.dumps(links, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ {out_links} ({len(links)} links)")
    
    # Resumen
    print(f"\nResumen:")
    for fam in hierarchy:
        total = sum(len(ms) for subs in hierarchy[fam].values() for ms in subs.values())
        print(f"  {fam}: {total} métodos")
    
    return methods


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scrape-dir", default="scrape_out", help="Carpeta con el scrapeo")
    a = ap.parse_args()
    parse_web_methods(a.scrape_dir)
