#!/usr/bin/env python3
"""
Scraping sistemático de https://v0-sunburst-diagram-webpage.vercel.app/

La web es una app Next.js que pinta los métodos en el navegador, por eso un GET simple
solo devuelve la portada. Este script usa un navegador real (Playwright) y hace:

  crawl   Renderiza cada ruta, guarda HTML/screenshot/assets/respuestas de red, extrae el payload
          de Next.js (__next_f / __NEXT_DATA__), y recorre con hover y clic cada elemento
          interactivo (arcos del sunburst, tarjetas, botones) registrando qué texto aparece.
          Genera: site/, data/interactions.json, data/candidates.json, sitemap.json y sitemap.xml
  extract Lee los chunks JS descargados y saca la base de datos completa de los 48 métodos
          (familia > categoría > subcategoría > método, con descripción, caso, artículo y hallazgos).
  papers  Para cada método de data/methods.json busca artículos en OpenAlex (gratis, sin clave).
  build   Une todo en data/content.json (cuatro fases con color y animación) y empaqueta el zip.
  all     crawl + extract + papers + build

Uso:
  pip install playwright requests && playwright install chromium
  python scrape_metodologias.py crawl
  python scrape_metodologias.py extract
  python scrape_metodologias.py papers --mailto tu@correo.org
  python scrape_metodologias.py build
"""

import argparse
import asyncio
import hashlib
import json
import re
import sys
import time
import zipfile
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse

# Importa las utilidades de normalización compartidas con la app (core.py).
sys.path.insert(0, str(Path(__file__).resolve().parent / "scrape_out"))
from core import norm_name, normalize_name

BASE = "https://v0-sunburst-diagram-webpage.vercel.app/"
OUT = Path("scrape_out")

PHASES = [
    {
        "id": "pre",
        "nombre": "1. Pre-análisis",
        "color": "#4B2A82",
        "objetivo": "No perder información mientras sea posible.",
        "tareas": [
            "Elaborar fichas",
            "Organizar base de datos y metadata",
            "Recolectar datos faltantes",
            "Registrar información fácil de olvidar o perder",
        ],
    },
    {
        "id": "exp",
        "nombre": "2. Exploración",
        "color": "#6C3FB5",
        "objetivo": "Poner los datos en contexto; puede ser una investigación en sí misma.",
        "tareas": [
            "Validar calidad: sesgos, intertextualidades, validación con participantes",
            "Preguntas de adecuación: contexto que desafía el problema",
            "Procesos y tipologías desde metáforas u otros estudios",
        ],
    },
    {
        "id": "ana",
        "nombre": "3. Análisis",
        "color": "#B26EF2",
        "objetivo": "Análisis profundo de los datos.",
        "tareas": [
            "Codificación deductiva o inductiva",
            "Comparación constante y conceptualización",
            "Construcción y verificación iterativa de hipótesis",
            "Apoyo cuantitativo",
        ],
    },
    {
        "id": "sin",
        "nombre": "4. Síntesis",
        "color": "#D2A8FF",
        "objetivo": "Redacción iterativa bajo distintos estilos.",
        "tareas": [
            "Narrativo, comparativo, temático",
            "Teoría fundamentada, síntesis de investigación",
        ],
    },
]


def slug(s):
    s = re.sub(
        r"[^a-z0-9]+",
        "-",
        s.lower()
        .replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
        .replace("ñ", "n"),
    )
    return s.strip("-") or "root"


def asset_path(url):
    u = urlparse(url)
    p = u.path.lstrip("/") or "index"
    if p.endswith("/"):
        p += "index"
    if u.query:
        p += "." + hashlib.md5(u.query.encode()).hexdigest()[:8]
    # Normaliza el nombre base (decodifica URL y quita acentos) para que
    # coincida con los nombres guardados en GitHub (sin acentos).
    parts = p.rsplit("/", 1)
    parts[-1] = norm_name(parts[-1])
    return "/".join(parts)


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


# ----------------------------------------------------------------------------- CRAWL
JS_DESCRIBE = """el => ({
  tag: el.tagName.toLowerCase(),
  text: (el.innerText || el.textContent || '').trim().slice(0, 120),
  aria: el.getAttribute('aria-label') || '',
  title: (el.querySelector && el.querySelector('title') ? el.querySelector('title').textContent : el.getAttribute('title')) || '',
  href: el.getAttribute('href') || '',
  data: Object.fromEntries([...el.attributes].filter(a => a.name.startsWith('data-')).map(a => [a.name, a.value])),
  fill: el.getAttribute('fill') || ''
})"""
CLICKABLE = (
    "a[href], button, [role=button], [role=tab], [role=link], [onclick], [tabindex], "
    ".cursor-pointer, svg path, svg g[class], svg circle"
)


async def visible_lines(page):
    txt = await page.evaluate("document.body.innerText")
    return [l.strip() for l in txt.splitlines() if l.strip()]


async def explore(page, url, max_elems):
    """Hover y clic en cada elemento interactivo; guarda las líneas de texto nuevas que aparecen."""
    results, links = [], set()
    await page.goto(url, wait_until="networkidle")
    n = min(await page.locator(CLICKABLE).count(), max_elems)
    for i in range(n):
        for action in ("hover", "click"):
            try:
                await page.goto(url, wait_until="networkidle")
                base = set(await visible_lines(page))
                el = page.locator(CLICKABLE).nth(i)
                desc = await el.evaluate(JS_DESCRIBE)
                await getattr(el, action)(timeout=1500, force=True)
                await page.wait_for_timeout(350)
                after = await visible_lines(page)
                new = [l for l in after if l not in base]
                tip = await page.locator("[role=tooltip]").all_inner_texts()
                if page.url != url:
                    links.add(urldefrag(page.url)[0])
                if new or tip:
                    results.append(
                        {
                            "page": url,
                            "index": i,
                            "action": action,
                            "element": desc,
                            "new_text": new,
                            "tooltips": tip,
                            "url_after": page.url,
                        }
                    )
            except Exception:
                continue
    return results, links


async def load_state(page, url, path):
    """Recarga la página y repite la ruta de clics (lista de índices) para volver a un estado interno."""
    await page.goto(url, wait_until="networkidle")
    for i in path:
        await page.locator(CLICKABLE).nth(i).click(timeout=2500, force=True)
        await page.wait_for_timeout(450)


async def explore_tree(page, url, depth, max_states, max_elems):
    """Recorre en anchura los estados de la app: cada clic que cambia el texto visible es un nodo nuevo
    y se explora a su vez hasta `depth` niveles. Así se llega a vistas que no tienen URL propia."""
    nodes, seen_sig, queue, states = [], set(), [[]], 0
    while queue and states < max_states:
        path = queue.pop(0)
        states += 1
        try:
            await load_state(page, url, path)
        except Exception:
            continue
        base_lines = await visible_lines(page)
        n = min(await page.locator(CLICKABLE).count(), max_elems)
        print(
            f"  estado {states} (ruta {path}): {n} elementos, {len(nodes)} nodos hasta ahora",
            flush=True,
        )
        for i in range(n):
            try:
                if i:
                    await load_state(page, url, path)
                el = page.locator(CLICKABLE).nth(i)
                desc = await el.evaluate(JS_DESCRIBE)
                await el.click(timeout=1500, force=True)
                await page.wait_for_timeout(450)
                lines = await visible_lines(page)
            except Exception:
                continue
            sig = hashlib.md5("\n".join(lines).encode()).hexdigest()
            if lines == base_lines or sig in seen_sig:
                continue
            seen_sig.add(sig)
            nodes.append(
                {
                    "path": path + [i],
                    "depth": len(path) + 1,
                    "element": desc,
                    "new_text": [l for l in lines if l not in base_lines],
                    "url": page.url,
                }
            )
            if len(path) + 1 < depth:
                queue.append(path + [i])
    return nodes


async def crawl(base, out, max_pages, max_elems, depth=4, max_states=120):
    from playwright.async_api import async_playwright

    host = urlparse(base).netloc
    site = out / "site"
    site.mkdir(parents=True, exist_ok=True)
    pages, interactions, edges, tree = {}, [], [], []
    queue, seen = [base], set()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900}, locale="es-PE"
        )

        async def on_response(resp):
            if (
                urlparse(resp.url).netloc != host
                or resp.request.resource_type == "document"
            ):
                return
            try:
                body = await resp.body()
                f = site / "assets" / asset_path(resp.url)
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(body)
            except Exception:
                pass

        # Re-descarga las imágenes con nombres normalizados (sin acentos):
        # intercepta la petición y reescribe la URL a la versión sin acentos,
        # que es como están guardadas en GitHub.
        async def on_route(route):
            req = route.request
            u = urlparse(req.url)
            if u.netloc == host and u.path.lower().endswith(
                (".png", ".jpg", ".jpeg", ".webp", ".gif")
            ):
                parts = u.path.rsplit("/", 1)
                parts[-1] = norm_name(parts[-1])
                new_path = "/".join(parts)
                if new_path != u.path:
                    await route.continue_(url=urlunparse(u._replace(path=new_path)))
                    return
            await route.continue_()

        ctx.on("response", lambda r: asyncio.ensure_future(on_response(r)))
        await ctx.route("**/*", on_route)

        while queue and len(seen) < max_pages:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            page = await ctx.new_page()
            try:
                resp = await page.goto(url, wait_until="networkidle", timeout=45000)
            except Exception as e:
                pages[url] = {"error": str(e)}
                await page.close()
                continue
            name = slug(urlparse(url).path)
            html = await page.content()
            (site / f"{name}.html").write_text(html, encoding="utf-8")
            await page.screenshot(path=str(site / f"{name}.png"), full_page=True)
            payload = await page.evaluate("(self.__next_f||[]).map(x=>x[1]).join('')")
            nd = await page.evaluate(
                "document.getElementById('__NEXT_DATA__') ? document.getElementById('__NEXT_DATA__').textContent : ''"
            )
            (site / f"{name}.next_payload.txt").write_text(
                (payload or "") + "\n" + (nd or ""), encoding="utf-8"
            )
            anchors = await page.eval_on_selector_all(
                "a[href]", "els => els.map(e => e.href)"
            )
            internal = {urldefrag(a)[0] for a in anchors if urlparse(a).netloc == host}
            pages[url] = {
                "status": resp.status if resp else None,
                "title": await page.title(),
                "file": f"{name}.html",
                "links": sorted(internal),
            }
            edges += [(url, l) for l in internal]
            await page.close()

            page = await ctx.new_page()
            found, new_links = await explore(page, url, max_elems)
            await page.close()
            interactions += found
            if url == base:
                page = await ctx.new_page()
                tree = await explore_tree(page, url, depth, max_states, max_elems)
                await page.close()
            for l in internal | new_links:
                if urlparse(l).netloc == host and l not in seen:
                    queue.append(l)
        await browser.close()

    data = out / "data"
    save_json(data / "interactions.json", interactions)
    save_json(data / "tree.json", tree)
    # Candidatos a nombres de método: líneas de texto cortas que solo aparecen tras interactuar
    cand = {}
    for it in interactions:
        for line in it["new_text"] + it["tooltips"]:
            if 3 <= len(line) <= 90:
                cand.setdefault(line, set()).add(it["page"])
    for nd in tree:
        for line in nd["new_text"] + [
            nd["element"]["text"],
            nd["element"]["aria"],
            nd["element"]["title"],
        ]:
            if line and 3 <= len(line) <= 90:
                cand.setdefault(line, set()).add(f"nivel {nd['depth']}")
    save_json(
        data / "candidates.json",
        [{"texto": k, "paginas": sorted(v)} for k, v in sorted(cand.items())],
    )
    save_json(out / "sitemap.json", {"base": base, "pages": pages})
    urls = "\n".join(f"  <url><loc>{u}</loc></url>" for u in pages)
    (out / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}\n</urlset>\n',
        encoding="utf-8",
    )
    print(
        f"{len(pages)} páginas, {len(interactions)} interacciones con cambios, {len(tree)} nodos en tree.json, {len(cand)} textos candidatos"
    )


# ----------------------------------------------------------------------------- EXTRACT
def _match_brace(s, start):
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
        elif c in "\"'`":
            q = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("llaves sin cerrar")


def js_to_json(lit):
    """Convierte un literal de objeto JS (claves sin comillas, comillas simples) a Python, sin Node."""
    out, i, n = [], 0, len(lit)
    while i < n:
        c = lit[i]
        if c in "\"'":
            j, buf = i + 1, []
            while lit[j] != c:
                if lit[j] == "\\":
                    nx = lit[j + 1]
                    if nx == "u":
                        buf.append(chr(int(lit[j + 2 : j + 6], 16)))
                        j += 6
                        continue
                    buf.append({"n": "\n", "t": "\t", "r": "\r"}.get(nx, nx))
                    j += 2
                    continue
                buf.append(lit[j])
                j += 1
            txt = (
                "".join(buf)
                .encode("utf-16", "surrogatepass")
                .decode("utf-16", "replace")
            )
            out.append(json.dumps(txt, ensure_ascii=False))
            i = j + 1
        elif c.isalpha() or c == "_":
            j = i
            while j < n and (lit[j].isalnum() or lit[j] == "_"):
                j += 1
            out.append(json.dumps(lit[i:j]) if lit[j : j + 1] == ":" else lit[i:j])
            i = j
        else:
            out.append(c)
            i += 1
    return json.loads("".join(out))


def extract(out):
    """Busca en los chunks el objeto {"ANALISIS INTERNO": {...}, "ANALISIS FORMAL": {...}} y lo vuelca a JSON."""
    chunks = (out / "site" / "assets" / "_next" / "static" / "chunks").glob("*.js")
    raw = None
    for f in chunks:
        txt = f.read_text(encoding="utf-8", errors="ignore")
        k = txt.find('"ANALISIS INTERNO"')
        if k < 0:
            continue
        start = txt.rfind("{", 0, k)
        raw = js_to_json(txt[start : _match_brace(txt, start) + 1])
        break
    if raw is None:
        raise SystemExit(
            "No encontré la base de datos en los chunks; revisa site/assets/_next/static/chunks"
        )
    save_json(out / "data" / "methods_raw.json", raw)
    imgs = {
        norm_name(p.name): norm_name(p.name)
        for p in (out / "site" / "assets" / "images").glob("*.png")
    }
    methods = []
    for fam, cats in raw.items():
        for cat, subs in cats.items():
            for sub, items in subs.items():
                for m in items:
                    nombre = m["metodo"]
                    img = imgs.get(normalize_name(nombre) + ".png")
                    methods.append(
                        {
                            "id": slug(nombre),
                            "nombre": nombre,
                            "familia": fam.title(),
                            "categoria": cat,
                            "subcategoria": sub,
                            "descripcion_caso": m.get("descripcion"),
                            "ideal_para": m.get("good_for"),
                            "ejemplo": {
                                "titulo": m.get("ejemplo_aplicacion"),
                                "url": m.get("url"),
                            },
                            "hallazgos": m.get("resumen_hallazgos"),
                            "image_prompt": m.get("image_prompt"),
                            "imagen": f"site/assets/images/{img}" if img else None,
                        }
                    )
    save_json(out / "data" / "methods.json", methods)
    print(f"{len(methods)} métodos extraídos")


# ----------------------------------------------------------------------------- PAPERS
# Nombre canónico en inglés de cada método (la literatura casi siempre lo usa así). Editable en data/queries.json.
# Los marcados en INCIERTOS tienen un nombre ambiguo en la web: revisa que la traducción sea la que quieres.
QUERIES = {
    "Etnometodologia": ["ethnomethodology"],
    "Hermeneutica objetiva": ["objective hermeneutics"],
    "Analisis de contenido cualitativo": ["qualitative content analysis"],
    "Analisis de practicas": ["analysis of practices", "practice theory habitus"],
    "Fenomenografia": ["phenomenography"],
    "Analisis de la conversacion": ["conversation analysis"],
    "Analisis narrativo dialogico": ["dialogical narrative analysis"],
    "Analisis narrativo performativo": ["performative narrative analysis"],
    "Tematizacion": ["thematic analysis", "thematic synthesis"],
    "Descripcion interpretativa": ["interpretive description"],
    "Teoria fundamentada straussiana": [
        "Straussian grounded theory",
        "Strauss and Corbin grounded theory",
    ],
    "Teoria fundamentada constructivista o Analisis situacional": [
        "constructivist grounded theory",
        "situational analysis",
    ],
    "Análisis fenomenológico interpretativo": [
        "interpretative phenomenological analysis"
    ],
    "Analisis narrativo": ["narrative analysis", "narrative inquiry"],
    "Analisis psicoanalitico": ["psychoanalytic interpretation qualitative research"],
    "Analisis tematico reflexivo": ["reflexive thematic analysis"],
    "Analisis basado en giros post": [
        "poststructural analysis",
        "poststructuralist discourse analysis",
    ],
    "Analisis del discurso": ["discourse analysis"],
    "Analisis del discurso critico": ["critical discourse analysis"],
    "Codificacion OCM": ["Outline of Cultural Materials", "Human Relations Area Files"],
    "Analisis funcional": ["functional analysis sociology", "structural functionalism"],
    "Analisis de redes sociales cualitativo": ["qualitative social network analysis"],
    "Analisis sistemico": ["systems theory Luhmann", "social systems analysis"],
    "Analisis organizacional o institucional": [
        "organizational ethnography",
        "institutional analysis",
    ],
    "Analisis de metaforas": ["metaphor analysis"],
    "Analisis de recursos": ["rhetorical analysis", "rhetorical resources"],
    "Modelos mentales": ["mental models elicitation", "mental model approach"],
    "Induccion analitica": ["analytic induction"],
    "Teoria fundamentada clasica": [
        "classic grounded theory",
        "Glaserian grounded theory",
    ],
    "Metodo Gioia": ["Gioia methodology", "Gioia method"],
    "Systematic combining": ["systematic combining"],
    "Enfoque de Construcción de Teoría desde Casos (Eisenhardt)": [
        "building theories from case study research"
    ],
    "Teorización Centrada en el Diseño (Stigliani, Corley & Gioia)": [
        "design-centered theorizing"
    ],
    "Analisis cualitativo comparativo": ["qualitative comparative analysis"],
    "Process tracing": ["process tracing"],
    "Event Structure Analysis": ["event structure analysis"],
    "Análisis cualitativo longitudinal": ["qualitative longitudinal research"],
    "Estrategias de Teorización de Procesos (Langley)": [
        "theorizing from process data"
    ],
    "Dominios culturales": ["cultural domain analysis", "free listing"],
    "Metodologia Q": ["Q methodology"],
    "Analisis de marco": ["frame analysis"],
    "Analisis logico": ["argument analysis", "logical reconstruction of arguments"],
    "Analisis narrativo funcional": [
        "structural narrative analysis",
        "functional narrative analysis",
    ],
    "Análisis Pragmático Cultural": ["cultural pragmatics"],
    "Tecnica del incidente critico": ["critical incident technique"],
    "Repertory grid": ["repertory grid"],
    "Antropologia comparada": ["comparative anthropology", "cross-cultural comparison"],
    "Historia comparada": ["comparative historical analysis"],
}
INCIERTOS = {
    "Analisis de practicas",
    "Analisis basado en giros post",
    "Analisis funcional",
    "Analisis sistemico",
    "Analisis organizacional o institucional",
    "Analisis de recursos",
    "Analisis logico",
    "Análisis Pragmático Cultural",
    "Antropologia comparada",
    "Analisis psicoanalitico",
}
PROC_WORDS = (
    "procedure",
    "steps",
    "step-by-step",
    "guide",
    "tutorial",
    "how to",
    "introduction",
    "handbook",
    "approach",
    "method",
    "using",
    "applying",
)


def _openalex(params):
    import requests

    for t in range(4):
        r = requests.get("https://api.openalex.org/works", params=params, timeout=40)
        if r.status_code in (429, 500, 502, 503):
            time.sleep(2 * (t + 1))
            continue
        break
    if not r.ok:
        print(f"    ! HTTP {r.status_code}: {r.text[:140]}")
        return None
    return r.json().get("results", [])


def fetch_papers(out, per, mailto):
    import math

    data = out / "data"
    methods = json.loads((data / "methods.json").read_text(encoding="utf-8"))
    qf = data / "queries.json"
    if not qf.exists():
        save_json(qf, QUERIES)
    queries = json.loads(qf.read_text(encoding="utf-8"))
    result, vacios, errores = {}, [], []
    for m in methods:
        phrases = queries.get(m["nombre"]) or []
        found = {}
        for ph in phrases:
            res = _openalex(
                {
                    # la frase exacta debe aparecer en título o resumen; `search` ordena por palabras de procedimiento
                    "filter": f'title_and_abstract.search:"{ph}",type:article|review,language:en|es',
                    "search": "procedure steps guide tutorial how to introduction",
                    "per-page": 25,
                    "mailto": mailto,
                    "select": "doi,title,publication_year,cited_by_count,open_access,type",
                }
            )
            if res is None:
                errores.append(m["nombre"])
                continue
            for w in res:
                title = w.get("title") or ""
                tl = title.lower()
                key = w.get("doi") or tl
                score = (
                    (3 if ph.lower() in tl else 0)
                    + sum(1 for k in PROC_WORDS if k in tl)
                    + math.log10((w.get("cited_by_count") or 0) + 1)
                )
                if key not in found or score > found[key]["puntaje"]:
                    found[key] = {
                        "titulo": title,
                        "anio": w.get("publication_year"),
                        "doi": w.get("doi"),
                        "citas": w.get("cited_by_count"),
                        "tipo": w.get("type"),
                        "url": (w.get("open_access") or {}).get("oa_url")
                        or w.get("doi"),
                        "acceso_abierto": (w.get("open_access") or {}).get("is_oa"),
                        "consulta": ph,
                        "puntaje": round(score, 2),
                        "fuente": "openalex",
                        "revisado": False,
                    }
            time.sleep(0.15)
        top = sorted(found.values(), key=lambda x: -x["puntaje"])[:per]
        result[m["id"]] = top
        flag = "  (nombre incierto, revisar)" if m["nombre"] in INCIERTOS else ""
        print(f"  {m['nombre']}: {len(top)}{flag}", flush=True)
        if not top:
            vacios.append(m["nombre"])
    save_json(data / "papers.json", result)
    print(
        f"\npapers de {len(result)} métodos; {sum(len(v) for v in result.values())} artículos"
    )
    if vacios:
        print("Sin resultados (ajusta data/queries.json):", ", ".join(vacios))
    if errores:
        print("Con errores de red:", ", ".join(sorted(set(errores))))


# ----------------------------------------------------------------------------- BUILD
def build(out, base):
    data = out / "data"
    methods = json.loads((data / "methods.json").read_text(encoding="utf-8"))
    pf = data / "papers.json"
    extra = json.loads(pf.read_text(encoding="utf-8")) if pf.exists() else {}
    tree, content = (
        {},
        {
            "version": 1,
            "fuente": base,
            "animacion": {
                "entrada": "slide-right",
                "retraso_ms_por_fase": 220,
                "flecha": "draw",
                "grupo_retroalimentacion": ["ana", "sin"],
            },
            "metodos": [],
        },
    )
    for m in methods:
        # Referencias: primero el caso que la propia web cita, luego los hallazgos de OpenAlex (por revisar)
        refs = [
            {
                "rol": "ejemplo_de_aplicacion",
                "titulo": m["ejemplo"]["titulo"],
                "url": m["ejemplo"]["url"],
                "fuente": "sitio original",
                "revisado": True,
            }
        ] + extra.get(m["id"], [])
        content["metodos"].append(
            {
                **{
                    k: m[k]
                    for k in (
                        "id",
                        "nombre",
                        "familia",
                        "categoria",
                        "subcategoria",
                        "descripcion_caso",
                        "ideal_para",
                        "hallazgos",
                        "imagen",
                    )
                },
                "fases": [
                    {
                        **p,
                        "orden": i + 1,
                        "animacion": {"retraso_ms": i * 220},
                        "herramientas": [],
                        "papers": [],
                    }
                    for i, p in enumerate(PHASES)
                ],
                "referencias": refs,
            }
        )
        node = (
            tree.setdefault(m["familia"], {})
            .setdefault(m["categoria"], {})
            .setdefault(m["subcategoria"], [])
        )
        node.append(m["id"])
    save_json(data / "content.json", content)

    # Mapa del sitio. La web real es una SPA con una sola URL; estas rutas son la propuesta navegable.
    def r(*parts):
        return "/" + "/".join(slug(x) for x in parts)

    routes = (
        ["/"]
        + sorted({r(m["familia"]) for m in methods})
        + sorted({r(m["familia"], m["categoria"]) for m in methods})
        + sorted({r(m["familia"], m["categoria"], m["subcategoria"]) for m in methods})
        + sorted(
            {
                r(m["familia"], m["categoria"], m["subcategoria"], m["id"])
                for m in methods
            }
        )
    )
    save_json(
        out / "sitemap_propuesto.json", {"base": base, "arbol": tree, "rutas": routes}
    )
    urls = "\n".join(f"  <url><loc>{base.rstrip('/')}{x}</loc></url>" for x in routes)
    (out / "sitemap_propuesto.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}\n</urlset>\n',
        encoding="utf-8",
    )
    z = out.with_suffix(".zip")
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in out.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(out.parent))
    print(
        f"content.json: {len(methods)} métodos, {len(routes)} rutas propuestas; zip en {z}"
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["crawl", "extract", "papers", "build", "all"])
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--max-pages", type=int, default=100)
    ap.add_argument("--max-elems", type=int, default=250)
    ap.add_argument("--depth", type=int, default=4)
    ap.add_argument("--max-states", type=int, default=120)
    ap.add_argument("--per", type=int, default=5)
    ap.add_argument("--mailto", default="")
    a = ap.parse_args()
    out = Path(a.out)
    if a.cmd in ("crawl", "all"):
        asyncio.run(crawl(a.base, out, a.max_pages, a.max_elems, a.depth, a.max_states))
    if a.cmd in ("extract", "all"):
        extract(out)
    if a.cmd in ("papers", "all"):
        fetch_papers(out, a.per, a.mailto)
    if a.cmd in ("build", "all"):
        build(out, a.base)
