#!/usr/bin/env python3
"""Extrae 'patrones_emergentes' (descripción sintética ≤3 líneas) de cada método
usando el texto completo del PDF vía Together AI (DeepSeek), con caché.

Uso:
    python extract_patrones.py --root scrape_out
    python extract_patrones.py --root scrape_out --solo etnografia-comparada
    python extract_patrones.py --root scrape_out --forzar

Salida: añade la clave 'patrones_emergentes' a cada método en
<root>/data/metodos_completos.json (se guarda incrementalmente).
"""

import argparse
import json
import sys
import time
from pathlib import Path

from together import Together

MODEL = "deepseek-ai/DeepSeek-V4-Pro-0813"
API_KEY = "tgp_v1_CKLcJWHK4aCThBa5kIx9SX9tcTWDEyRALNyAjQhdoa0"
CACHE_KEY = "patrones_emergentes_v2"  # fijo: agrupa peticiones para reusar prefijo
MAX_CHARS = 100000  # cubre los PDFs completos (el mayor ronda 90k chars)

# SYSTEM_PROMPT constante de módulo: cualquier cambio invalida la caché.
SYSTEM_PROMPT = """Eres un experto en metodología de investigación cualitativa y síntesis académica. A partir del texto completo de un estudio (extraído de su PDF), redactas una descripción sintética en español.

# TAREA
Lee el texto del estudio y produce un párrafo breve (máximo 3 líneas) en lenguaje claro y sencillo que cubra DOS cosas:
1. Los patrones empíricos centrales que emergen del estudio (qué encontraron los autores en sus datos).
2. La interpretación personal o teorización que el autor o autores hacen de esos patrones (qué significan, qué explican, qué concluyen).

# REGLAS
- Máximo 3 líneas (unas 50-70 palabras). Sé conciso y directo.
- Un solo párrafo, sin saltos de línea.
- Lenguaje claro y sencillo, sin jerga innecesaria.
- No cites, no parafrasees el texto, no uses markdown.
- No inventes información que no esté en el texto.
- Si el texto no permite identificar patrones o interpretación, redacta lo que sí se pueda inferir con honestidad.

# SALIDA (obligatoria)
Devuelve ÚNICAMENTE un objeto JSON válido con la forma {"patrones_emergentes": "..."}. No envuelvas el JSON en ``` ni en ningún otro bloque. No añadas claves nuevas."""

JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["patrones_emergentes"],
    "properties": {
        "patrones_emergentes": {"type": "string"},
    },
}

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = Together(api_key=API_KEY)
    return _client


def _extract_json(text):
    start = text.find("{")
    if start == -1:
        raise ValueError("no se encontró '{' en la respuesta")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("JSON incompleto en la respuesta")


def _usage_dict(usage):
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    if isinstance(usage, dict):
        return usage
    return {
        k: getattr(usage, k, None)
        for k in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "cached_tokens",
            "prompt_tokens_details",
        )
    }


def llamar(mid, nombre, texto, reintentos=3):
    client = _get_client()
    user = f"MÉTODO: {nombre}\n\nTEXTO DEL ESTUDIO:\n{texto}"
    for i in range(reintentos):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                temperature=0.0,
                top_p=0.95,
                max_tokens=2000,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
                extra_body={"prompt_cache_key": CACHE_KEY},
            )
            u = _usage_dict(resp.usage)
            details = u.get("prompt_tokens_details") or {}
            if hasattr(details, "model_dump"):
                details = details.model_dump()
            cached = (details or {}).get("cached_tokens", u.get("cached_tokens", 0))
            total_in = u.get("prompt_tokens", 0) or 0
            pct = (100 * cached / total_in) if total_in else 0
            print(f"  [{mid}] prompt={total_in} cached={cached} ({pct:.0f}%)")
            content = resp.choices[0].message.content
            return _extract_json(content)
        except Exception as e:
            print(f"  ! {mid} intento {i + 1}: {str(e)[:90]}", file=sys.stderr)
            time.sleep(2 * (i + 1))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="scrape_out")
    ap.add_argument("--solo", nargs="*", default=None)
    ap.add_argument("--forzar", action="store_true")
    args = ap.parse_args()

    d = Path(args.root) / "data"
    metodos_path = d / "metodos_completos.json"
    pdf_texts_path = d / "pdf_texts.json"

    metodos = json.load(open(metodos_path, encoding="utf-8"))
    pdf_texts = json.load(open(pdf_texts_path, encoding="utf-8"))

    for m in metodos:
        mid = m["id"]
        if args.solo and mid not in args.solo:
            continue
        if not args.forzar and m.get("patrones_emergentes"):
            continue
        entries = pdf_texts.get(mid) or [{}]
        texto = (entries[0].get("text") or "").strip()
        if not texto:
            print(f"  ! {mid}: sin texto de PDF, se omite", file=sys.stderr)
            continue
        texto = texto[:MAX_CHARS]
        resp = llamar(mid, m["nombre"], texto)
        if resp is None:
            print(f"  ! {mid}: sin respuesta válida, se omite", file=sys.stderr)
            continue
        pat = (resp.get("patrones_emergentes") or "").strip()
        if not pat:
            print(f"  ! {mid}: patrones_emergentes vacío", file=sys.stderr)
            continue
        m["patrones_emergentes"] = pat
        print(f"{mid}: ok ({len(pat)} chars)")
        json.dump(
            metodos,
            open(metodos_path, "w", encoding="utf-8"),
            ensure_ascii=False,
            indent=2,
        )

    json.dump(
        metodos,
        open(metodos_path, "w", encoding="utf-8"),
        ensure_ascii=False,
        indent=2,
    )
    print("->", metodos_path)


if __name__ == "__main__":
    main()
