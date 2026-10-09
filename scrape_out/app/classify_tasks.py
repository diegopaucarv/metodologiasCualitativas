#!/usr/bin/env python3
"""Clasificación de tareas por etapa con DeepSeek vía Together AI.

    python classify_tasks.py --root scrape_out
    python classify_tasks.py --root scrape_out --heuristica
    python classify_tasks.py --root scrape_out --solo etnometodologia --forzar
    python classify_tasks.py --exportar-prompt

Salida: <root>/data/clasificacion_tareas.json
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from together import Together

# =============================================================================
# TAXONOMÍA (fuente única)
# =============================================================================
ETAPAS = {
    "pre_analisis": ("preparacion", "Preparación", "#c4b5fd", "prep"),
    "interpretacion_fuentes": ("exploracion", "Exploración", "#a78bfa", "prep"),
    "analisis_profundo": ("analisis", "Análisis", "#8b5cf6", "ana"),
    "sintesis": ("sintesis", "Síntesis", "#6d28d9", "sin"),
    "pasos_adicionales": ("adicional", "Pasos adicionales", "#9ca3af", "any"),
}

TIPOS = {
    "ETI": (
        "Ética",
        "prep",
        "#0ea5e9",
        "Resguardo ético de participantes y datos: consentimiento informado, aprobación de comité de ética, anonimización, confidencialidad, manejo de riesgos.",
    ),
    "ADE": (
        "Adecuación",
        "prep",
        "#14b8a6",
        "Trabajo de gabinete o con fuentes externas (literatura, documentos previos, expertos, piloto) cuyo fin es AJUSTAR, reformular o refinar las preguntas, objetivos, hipótesis o diseño del investigador. NO es recolectar los datos principales.",
    ),
    "CAL": (
        "Calidad",
        "prep",
        "#f59e0b",
        "Identificar, evaluar o mitigar sesgos, limitaciones, vacíos, fiabilidad, representatividad o saturación de los datos o las fuentes.",
    ),
    "CNV": (
        "Conversión de datos",
        "prep",
        "#84cc16",
        "Transcribir, digitalizar, traducir, aplicar OCR, formatear, segmentar, importar o estandarizar el material a un formato de trabajo común.",
    ),
    "IND": (
        "Inducción",
        "ana",
        "#3b82f6",
        "Describir y organizar TODO el conjunto de datos desde los propios datos, sin teoría previa: tematización, paráfrasis, resumen, codificación abierta o inductiva, descripción densa, agrupar fragmentos.",
    ),
    "DED": (
        "Deducción",
        "ana",
        "#6366f1",
        "Usar marcos teóricos, conceptos o categorías previas (a priori) de autores específicos para organizar, priorizar o interpretar los datos: codificación deductiva, aplicar un modelo, teoría o protocolo de análisis ya existente.",
    ),
    "ABD": (
        "Abducción",
        "ana",
        "#d946ef",
        "Crear y verificar hipótesis o explicaciones: comparación constante o iterativa de casos o documentos, creación de conceptos, nombrar patrones, taxonomías o tipologías, codificación axial o selectiva, casos negativos, ir y venir entre teoría y datos.",
    ),
    "CAS": (
        "Estudio de caso",
        "sin",
        "#f97316",
        "Presentar las características del caso organizadas por temas y subtemas (informe descriptivo estructurado).",
    ),
    "ESQ": (
        "Esquema teórico",
        "sin",
        "#ef4444",
        "Formular un modelo, teoría, proposiciones o marco conceptual explícito.",
    ),
    "NAR": (
        "Narrativa",
        "sin",
        "#ec4899",
        "Relato continuo: biográfico (BIO), histórico (HIS) o causal (CAU). Indicar el subtipo en `narrativa`.",
    ),
    "CMP": (
        "Comparación",
        "sin",
        "#eab308",
        "Redactar contrastes entre casos, grupos o narrativas.",
    ),
    "DIA": (
        "Diagramación",
        "sin",
        "#10b981",
        "Unir hechos aparentemente desconectados en un diagrama, mapa, red o esquema visual.",
    ),
    "MEM": (
        "Memo analítico",
        "sin",
        "#b45309",
        "Escribir memos, notas analíticas o reflexividad del investigador. (Tipo añadido)",
    ),
    "ARG": (
        "Discusión",
        "sin",
        "#0891b2",
        "Argumentar e interpretar los hallazgos frente a la literatura, sus limitaciones, implicaciones y conclusiones. (Tipo añadido)",
    ),
    "NC": (
        "Sin clasificación clara",
        "any",
        "#9ca3af",
        "La tarea no encaja con claridad en ningún tipo de su etapa.",
    ),
}

DATOS = {
    "ENT": (
        "Entrevistas y grupos",
        "#e11d74",
        "entrevistas, grupos focales, testimonios, relatos orales",
    ),
    "OBS": (
        "Observación y campo",
        "#0d9488",
        "observación directa, etnografía, notas o diario de campo",
    ),
    "AVI": (
        "Audio y video",
        "#ea580c",
        "grabaciones de audio, video o fotografías como dato",
    ),
    "DOC": (
        "Documentos y textos",
        "#2563eb",
        "documentos, archivos, prensa, discursos, leyes, corpus de texto",
    ),
    "ENC": (
        "Encuestas y datos numéricos",
        "#7c3aed",
        "encuestas, cuestionarios, escalas, Q-sort, rejillas, datos estadísticos",
    ),
    "LIT": (
        "Literatura académica",
        "#65a30d",
        "artículos, libros o estudios previos buscados o seleccionados como fuente",
    ),
    "DIG": (
        "Datos digitales y redes",
        "#0891b2",
        "redes sociales, web, foros, plataformas, registros en línea",
    ),
    "OTR": (
        "Otro dato",
        "#64748b",
        "recolección de un dato que no encaja en los anteriores",
    ),
}

TONOS = {
    "CRI": "Crítico",
    "CIE": "Científico",
    "ART": "Artístico",
    "EVP": "Evaluativo-propositivo",
}
NARR = {"BIO": "Biográfica", "HIS": "Histórica", "CAU": "Causal"}
CONF = {"A": "alta", "M": "media", "B": "baja"}

# =============================================================================
# Esquema JSON — solo se usa para VALIDACIÓN LOCAL del output del modelo.
# NO se envía como response_format a Together, porque el json_schema rompe
# el prefijo cacheado y los aciertos de prompt caching caen a 0.
# =============================================================================
JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["tareas"],
    "properties": {
        "tareas": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "tipo", "datos", "narrativa", "tono", "conf"],
                "properties": {
                    "id": {"type": "integer"},
                    "tipo": {"type": "string", "enum": list(TIPOS)},
                    "datos": {
                        "type": "array",
                        "uniqueItems": True,
                        "maxItems": 4,
                        "items": {"type": "string", "enum": list(DATOS)},
                    },
                    "narrativa": {"type": "string", "enum": ["NA", *NARR]},
                    "tono": {"type": "string", "enum": ["NA", *TONOS]},
                    "conf": {"type": "string", "enum": list(CONF)},
                },
            },
        }
    },
}


# =============================================================================
# SYSTEM PROMPT — constante de módulo, evaluada UNA SOLA VEZ.
# Cualquier cambio aquí invalida la caché: no lo toques entre ejecuciones.
# =============================================================================
def _tabla(grupo: str) -> str:
    return "\n".join(
        f"- {c}: {n}. {d}" for c, (n, g, _, d) in TIPOS.items() if g == grupo
    )


_DATOS_BULLETS = "\n".join(f"  {c}: {d[2]}" for c, d in DATOS.items())

SYSTEM_PROMPT = f"""Eres un codificador experto en metodología de investigación cualitativa. Etiquetas tareas de procedimiento ya extraídas de estudios; NO redactas contenido.

# SALIDA (obligatoria)
Devuelve ÚNICAMENTE un objeto json válido con la forma {{"tareas":[...]}}. Cada elemento usa SOLO códigos de las listas de abajo. Prohibido: texto libre, explicaciones, comentarios, citar o parafrasear las tareas, markdown o claves nuevas. Una entrada por cada id recibido, en el mismo orden, sin omitir ni inventar ids. No envuelvas el JSON en ``` ni en ningún otro bloque.

# ENTRADA
Una línea por tarea: `id|etapa|texto`. Etapas: preparacion, exploracion, analisis, sintesis, adicional. La etapa ya está decidida: no la cambies ni la devuelvas. Usa el nombre del método y las demás tareas solo como contexto.

# CAMPOS POR TAREA
- id: el id recibido (entero).
- tipo: un código, según la etapa (ver tablas). Si ninguno encaja con claridad, "NC".
- datos: tags de TIPO DE DATO. Solo si la tarea es de RECOLECCIÓN (obtener, buscar, seleccionar, reclutar o registrar datos o fuentes: entrevistar, observar, descargar, aplicar un cuestionario, buscar literatura). Si no es recolección, []. Máx. 4 códigos.
{_DATOS_BULLETS}
- narrativa: "BIO", "HIS" o "CAU" solo si tipo=NAR; si no, "NA".
- tono: solo en síntesis (y en adicional si el tipo es de síntesis). "CRI" (cuestiona poder, ideología o desigualdad), "CIE" (describe o explica con evidencia, busca objetividad o generalización), "ART" (forma literaria, poética o creativa), "EVP" (evalúa y propone mejoras, recomendaciones o políticas). Si no hay señal explícita, "NA". En otros casos, "NA".
- conf: "A" (inequívoco), "M" (razonable pero ambiguo), "B" (conjetura).

# TIPOS PARA preparacion y exploracion
{_tabla("prep")}
# TIPOS PARA analisis
{_tabla("ana")}
# TIPOS PARA sintesis
{_tabla("sin")}
# TIPOS PARA adicional
Cualquiera de las tres listas si encaja con claridad; si no, "NC".

# PROCEDIMIENTO (razónalo internamente, no lo escribas)
1. ¿Es recolección? Si sí, completa `datos`.
2. Elige el tipo según el VERBO PRINCIPAL y el PROPÓSITO, no por palabras sueltas.
3. Desempates: ABD > DED > IND en análisis (si crea conceptos, hipótesis o compara iterativamente es ABD aunque también codifique; si aplica categorías de un autor es DED; solo si describe sin teoría previa es IND). ETI gana si hay consentimiento o anonimización. CAL gana si el propósito es evaluar sesgos o límites de los datos. En síntesis, elige la forma de redacción dominante.
4. Una tarea de recolección pura (p. ej., "realizar entrevistas") no encaja en ningún tipo: tipo "NC" con sus `datos`.
5. Ante duda real, "NC" con conf "B". Nunca fuerces un tipo.

# EJEMPLO
Entrada:
MÉTODO: Ejemplo ficticio
N_TAREAS: 6
1|preparacion|Obtener el consentimiento informado y anonimizar los nombres.
2|preparacion|Realizar entrevistas semiestructuradas a 12 docentes.
3|exploracion|Transcribir literalmente las entrevistas.
4|analisis|Codificar de forma inductiva cada transcripción para identificar temas emergentes.
5|analisis|Comparar de forma constante los casos para proponer una tipología de trayectorias.
6|sintesis|Redactar la historia de vida de cada participante en orden cronológico.
Salida:
{{"tareas":[{{"id":1,"tipo":"ETI","datos":[],"narrativa":"NA","tono":"NA","conf":"A"}},{{"id":2,"tipo":"NC","datos":["ENT"],"narrativa":"NA","tono":"NA","conf":"A"}},{{"id":3,"tipo":"CNV","datos":[],"narrativa":"NA","tono":"NA","conf":"A"}},{{"id":4,"tipo":"IND","datos":[],"narrativa":"NA","tono":"NA","conf":"A"}},{{"id":5,"tipo":"ABD","datos":[],"narrativa":"NA","tono":"NA","conf":"A"}},{{"id":6,"tipo":"NAR","datos":[],"narrativa":"BIO","tono":"NA","conf":"M"}}]}}"""


# =============================================================================
# ENTRADA / VALIDACIÓN
# =============================================================================
def tareas_de(m):
    """Tareas planas de un método -> [{id, fase, texto}]."""
    out = [
        {
            "id": p["orden_global"],
            "fase": p["fase"],
            "texto": " ".join(p["paso"].split())[:400],
        }
        for p in (m.get("pasos_procedimentales_lineales") or [])
    ]
    ids = [t["id"] for t in out]
    assert len(ids) == len(set(ids)), f"orden_global repetido en {m['id']}"
    return out


def mensaje_usuario(m, tareas):
    ident = (m.get("metadatos_extraidos") or {}).get("metodo_identificado") or ""
    lin = "\n".join(f"{t['id']}|{ETAPAS[t['fase']][0]}|{t['texto']}" for t in tareas)
    return f"MÉTODO: {m['nombre']} — {ident}\nN_TAREAS: {len(tareas)}\n{lin}"


def validar(resp, tareas):
    """Normaliza la respuesta del modelo y corrige incompatibilidades."""
    por_id = {
        r.get("id"): r for r in (resp or {}).get("tareas", []) if isinstance(r, dict)
    }
    out, fix = {}, 0
    for t in tareas:
        r = por_id.get(t["id"]) or {}
        grupo = ETAPAS[t["fase"]][3]
        tipo = r.get("tipo") if r.get("tipo") in TIPOS else "NC"
        if tipo != "NC" and grupo != "any" and TIPOS[tipo][1] != grupo:
            tipo, fix = "NC", fix + 1
        datos = (
            [d for d in dict.fromkeys(r.get("datos") or []) if d in DATOS]
            if grupo in ("prep", "any")
            else []
        )
        nar = (
            r.get("narrativa") if tipo == "NAR" and r.get("narrativa") in NARR else "NA"
        )
        tono = (
            r.get("tono")
            if TIPOS[tipo][1] == "sin" and r.get("tono") in TONOS
            else "NA"
        )
        conf = r.get("conf") if r.get("conf") in CONF and r else "B"
        out[t["id"]] = {
            "tipo": tipo,
            "datos": datos[:4],
            "narrativa": nar,
            "tono": tono,
            "conf": conf,
        }
    faltantes = sum(1 for t in tareas if t["id"] not in por_id)
    return out, fix, faltantes


# =============================================================================
# DEEPSEEK (Together AI)
# =============================================================================
ENDPOINT = "https://api.together.xyz/v1/chat/completions"
CACHE_KEY = "clasificacion_tareas_v1"  # fijo: agrupa peticiones para reusar prefijo
CACHE_VERSION = "v1"  # súbelo si cambias SYSTEM_PROMPT o TIPOS


def _extract_json(text: str) -> dict:
    """Extrae el primer objeto JSON balanceado del texto (ignora prosa previa)."""
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


CACHE_KEY = "clasificacion_tareas_v1"  # fijo: agrupa peticiones para reusar prefijo
CACHE_VERSION = "v1"  # súbelo si cambias SYSTEM_PROMPT o TIPOS

_client = None


def _get_client(key):
    """Cliente Together reutilizable (una sola instancia por proceso)."""
    global _client
    if _client is None:
        _client = Together(api_key=key)
    return _client


def _extract_json(text: str) -> dict:
    """Extrae el primer objeto JSON balanceado del texto (ignora prosa previa)."""
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
    """Normaliza el objeto usage del SDK a un dict plano."""
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


def llamar(m, tareas, model, key, reintentos=3):
    client = _get_client(key)

    for i in range(reintentos):
        try:
            resp = client.chat.completions.create(
                model=model,
                temperature=0.0,
                top_p=0.95,
                max_tokens=64000,  # sin cambios
                reasoning_effort="high",  # FIJO: parte del prefijo cacheado
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},  # prefijo estable
                    {"role": "user", "content": mensaje_usuario(m, tareas)},
                ],
                # prompt_cache_key va dentro de extra_body: así lo espera el SDK
                extra_body={"prompt_cache_key": CACHE_KEY},
            )

            # -------- Diagnóstico de caché --------
            u = _usage_dict(resp.usage)
            details = u.get("prompt_tokens_details") or {}
            if hasattr(details, "model_dump"):
                details = details.model_dump()
            cached = (details or {}).get("cached_tokens", u.get("cached_tokens", 0))
            total_in = u.get("prompt_tokens", 0) or 0
            pct = (100 * cached / total_in) if total_in else 0
            print(f"  [{m['id']}] prompt={total_in} cached={cached} ({pct:.0f}%)")

            content = resp.choices[0].message.content
            return _extract_json(content)
        except Exception as e:
            print(f"  ! {m['id']} intento {i + 1}: {str(e)[:90]}", file=sys.stderr)
            time.sleep(2 * (i + 1))
    return None


# =============================================================================
# HEURÍSTICA (provisional, sin API)
# =============================================================================
H = {
    "ETI": r"[ée]tic|consentimiento|anonimiz|confidencial|protecci[oó]n de (los )?datos",
    "ADE": r"literatura|marco te[oó]rico|reformul|refin|ajustar|adaptar (el|la|los|las) (gu[ií]a|protocolo|pregunta|dise)|pregunta de investigaci|hip[oó]tesis inicial|estudio piloto|conocimiento previo",
    "CAL": r"sesgo|limitaci[oó]n|saturaci|fiabilidad|confiabilidad|representatividad|calidad de (los )?datos|credibilidad",
    "CNV": r"transcrib|transcripci|digitaliz|\bocr\b|traduc|subtitul|formato|estandariz|importar|segmentar|organizar (el|los) (material|datos|archivos)",
    "ABD": r"hip[oó]tesis|comparaci[oó]n constante|compar|contrast|concepto|conceptualiz|taxonom|tipolog|patr[oó]n|axial|selectiv|caso negativo|iterativ|teoriz",
    "DED": r"a priori|predefinid|deductiv|marco (te[oó]rico|conceptual) de|seg[uú]n (la )?teor|aplicar (el|la) (enfoque|marco|modelo|an[aá]lisis|m[eé]todo|protocolo)|utilizar (el|la) (an[aá]lisis|enfoque|modelo|marco)|categor[ií]as de",
    "IND": r"inductiv|tematiz|par[aá]frasis|parafrase|codificaci[oó]n abierta|codific|temas|etiquet|descri(bir|pci)|agrup|resumir|identificar (categor|unidades|fragmentos)|leer|segment",
    "MEM": r"\bmemo",
    "CMP": r"compar|contrast",
    "NAR": r"narrativ|historia de|biogr|relato|cronolog|trayectoria",
    "DIA": r"diagrama|mapa|red de|esquema visual|grafo|visualiz|figura",
    "ESQ": r"teor[ií]a|modelo (te[oó]rico|explicativo|conceptual)|proposiciones|marco conceptual|construir (un|el) modelo",
    "ARG": r"discut|discusi|implicaci|conclu|limitaciones del estudio",
    "CAS": r"caso|tema|subtema|resultados|hallazgos|describir|informe",
}
H_DATOS = {
    "ENT": r"entrevist|grupo[s]? focal|testimon|relatos",
    "OBS": r"observaci|etnograf|notas de campo|diario de campo|trabajo de campo",
    "AVI": r"v[ií]deo|audio|grabaci|fotograf",
    "DOC": r"document|archivo|prensa|discurso|corpus|textos?\b|carta",
    "ENC": r"encuesta|cuestionario|escala|estad[ií]st|q-sort|rejilla",
    "LIT": r"literatura|art[ií]culos|estudios previos|bibliogr",
    "DIG": r"redes sociales|en l[ií]nea|online|\bweb\b|plataforma|internet",
}
H_RECOL = r"recolect|recopil|obten|reclut|seleccion|entrevist|grab|registr|observ|encuesta|cuestionario|muestre|recoger|solicitar|acceder|descargar|buscar|b[uú]squeda|extraer (datos|tweets|textos)"
H_TONO = {
    "CRI": r"cr[ií]tic|poder|ideolog|desigualdad|emancip",
    "EVP": r"recomend|propuesta|propon|mejora|intervenci[oó]n|pol[ií]tica",
    "ART": r"po[eé]tic|art[ií]stic|creativ|literari",
    "CIE": r"emp[ií]ric|cient[ií]fic|replic|generaliz|objetiv",
}
ORDEN = {
    "prep": ["ETI", "CAL", "CNV", "ADE"],
    "ana": ["ABD", "DED", "IND"],
    "sin": ["MEM", "CMP", "NAR", "DIA", "ESQ", "ARG", "CAS"],
    "any": [
        "ETI",
        "CAL",
        "CNV",
        "ABD",
        "DED",
        "IND",
        "MEM",
        "CMP",
        "NAR",
        "DIA",
        "ESQ",
        "ARG",
    ],
}


def heuristica(tareas):
    out = {}
    for t in tareas:
        x, g = t["texto"].lower(), ETAPAS[t["fase"]][3]
        tipo = next((c for c in ORDEN[g] if re.search(H[c], x)), "NC")
        datos = []
        if g in ("prep", "any") and re.search(H_RECOL, x):
            datos = [k for k, p in H_DATOS.items() if re.search(p, x)][:4] or ["OTR"]
        nar = (
            (
                "BIO"
                if re.search(r"biogr|vida", x)
                else "CAU"
                if re.search(r"caus", x)
                else "HIS"
            )
            if tipo == "NAR"
            else "NA"
        )
        tono = (
            next((k for k, p in H_TONO.items() if re.search(p, x)), "NA")
            if TIPOS[tipo][1] == "sin"
            else "NA"
        )
        out[t["id"]] = {
            "tipo": tipo,
            "datos": datos,
            "narrativa": nar,
            "tono": tono,
            "conf": "B" if tipo == "NC" else "M",
        }
    return out


# =============================================================================
# CLI / ORQUESTACIÓN
# =============================================================================
def exportar_prompt(destino):
    Path(destino).write_text(
        "# Prompt de clasificación de tareas\n\n## System\n\n```\n"
        + SYSTEM_PROMPT
        + "\n```\n\n## User (plantilla)\n\n```\n"
        "MÉTODO: <nombre> — <metodo_identificado>\nN_TAREAS: <n>\n<id>|<etapa>|<texto>\n...\n```\n\n"
        "## response_format\n\n```json\n"
        + json.dumps({"type": "json_object"}, ensure_ascii=False, indent=1)
        + "\n```\n\n## Esquema (validación local, no enviado)\n\n```json\n"
        + json.dumps(JSON_SCHEMA, ensure_ascii=False, indent=1)
        + "\n```\n",
        encoding="utf-8",
    )
    print("escrito", destino)


def clasificar(
    root, heur=False, model="deepseek-ai/DeepSeek-V4-Pro-0813", solo=None, forzar=False
):
    d = Path(root) / "data"
    metodos = json.load(open(d / "metodos_completos.json", encoding="utf-8"))
    ruta = d / "clasificacion_tareas.json"
    prev = json.load(open(ruta, encoding="utf-8")) if ruta.exists() else {"metodos": {}}
    if forzar:
        if solo:
            # --forzar + --solo: re-clasificar solo los indicados, conservar el resto
            for mid in solo:
                prev.setdefault("metodos", {}).pop(mid, None)
        else:
            prev = {"metodos": {}}
    key = ""
    if not heur and not key:
        sys.exit("Falta TOGETHER_API_KEY en el entorno.")

    res = {
        "fuente": "heuristica" if heur else model,
        "metodos": {} if heur else dict(prev.get("metodos", {})),
    }
    if not heur and prev.get("fuente") == "heuristica":
        res["metodos"] = {}

    for m in metodos:
        if solo and m["id"] not in solo:
            continue
        tareas = tareas_de(m)
        if not tareas or (m["id"] in res["metodos"] and not heur):
            continue

        if heur:
            lim = heuristica(tareas)
            fix = falt = 0
        else:
            resp = llamar(m, tareas, model, key)
            if resp is None:
                print(f"  ! {m['id']}: sin respuesta válida, se omite", file=sys.stderr)
                continue
            lim, fix, falt = validar(resp, tareas)

        res["metodos"][m["id"]] = {str(k): v for k, v in lim.items()}
        print(f"{m['id']}: {len(tareas)} tareas | corregidas {fix} | faltantes {falt}")
        json.dump(res, open(ruta, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    json.dump(res, open(ruta, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("->", ruta, "| fuente:", res["fuente"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Clasifica tareas por etapa con DeepSeek vía Together AI."
    )
    ap.add_argument("--root", default="scrape_out")
    ap.add_argument("--heuristica", action="store_true")
    ap.add_argument("--model", default="deepseek-ai/DeepSeek-V4-Pro-0813")
    ap.add_argument(
        "--solo", default="", help="IDs separados por coma (procesa solo esos métodos)."
    )
    ap.add_argument("--forzar", action="store_true")
    ap.add_argument("--exportar-prompt", action="store_true")
    a = ap.parse_args()

    if a.exportar_prompt:
        exportar_prompt("prompt_clasificacion.md")
    else:
        clasificar(
            a.root,
            a.heuristica,
            a.model,
            set(filter(None, a.solo.split(","))) or None,
            a.forzar,
        )
