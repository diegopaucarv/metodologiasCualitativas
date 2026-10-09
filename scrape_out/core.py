"""Núcleo compartido de la aplicación.

Contiene la lógica de dominio (clasificación de pasos, detección de entradas,
construcción del modelo de métodos) que consumen tanto la CLI como el frontend
HTML. No depende de la interfaz: solo lee los datos de ``data/`` y produce
estructuras JSON listas para presentar.

Uso:
    from core import load_data, build, leyenda
"""

import collections
import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import unquote

# id: (nombre, tendencia, color, patrones). Verbos genéricos (realizar, aplicar...) no puntúan: manda el objeto.
EST = {
    "fundamentar": (
        "Fundamentación y diseño",
        "preparacion",
        "#8b7be8",
        r"abord|defin|establec|dise[ñn]|formul|delimit|plantear|justific|fundament|marco te|pregunta de inv|objetivo",
    ),
    "recoleccion": (
        "Recolección y muestreo",
        "preparacion",
        "#b08cf0",
        r"selec+ion|reclut|recolect|recopil|obten|solicit|grab|registr|busc|muestr|acced|contact|entrevist",
    ),
    "preparacion": (
        "Preparación del material",
        "preparacion",
        "#c9b0f5",
        r"transcrib|organiz|orden|compil|depur|anonim|digitaliz|archiv|prepar|tradu|fichas|base de datos",
    ),
    "inmersion": (
        "Lectura y exploración",
        "exploracion",
        "#22b8cf",
        r"\bleer|lectur|revis|explor|inspecc|familiariz|examin|inmersi|observ|escuch|visualiz|repetid",
    ),
    "codificacion": (
        "Codificación y categorías",
        "analisis",
        "#4f7cf0",
        r"codific|etiquet|identific|reconoc\w* (?:patron|tema|categor|c[oó]digo)|clasific|agrup|categoriz|segment|marc|anot|extra",
    ),
    "comparacion": (
        "Comparación y contraste",
        "analisis",
        "#6d63f2",
        r"compar|contrast|relacion|diferenci|constante|cotej",
    ),
    "interpretacion": (
        "Análisis e interpretación",
        "analisis",
        "#2f5bd6",
        r"analiz|interpret|calcul|emple|evalu|explic|modelar|mape",
    ),
    "integracion": (
        "Integración y teorización",
        "sintesis",
        "#ec4899",
        r"constru|integr|sintet|propon|desarroll|generar|resum|condens|conceptual|reconstru|teori|modelo|refin|articul",
    ),
    "redaccion": (
        "Redacción y reporte",
        "sintesis",
        "#f59e0b",
        r"redact|elabor|present|discut|conclu|describ|document|produc|report|escrib|narr|informe",
    ),
    "validacion": (
        "Validación y rigor",
        "rigor",
        "#10b981",
        r"valid|verific|reconoc\w* (?:la )?(?:participaci|posici|rol |sesgo|subjetiv)|triangul|member|saturaci|mantener|reflexion|rigor|confiab|credib|audit|pares",
    ),
}
GENERICOS = (
    "realiz",
    "aplic",
    "utiliz",
    "llev",
    "emple",
    "incluir",
    "adopt",
    "consider",
    "tomar",
    "continu",
)
PRIOR = {
    "pre_analisis": ("fundamentar", "recoleccion", "preparacion"),
    "interpretacion_fuentes": ("inmersion", "preparacion"),
    "analisis_profundo": ("codificacion", "comparacion", "interpretacion"),
    "sintesis": ("integracion", "redaccion"),
    "pasos_adicionales": ("validacion",),
}
FASES = [
    ("pre_analisis", "Pre-análisis"),
    ("interpretacion_fuentes", "Exploración"),
    ("analisis_profundo", "Análisis"),
    ("sintesis", "Síntesis"),
    ("pasos_adicionales", "Rigor y pasos adicionales"),
]
ENT = {  # tipos de dato de entrada
    "entrevistas": (
        "Entrevistas y grupos",
        "#e11d74",
        r"entrevist|grupo focal|grupos focales|focus|testimon|relatos",
    ),
    "observacion": (
        "Observación y campo",
        "#0d9488",
        r"observaci|etnograf|notas de campo|diario de campo|trabajo de campo",
    ),
    "audiovisual": (
        "Audio y video",
        "#ea580c",
        r"video|v[ií]deo|audio|grabaci|fotograf|imagen|fotogram",
    ),
    "documentos": (
        "Documentos y textos",
        "#2563eb",
        r"document|archiv|corpus|prensa|discurso|texto|carta|libro|memorand|informes",
    ),
    "cuantitativos": (
        "Encuestas y datos numéricos",
        "#7c3aed",
        r"encuesta|cuestionario|escala|estad[ií]st|q-sort|rejilla|matriz|frecuenc|datos cuantit|puntaj",
    ),
    "literatura": (
        "Literatura previa",
        "#65a30d",
        r"literatura|art[ií]culos|estudios previos|marco te|revisi[oó]n bibliogr|bibliograf",
    ),
    "digital": (
        "Datos digitales y redes",
        "#0891b2",
        r"redes sociales|en l[ií]nea|online|plataforma|\bweb\b|twitter|datos digitales|internet",
    ),
}


def stem_hit(p, w):
    return re.match(r"(?:" + p + ")", w) is not None


def clasificar(texto, fase):
    t = texto.lower()
    w = re.findall(r"[a-záéíóúñ\-]+", t)
    first = w[0] if w else ""
    sc = {k: 0.0 for k in EST}
    for k, (_, _, _, pat) in EST.items():
        if not any(first.startswith(g) for g in GENERICOS) and stem_hit(pat, first):
            sc[k] += 4
        sc[k] += min(2, len(re.findall(pat, " ".join(w[1:16]))) * 0.7)
        if k in PRIOR.get(fase, ()):
            sc[k] += 1.5
    return max(sc, key=sc.get)


def entradas(texto):
    return [k for k, (_, _, p) in ENT.items() if re.search(p, texto.lower())]


def norm_name(s):
    s = unquote(s)
    s = unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode()
    return s.lower()


def normalize_name(s):
    """Normaliza un nombre para archivo: decodifica URL, quita acentos,
    minúsculas y espacios → guiones bajos. Coincide con la convención de GitHub."""
    return re.sub(r"\s+", "_", norm_name(s))


def doi_cabecera(txt):
    m = re.search(r"10\.\d{4,9}/[^\s\"<>,;)\]]+", txt[:6000])
    return m.group(0).rstrip(".") if m else None


def autores(a):
    a = a or []
    return ", ".join(a) if len(a) <= 2 else f"{a[0]} et al."


def load_data(data_dir):
    """Carga los tres archivos de datos y devuelve (metodos, refs, pdft)."""
    metodos = json.load(open(data_dir / "metodos_completos.json", encoding="utf-8"))
    refs = json.load(open(data_dir / "pdf_refs.json", encoding="utf-8"))
    pdft = json.load(open(data_dir / "pdf_texts.json", encoding="utf-8"))
    return metodos, refs, pdft


def build(metodos, refs, pdft):
    """Construye el modelo de métodos listo para presentar (CLI o frontend)."""
    out = []
    for m in metodos:
        me = m["metadatos_extraidos"]
        ref = (refs.get(m["id"]) or [{}])[0]
        txt = ((pdft.get(m["id"]) or [{}])[0]).get("text", "")
        if ref.get("url"):
            url, origen = ref["url"], "documento descargado"
        elif d := doi_cabecera(txt):
            url, origen = "https://doi.org/" + d, "DOI detectado en el PDF aportado"
        else:
            url, origen = None, "PDF aportado manualmente, sin enlace"
        fases, cnt, ent_c, tops = (
            [],
            collections.Counter(),
            collections.Counter(),
            collections.defaultdict(collections.Counter),
        )
        lin = m.get("pasos_procedimentales_lineales") or []
        for fid, fnom in FASES:
            pasos = []
            for p in [x for x in lin if x["fase"] == fid]:
                e = clasificar(p["paso"], fid)
                en = entradas(p["paso"])
                pasos.append({"n": p["orden_global"], "t": p["paso"], "e": e, "i": en})
                cnt[EST[e][1]] += 1
                tops[EST[e][1]][e] += 1
                if fid in ("pre_analisis", "interpretacion_fuentes"):
                    ent_c.update(en)
            if pasos:
                fases.append({"id": fid, "nombre": fnom, "pasos": pasos})
        if not ent_c:
            [ent_c.update(p["i"]) for f in fases for p in f["pasos"]]
        ents = [{"id": k, "n": n} for k, n in ent_c.most_common(4)]
        n = sum(cnt.values())

        def top(t):
            return EST[tops[t].most_common(1)[0][0]][0].lower() if tops[t] else None

        desc = f"{me['metodo_identificado']}, aplicado en «{me['titulo']}» ({autores(me.get('autores'))}{', ' + str(me['anio_publicacion']) if me.get('anio_publicacion') else ''})."
        if n:
            desc += (
                f" El recorrido reconstruido tiene {n} pasos: {cnt['preparacion']} de preparación, {cnt['exploracion']} de exploración, "
                f"{cnt['analisis']} de análisis, {cnt['sintesis']} de síntesis y {cnt['rigor']} de rigor."
            )
            if ents:
                desc += (
                    " Datos de entrada: "
                    + ", ".join(ENT[e["id"]][0].lower() for e in ents[:3])
                    + "."
                )
            if top("analisis"):
                desc += (
                    f" En el análisis predominan las estrategias de {top('analisis')}"
                )
            if top("sintesis"):
                desc += f"; la síntesis se apoya sobre todo en {top('sintesis')}."
            elif top("analisis"):
                desc += "."
        else:
            desc += " El texto disponible no permitió reconstruir los pasos del procedimiento."
        out.append(
            {
                "id": m["id"],
                "nombre": m["nombre"],
                "familia": m["familia"],
                "categoria": m["categoria"],
                "subcategoria": m["subcategoria"],
                "ideal_para": m["ideal_para"],
                "descripcion": desc,
                "imagen": m.get("imagen"),
                "estudio": {
                    "titulo": me["titulo"],
                    "autores": me.get("autores") or [],
                    "anio": me.get("anio_publicacion"),
                    "tipo": me.get("tipo_documento"),
                    "url": url,
                    "origen_enlace": origen,
                    "confianza": me["confianza"],
                    "notas": me.get("notas_limitaciones"),
                    "metodo_identificado": me["metodo_identificado"],
                },
                "resumen": {
                    "pasos": n,
                    **{
                        k: cnt[k]
                        for k in (
                            "preparacion",
                            "exploracion",
                            "analisis",
                            "sintesis",
                            "rigor",
                        )
                    },
                },
                "entradas": ents,
                "fases": fases,
            }
        )
    return out


def leyenda():
    return {
        "estrategias": {
            k: {"nombre": v[0], "tendencia": v[1], "color": v[2]}
            for k, v in EST.items()
        },
        "entradas": {k: {"nombre": v[0], "color": v[1]} for k, v in ENT.items()},
    }
