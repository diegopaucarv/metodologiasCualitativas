#!/usr/bin/env python3
"""
Pipeline completo de descarga, extracción y estructuración metodológica.

Descarga (cadena de 8 fases):
  0   → Resolución de DOI
  0.5 → Descarga directa de PDFs
  1   → OpenAlex
  2   → Unpaywall
  3   → pyPaperFlow
  4   → Zotero OA mirror
  5   → Sci-Hub
  6   → Chromium (vía CDP a Chrome real)

Extracción:
  extract → texto de PDFs con pdfplumber
  steps   → pasos procedimentales con Together AI (JSON Schema estructurado)
  merge   → integración en metodos_completos.json

Instalación:
  pip install curl_cffi requests pdfplumber playwright playwright-stealth tqdm
  pip install pyPaperFlow               # opcional
  playwright install chromium

Arrancar Chrome con debugging remoto (otra terminal):
  # Windows
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" ^
      --remote-debugging-port=9222 --user-data-dir=%TEMP%\\chrome-debug
  # Linux
  google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-debug

Uso (desde la raíz del proyecto):
  python scrape_out/app/extract_procedural_steps.py download --cdp-url http://localhost:9222 --verbose
  python scrape_out/app/extract_procedural_steps.py download --phases 0,0.5,1,2 --verbose
  python scrape_out/app/extract_procedural_steps.py extract
  python scrape_out/app/extract_procedural_steps.py steps --model deepseek-ai/DeepSeek-V4-Flash-0731
  python scrape_out/app/extract_procedural_steps.py merge
  python scrape_out/app/extract_procedural_steps.py all
  # Fases post-descarga como fases independientes (7=extract, 8=steps, 9=merge):
  python scrape_out/app/extract_procedural_steps.py all --phases 7,8,9
  python scrape_out/app/extract_procedural_steps.py all --phases 0,1,7   # descarga 0,1 + extract
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse
from pathlib import Path
from typing import Optional

# ---- HTTP client: curl_cffi si está disponible, requests si no ----
try:
    from curl_cffi import requests as http_client

    HAS_CURL_CFFI = True
except ImportError:
    import requests as http_client

    HAS_CURL_CFFI = False

# ---- Barra de progreso ----
try:
    from tqdm import tqdm as _tqdm

    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

# =============================================================================
# CONFIGURACIÓN
# =============================================================================

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"

UA_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

OPENALEX_BASE = "https://api.openalex.org/works/"
UNPAYWALL_BASE = "https://api.unpaywall.org/v2/"
ZOTERO_OA_ENDPOINT = "https://services.zotero.org/oa/search"
CROSSREF_ENDPOINT = "https://api.crossref.org/works"
PMC_IDCONV_ENDPOINT = "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/"
TOGETHER_CHAT_ENDPOINT = "https://api.together.xyz/v1/chat/completions"

DEFAULT_SCIHUB_MIRRORS = [
    "https://sci-hub.ru/",
    "https://sci-hub.su/",
    "https://sci-hub.st/",
    "https://sci-hub.se/",
]

DOMAIN_PDF_HINTS = {
    "tandfonline.com": lambda url: url.split("?")[0] + "?download=true",
    "sciencedirect.com": lambda url: (
        url.rstrip("/") + "/pdfft?isDTMRedir=true&download=true"
    ),
    "link.springer.com": lambda url: (
        url.replace("/article/", "/content/pdf/") + ".pdf"
        if "/article/" in url and not url.endswith(".pdf")
        else url
    ),
    "onlinelibrary.wiley.com": lambda url: (
        url.replace("/doi/abs/", "/doi/pdfdirect/").replace(
            "/doi/full/", "/doi/pdfdirect/"
        )
        if "/doi/" in url
        else url
    ),
}

DELAY_BETWEEN_ITEMS = 0.3
DELAY_BETWEEN_SCIHUB = 1.0
DELAY_UNPAYWALL_RETRY = 3.0

TIMEOUT_SHORT = 6
TIMEOUT_MED = 12
TIMEOUT_LONG = 30
TIMEOUT_PDF = 45


# =============================================================================
# HELPERS DE OUTPUT
# =============================================================================


def _emit(msg: str):
    if HAS_TQDM:
        _tqdm.write(msg)
    else:
        print(msg, flush=True)


def _progress(iterable, desc: str, total: Optional[int] = None):
    if HAS_TQDM:
        return _tqdm(
            iterable,
            desc=desc,
            total=total,
            unit="item",
            dynamic_ncols=True,
            leave=True,
            position=0,
        )
    return _FallbackProgress(iterable, desc, total)


class _FallbackProgress:
    def __init__(self, iterable, desc: str, total: Optional[int]):
        self.iterable = list(iterable)
        self.desc = desc
        self.total = total or len(self.iterable)
        self.n = 0

    def __iter__(self):
        for item in self.iterable:
            yield item
            self.n += 1
            self._draw()

    def _draw(self):
        if self.total == 0:
            return
        pct = self.n / self.total * 100
        bar_len = 30
        filled = int(bar_len * self.n / self.total)
        bar = "█" * filled + "░" * (bar_len - filled)
        print(
            f"\r{self.desc} |{bar}| {self.n}/{self.total} ({pct:.0f}%)",
            end="",
            flush=True,
        )
        if self.n == self.total:
            print()


def _set_postfix(pbar, **kwargs):
    if HAS_TQDM and hasattr(pbar, "set_postfix"):
        try:
            pbar.set_postfix(**kwargs, refresh=False)
        except Exception:
            pass


# =============================================================================
# HELPERS
# =============================================================================


def save_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def is_pdf(content: Optional[bytes]) -> bool:
    return bool(content) and content[:4] == b"%PDF"


def log(verbose: bool, msg: str):
    if verbose:
        _emit(f"    [debug] {msg}")


_URL_SUFFIXES = (
    ".pdf",
    ".html",
    ".htm",
    ".xml",
    ".json",
    ".epub",
    "/full",
    "/pdf",
    "/abs",
    "/abstract",
    "/fulltext",
    "/epdf",
    "/pdfdirect",
    "/meta",
    "/references",
    "/figures",
    "/tables",
    "/download",
    "/content",
)


def _clean_doi(doi: str) -> str:
    """Limpia un DOI capturado por regex, quitando sufijos de URL."""
    doi = doi.rstrip(".,;:)]}'\"")
    changed = True
    while changed:
        changed = False
        for suffix in _URL_SUFFIXES:
            if doi.lower().endswith(suffix):
                doi = doi[: -len(suffix)].rstrip(".,;:)]}'\"")
                changed = True
                break
    return doi


_WORKING_IMPERSONATE = "__not_tested__"


def _detect_working_impersonate() -> Optional[str]:
    """Detecta UNA VEZ qué target de impersonate funciona."""
    global _WORKING_IMPERSONATE
    if _WORKING_IMPERSONATE != "__not_tested__":
        return _WORKING_IMPERSONATE

    if not HAS_CURL_CFFI:
        _WORKING_IMPERSONATE = None
        return None

    for target in ("chrome120", "chrome110", "chrome104", "chrome"):
        try:
            r = http_client.get(
                "https://example.com",
                impersonate=target,
                timeout=6,
            )
            if r.status_code == 200:
                _WORKING_IMPERSONATE = target
                _emit(f"[init] curl_cffi OK con impersonate={target}")
                return target
        except Exception as e:
            _emit(f"[init] impersonate={target} falló: {type(e).__name__}")
            continue

    _WORKING_IMPERSONATE = None
    _emit("[init] curl_cffi no sirve, usando requests puro")
    return None


def http_get(url: str, **kwargs):
    headers = kwargs.pop("headers", {"User-Agent": UA_DESKTOP})
    target = _detect_working_impersonate()

    if target:
        try:
            return http_client.get(url, impersonate=target, headers=headers, **kwargs)
        except Exception:
            pass

    # Fallback requests puro
    try:
        import requests as _req

        kwargs.pop("impersonate", None)
        return _req.get(url, headers=headers, **kwargs)
    except Exception:
        return None


def http_post(url: str, **kwargs):
    headers = kwargs.pop("headers", {"User-Agent": UA_DESKTOP})
    target = _detect_working_impersonate()

    if target:
        try:
            return http_client.post(url, impersonate=target, headers=headers, **kwargs)
        except Exception:
            pass

    try:
        import requests as _req

        kwargs.pop("impersonate", None)
        return _req.post(url, headers=headers, **kwargs)
    except Exception:
        return None


# =============================================================================
# FASE 0: RESOLUCIÓN DE DOI
# =============================================================================

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s'\"&?#<>]+", re.IGNORECASE)
PMCID_RE = re.compile(r"PMC(\d+)", re.IGNORECASE)
PMID_URL_RE = re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", re.IGNORECASE)


def _doi_from_text(text: str) -> Optional[str]:
    m = DOI_RE.search(text)
    return _clean_doi(m.group(0)) if m else None


def _query_idconv(id_str: str, email: str = "", verbose: bool = False) -> Optional[str]:
    try:
        r = http_get(
            PMC_IDCONV_ENDPOINT,
            params={
                "ids": id_str,
                "tool": "extract_procedural_steps",
                "email": email or "anonymous@example.com",
                "format": "json",
            },
            timeout=TIMEOUT_MED,
        )
        if r is None:
            return None
        if r.status_code != 200:
            return None
        records = r.json().get("records", [])
        if records and records[0].get("doi"):
            return _clean_doi(records[0]["doi"])
    except Exception:
        pass
    return None


def _doi_step0_ncbi(url: str, email: str, verbose: bool) -> Optional[str]:
    m = PMCID_RE.search(url)
    if m:
        doi = _query_idconv(f"PMC{m.group(1)}", email, verbose)
        if doi:
            log(verbose, f"PMCID {m.group(0)} → DOI {doi}")
        return doi
    m = PMID_URL_RE.search(url)
    if m:
        doi = _query_idconv(m.group(1), email, verbose)
        if doi:
            log(verbose, f"PMID {m.group(1)} → DOI {doi}")
        return doi
    return None


def _doi_step1_regex(url: str) -> Optional[str]:
    return _doi_from_text(url)


def _doi_step2_redirect(url: str, timeout: int = TIMEOUT_SHORT) -> Optional[str]:
    r = http_get(url, allow_redirects=True, timeout=timeout)
    if r is None:
        return None
    try:
        return _doi_from_text(str(r.url))
    except Exception:
        return None


def _doi_step3_html(url: str, timeout: int = TIMEOUT_MED) -> Optional[str]:
    r = http_get(url, timeout=timeout)
    if r is None:
        return None
    try:
        if r.status_code != 200:
            return None
        for p in (
            r'<meta[^>]+name=["\']citation_doi["\'][^>]+content=["\']([^"\']+)',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_doi',
            r'<meta[^>]+name=["\']DC\.Identifier["\'][^>]+content=["\'](10\.[^"\']+)',
        ):
            m = re.search(p, r.text, re.IGNORECASE)
            if m:
                return _clean_doi(m.group(1).strip())
        return _doi_from_text(r.text)
    except Exception:
        return None


def _doi_step4_openalex(url: str, verbose: bool = False) -> Optional[str]:
    try:
        encoded = urllib.parse.quote(url, safe="")
        r = http_get(
            OPENALEX_BASE + encoded,
            params={"select": "doi", "mailto": "anonymous@example.com"},
            timeout=TIMEOUT_MED,
        )
        if r is None or r.status_code != 200:
            return None
        doi = r.json().get("doi")
        if doi:
            return _clean_doi(doi.replace("https://doi.org/", ""))
    except Exception:
        return None
    return None


def _doi_step5_crossref(
    titulo: str, autor: str = "", timeout: int = TIMEOUT_MED
) -> Optional[str]:
    if not titulo:
        return None
    query = f"{titulo} {autor}".strip()
    try:
        r = http_get(
            CROSSREF_ENDPOINT,
            params={"query.bibliographic": query, "rows": 1, "select": "DOI,score"},
            timeout=timeout,
        )
        if r is None or r.status_code != 200:
            return None
        items = r.json().get("message", {}).get("items", [])
        if items and items[0].get("score", 0) > 20:
            return items[0].get("DOI")
    except Exception:
        return None
    return None


def _safe(callable_fn, *args, **kwargs):
    """Ejecuta una función y devuelve None si falla por cualquier motivo."""
    try:
        return callable_fn(*args, **kwargs)
    except Exception as e:
        log(True, f"[safe] {callable_fn.__name__} falló: {str(e)[:100]}")
        return None


def resolve_doi(
    url: str, titulo: str = "", autor: str = "", email: str = "", verbose: bool = False
) -> tuple[Optional[str], str]:
    # Cada paso está aislado: si uno revienta, se pasa al siguiente.
    try:
        doi = _safe(_doi_step0_ncbi, url, email, verbose)
        if doi:
            return doi, "ncbi_idconv"
    except Exception:
        pass

    try:
        doi = _safe(_doi_step1_regex, url)
        if doi:
            return doi, "regex_url"
    except Exception:
        pass

    try:
        doi = _safe(_doi_step4_openalex, url, verbose)
        if doi:
            log(verbose, f"DOI vía OpenAlex: {doi}")
            return doi, "openalex_resolver"
    except Exception:
        pass

    try:
        doi = _safe(_doi_step2_redirect, url)
        if doi:
            log(verbose, f"DOI vía redirect: {doi}")
            return doi, "redirect"
    except Exception:
        pass

    try:
        doi = _safe(_doi_step3_html, url)
        if doi:
            log(verbose, f"DOI vía HTML meta: {doi}")
            return doi, "html_meta"
    except Exception:
        pass

    try:
        if titulo:
            doi = _safe(_doi_step5_crossref, titulo, autor)
            if doi:
                log(verbose, f"DOI vía Crossref: {doi}")
                return doi, "crossref"
    except Exception:
        pass

    return None, "none"


# =============================================================================
# FASE 0.5: PDFs DIRECTOS
# =============================================================================

_DIRECT_PDF_PATTERNS = (
    "/content/pdf/",
    "/article/download/",
    "/bitstream/",
    "/viewcontent.cgi",
    "/rest/api/core/bitstreams/",
    "/fulltext/",
    "/download/",
    "resource_ssm_path=",
    "/pdf/",
    "/pdf?",
    "/pdf#",
)


def looks_like_direct_pdf(url: str) -> bool:
    lower = url.lower()
    if lower.endswith(".pdf") or ".pdf?" in lower or ".pdf#" in lower:
        return True
    return any(p in lower for p in _DIRECT_PDF_PATTERNS)


def try_direct_pdf(url: str, verbose: bool = False) -> tuple[Optional[bytes], str]:
    r = http_get(
        url,
        timeout=TIMEOUT_PDF,
        headers={"User-Agent": UA_DESKTOP, "Accept": "application/pdf,*/*"},
        allow_redirects=True,
    )
    if r is None:
        return None, "http_none"
    try:
        if r.status_code == 200 and is_pdf(r.content):
            return r.content, "ok"
        return None, f"http_{r.status_code}_no_pdf"
    except Exception as e:
        return None, f"excepcion: {str(e)[:100]}"


def try_pmc_pdf(pmcid: str) -> tuple[Optional[bytes], str]:
    """Descarga PDF de PMC usando el patrón de URL directo."""
    url = f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/pdf/"
    r = http_get(url, timeout=TIMEOUT_PDF, allow_redirects=True)
    if r is not None and r.status_code == 200 and is_pdf(r.content):
        return r.content, "ok (pmc)"
    return None, f"pmc_http_{r.status_code if r else 'none'}"


def _extract_pmcid(url: str) -> Optional[str]:
    """Extrae el PMCID de una URL de PMC."""
    m = re.search(r"/articles/(PMC\d+)/?", url, re.IGNORECASE)
    return m.group(1) if m else None


# =============================================================================
# HELPER COMPARTIDO
# =============================================================================


def _download_pdf_with_html_fallback(
    url: str, timeout: int = TIMEOUT_PDF
) -> Optional[bytes]:
    try:
        r = http_get(url, timeout=timeout, allow_redirects=True)
        if r is None:
            return None
        if r.status_code != 200:
            return None
        if is_pdf(r.content):
            return r.content
        m = re.search(
            r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)',
            r.text,
            re.IGNORECASE,
        )
        if m:
            real = urllib.parse.urljoin(url, m.group(1))
            r2 = http_get(real, timeout=timeout, allow_redirects=True)
            if r2 is None:
                return None
            if r2.status_code == 200 and is_pdf(r2.content):
                return r2.content
    except Exception:
        pass
    return None


# =============================================================================
# FASE 1: OPENALEX
# =============================================================================


def _openalex_identifier(url: str, doi: Optional[str]) -> Optional[str]:
    """Identificador válido para OpenAlex: doi:, pmid: o pmcid:."""
    if doi:
        return f"doi:{doi}"
    if m := re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", url):
        return f"pmid:{m.group(1)}"
    if m := re.search(r"PMC(\d+)", url, re.IGNORECASE):
        return f"pmcid:PMC{m.group(1)}"
    return None


def try_openalex(
    url: str, doi: Optional[str], verbose: bool = False
) -> tuple[Optional[bytes], Optional[str], str]:
    identifier = _openalex_identifier(url, doi)
    if not identifier:
        return None, None, "sin_identificador"
    encoded = urllib.parse.quote(identifier, safe=":")
    try:
        r = http_get(
            OPENALEX_BASE + encoded,
            params={
                "select": "doi,open_access,best_oa_location,locations",
                "mailto": "anonymous@example.com",
            },
            timeout=TIMEOUT_MED,
        )
        if r is None:
            return None, None, "http_none"
        if r.status_code == 404:
            return None, None, "no_encontrado"
        if r.status_code != 200:
            return None, None, f"http_{r.status_code}"
        data = r.json()
        found_doi = data.get("doi")
        if found_doi:
            found_doi = found_doi.replace("https://doi.org/", "")
        oa = data.get("open_access", {})
        if not oa.get("is_oa"):
            return None, found_doi, f"no_oa ({oa.get('oa_status', 'closed')})"
        candidates = []
        best = data.get("best_oa_location") or {}
        if best.get("pdf_url"):
            candidates.append(best["pdf_url"])
        if best.get("landing_page_url"):
            candidates.append(best["landing_page_url"])
        for loc in data.get("locations", []) or []:
            if loc.get("pdf_url"):
                candidates.append(loc["pdf_url"])
            if loc.get("landing_page_url"):
                candidates.append(loc["landing_page_url"])
        for c in candidates:
            content = _download_pdf_with_html_fallback(c)
            if content:
                return content, found_doi, f"ok ({c[:60]})"
        return None, found_doi, "oa_sin_pdf_descargable"
    except Exception as e:
        return None, None, f"excepcion: {str(e)[:100]}"


# =============================================================================
# FASE 2: UNPAYWALL
# =============================================================================


def try_unpaywall(
    doi: Optional[str], email: str, verbose: bool = False
) -> tuple[Optional[bytes], str]:
    if not email:
        return None, "sin_email"
    if not doi:
        return None, "sin_doi"
    api_url = f"{UNPAYWALL_BASE}{urllib.parse.quote(doi, safe='')}"
    for intento in range(2):
        try:
            r = http_get(api_url, params={"email": email}, timeout=TIMEOUT_MED)
            if r is None:
                return None, "http_none"
            if r.status_code == 422:
                return None, "email_placeholder_422"
            if r.status_code == 403:
                return None, "email_rechazado_403"
            if r.status_code == 404:
                return None, "doi_no_registrado_404"
            if r.status_code == 500 and intento == 0:
                time.sleep(DELAY_UNPAYWALL_RETRY)
                continue
            if r.status_code != 200:
                return None, f"http_{r.status_code}"
            data = r.json()
            if not data.get("is_oa"):
                return None, f"no_oa ({data.get('oa_status', 'closed')})"
            candidates = []
            for key in ("first_oa_location", "best_oa_location"):
                if data.get(key):
                    candidates.append(data[key])
            candidates.extend(data.get("oa_locations", []))
            for loc in candidates:
                for pdf_key in ("url_for_pdf", "url"):
                    candidate = loc.get(pdf_key)
                    if not candidate:
                        continue
                    content = _download_pdf_with_html_fallback(candidate)
                    if content:
                        return content, f"ok ({pdf_key})"
            return None, "oa_sin_pdf_descargable"
        except Exception as e:
            return None, f"excepcion: {str(e)[:100]}"
    return None, "http_500_repetido"


# =============================================================================
# FASE 3: pyPaperFlow
# =============================================================================


_PYPAPERFLOW_CACHE = {"mod": None, "err": None, "tested": False}


def _pypaperflow_import():
    if _PYPAPERFLOW_CACHE["tested"]:
        return _PYPAPERFLOW_CACHE["mod"], _PYPAPERFLOW_CACHE["err"]

    _PYPAPERFLOW_CACHE["tested"] = True
    try:
        import pyPaperFlow

        public = [x for x in dir(pyPaperFlow) if not x.startswith("_")]
        if not public:
            _PYPAPERFLOW_CACHE["mod"] = None
            _PYPAPERFLOW_CACHE["err"] = (
                "modulo vacio — probablemente no es pyPaperFlow real"
            )
            _emit(
                "[init] pyPaperFlow importado pero sin API pública. "
                "Desinstala el paquete actual: pip uninstall pyPaperFlow"
            )
            return None, _PYPAPERFLOW_CACHE["err"]
        _emit(f"[init] pyPaperFlow expone: {public[:15]}")
        _PYPAPERFLOW_CACHE["mod"] = pyPaperFlow
        return pyPaperFlow, None
    except ImportError as e:
        _PYPAPERFLOW_CACHE["mod"] = None
        _PYPAPERFLOW_CACHE["err"] = str(e)
        return None, str(e)


def try_pypaperflow_cli(
    doi: str, out_dir: Path, verbose: bool = False
) -> tuple[Optional[bytes], str]:
    """Llama a pyPaperFlow vía CLI (paperflow paper-fetch)."""
    if not doi:
        return None, "sin_doi"

    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "paperflow",
        "paper-fetch",
        doi,
        "--out",
        str(out_dir),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if verbose:
            if result.stdout:
                _emit(f"    [paperflow stdout] {result.stdout[:200]}")
            if result.stderr:
                _emit(f"    [paperflow stderr] {result.stderr[:200]}")

        if result.returncode != 0:
            return None, f"cli_exit_{result.returncode}"

        for pdf_file in out_dir.glob("*.pdf"):
            if (
                doi.replace("/", "_") in pdf_file.name
                or pdf_file.stat().st_mtime > time.time() - 60
            ):
                content = pdf_file.read_bytes()
                if is_pdf(content):
                    return content, "ok (cli)"

        return None, "cli_sin_pdf"

    except FileNotFoundError:
        return None, "no_instalado (paperflow no está en PATH)"
    except subprocess.TimeoutExpired:
        return None, "cli_timeout"
    except Exception as e:
        return None, f"cli_excepcion: {str(e)[:100]}"


def try_pypaperflow(
    url: str, doi: Optional[str], verbose: bool = False, base_dir: Path = BASE
) -> tuple[Optional[bytes], str]:
    """Wrapper que llama a pyPaperFlow vía CLI."""
    if not doi:
        return None, "sin_doi"

    out_dir = base_dir / "data" / "pypaperflow_tmp"

    return try_pypaperflow_cli(doi, out_dir, verbose)


def _extract_content_from_pypaperflow(result) -> Optional[bytes]:
    if result is None:
        return None
    if isinstance(result, (bytes, bytearray)):
        return bytes(result) if is_pdf(bytes(result)) else None
    if isinstance(result, str):
        p = Path(result)
        if p.exists() and p.is_file():
            b = p.read_bytes()
            return b if is_pdf(b) else None
        return None
    if isinstance(result, dict):
        for k in ("pdf_bytes", "content", "data", "bytes"):
            v = result.get(k)
            if isinstance(v, (bytes, bytearray)) and is_pdf(bytes(v)):
                return bytes(v)
        for k in ("pdf_path", "path", "file", "filepath"):
            v = result.get(k)
            if isinstance(v, str):
                p = Path(v)
                if p.exists() and p.is_file():
                    b = p.read_bytes()
                    if is_pdf(b):
                        return b
        return None
    for attr in ("pdf_bytes", "content", "data"):
        v = getattr(result, attr, None)
        if isinstance(v, (bytes, bytearray)) and is_pdf(bytes(v)):
            return bytes(v)
    for attr in ("pdf_path", "path", "file", "filepath"):
        v = getattr(result, attr, None)
        if isinstance(v, str):
            p = Path(v)
            if p.exists() and p.is_file():
                b = p.read_bytes()
                if is_pdf(b):
                    return b
    return None


# =============================================================================
# FASE 4: ZOTERO OA
# =============================================================================


def try_zotero_oa(
    doi: Optional[str], verbose: bool = False
) -> tuple[Optional[bytes], str]:
    if not doi:
        return None, "sin_doi"
    try:
        r = http_post(
            ZOTERO_OA_ENDPOINT,
            json={"doi": doi},
            headers={"Content-Type": "application/json"},
            timeout=TIMEOUT_MED,
        )
        if r is None:
            return None, "http_none"
        if r.status_code != 200:
            return None, f"http_{r.status_code}"
        try:
            urls = r.json()
        except Exception:
            return None, "json_invalido"
        if not isinstance(urls, list) or not urls:
            return None, "sin_urls_oa"
        for entry in urls:
            if not isinstance(entry, dict):
                continue
            for key in ("url", "pageURL"):
                candidate = entry.get(key)
                if not candidate:
                    continue
                content = _download_pdf_with_html_fallback(candidate)
                if content:
                    return content, f"ok ({key})"
        return None, "urls_sin_pdf"
    except Exception as e:
        return None, f"excepcion: {str(e)[:100]}"


# =============================================================================
# FASE 5: SCI-HUB
# =============================================================================


def _probe_scihub_mirrors(mirrors: list[str], timeout: int = 6) -> list[str]:
    """Devuelve solo los mirrors que responden."""
    alive = []
    for base in mirrors:
        try:
            r = http_get(base, timeout=timeout, allow_redirects=True)
            if r is not None and r.status_code < 500:
                alive.append(base)
                _emit(f"  [sci-hub] {base} → OK ({r.status_code})")
            else:
                code = r.status_code if r is not None else "None"
                _emit(f"  [sci-hub] {base} → descartado ({code})")
        except Exception as e:
            _emit(f"  [sci-hub] {base} → descartado ({type(e).__name__})")
    return alive


def try_scihub(
    doi: Optional[str],
    mirrors: list[str],
    context,
    verbose: bool = False,
    pbar=None,
) -> tuple[Optional[bytes], str]:
    """Sci-Hub vía Chromium (CDP): navega al mirror y captura el PDF.

    Sci-Hub sirve un challenge de Cloudflare al HTTP plano (curl_cffi no lo
    pasa). Con el navegador real (Edge vía CDP, perfil warmeado) el challenge
    se resuelve solo y capturamos el PDF por red o por el <embed>.
    """
    if not doi:
        return None, "sin_doi"
    if context is None:
        return None, "sin_contexto_chromium"

    last_reason = "todos_mirrors_fallaron"
    for base in mirrors:
        _set_postfix(pbar, mirror=base.rstrip("/").replace("https://", ""))
        target = urllib.parse.urljoin(base, doi)
        page = None
        try:
            page = context.new_page()
            captured: list[bytes] = []

            def on_response(resp):
                try:
                    if "pdf" in resp.headers.get("content-type", "").lower():
                        body = resp.body()
                        if is_pdf(body):
                            captured.append(body)
                except Exception:
                    pass

            page.on("response", on_response)
            try:
                page.goto(target, wait_until="domcontentloaded", timeout=45000)
                # Esperar a que Cloudflare resuelva el challenge y cargue el PDF
                for _ in range(20):
                    if captured:
                        break
                    page.wait_for_timeout(1000)
            except Exception as e:
                log(
                    verbose,
                    f"sci-hub {base} goto: {type(e).__name__}: {str(e)[:80]}",
                )
            finally:
                try:
                    page.remove_listener("response", on_response)
                except Exception:
                    pass

            if captured:
                return captured[0], f"ok ({base.rstrip('/')})"

            # Fallback: buscar <embed>/<iframe>/<a> y descargar con cookies del browser
            html = ""
            try:
                html = page.content()
            except Exception:
                pass
            match = re.search(
                r'(?:<embed|<iframe)[^>]+src=["\']([^"\']+pdf[^"\']*)["\']',
                html,
                re.IGNORECASE,
            )
            if not match:
                match = re.search(
                    r'href=["\']([^"\']+\.pdf[^"\']*)["\']',
                    html,
                    re.IGNORECASE,
                )
            if match:
                pdf_path = match.group(1)
                if pdf_path.startswith("//"):
                    pdf_url = "https:" + pdf_path
                elif pdf_path.startswith("/"):
                    pdf_url = base.rstrip("/") + pdf_path
                elif pdf_path.startswith("http"):
                    pdf_url = pdf_path
                else:
                    pdf_url = urllib.parse.urljoin(base, pdf_path)
                cookies = context.cookies()
                cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
                pdf_r = http_get(
                    pdf_url,
                    timeout=TIMEOUT_LONG,
                    headers={"Referer": target, "Cookie": cookie_str},
                    allow_redirects=True,
                )
                if (
                    pdf_r is not None
                    and pdf_r.status_code == 200
                    and is_pdf(pdf_r.content)
                ):
                    return pdf_r.content, f"ok ({base.rstrip('/')})"
                last_reason = f"{base.rstrip('/')}_pdf_no_valido"
            else:
                low = html.lower()
                if "captcha" in low or "challenge" in low:
                    last_reason = f"{base.rstrip('/')}_captcha"
                elif "not found" in low or "no article" in low:
                    last_reason = f"{base.rstrip('/')}_no_article"
                else:
                    last_reason = f"{base.rstrip('/')}_sin_enlace_pdf"
        except Exception as e:
            last_reason = f"{base.rstrip('/')}_error_{type(e).__name__}"
            _emit(
                f"    [sci-hub] {base.rstrip('/')} → {type(e).__name__}: {str(e)[:80]}"
            )
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass
            time.sleep(DELAY_BETWEEN_SCIHUB)
    return None, last_reason


# =============================================================================
# FASE 6: CHROMIUM
# =============================================================================


def _apply_domain_hint(url: str) -> str:
    for domain, transform in DOMAIN_PDF_HINTS.items():
        if domain in url:
            try:
                return transform(url)
            except Exception:
                return url
    return url


def _extract_pdf_from_download(page, download) -> Optional[bytes]:
    try:
        temp_path = download.path()
        if not temp_path:
            return None
        with open(temp_path, "rb") as f:
            header = f.read(4)
            if header == b"%PDF":
                f.seek(0)
                return f.read()
    except Exception:
        pass
    return None


def _try_expect_download(page, target_url: str, timeout_ms: int) -> Optional[bytes]:
    try:
        with page.expect_download(timeout=timeout_ms) as download_info:
            try:
                page.goto(target_url, wait_until="commit", timeout=timeout_ms)
            except Exception as nav_error:
                err = str(nav_error)
                if "ERR_ABORTED" not in err and "Download is starting" not in err:
                    raise
        download = download_info.value
        return _extract_pdf_from_download(page, download)
    except PlaywrightTimeoutError:
        return None
    except Exception:
        return None


def _find_pdf_url_in_page(page, base_url: str) -> Optional[str]:
    # 1. meta citation_pdf_url
    try:
        meta = page.locator('meta[name="citation_pdf_url"]').first
        if meta.count() > 0:
            c = meta.get_attribute("content")
            if c:
                return urllib.parse.urljoin(base_url, c)
    except Exception:
        pass

    # 2. Enlaces con texto "PDF", "Download", "Descargar", "Full text"
    text_selectors = (
        'a:has-text("PDF")',
        'a:has-text("Download PDF")',
        'a:has-text("Download")',
        'a:has-text("Descargar")',
        'a:has-text("Texto completo")',
        'a:has-text("Full text")',
        'a:has-text("View PDF")',
    )
    for sel in text_selectors:
        try:
            link = page.locator(sel).first
            if link.count() > 0:
                href = link.get_attribute("href")
                if href and href != "#" and not href.startswith("javascript:"):
                    url = urllib.parse.urljoin(base_url, href)
                    low = url.lower()
                    if not any(
                        x in low for x in ("/login", "/subscribe", "/cart", "/signin")
                    ):
                        return url
        except Exception:
            continue

    # 3. Pista por dominio
    hinted = _apply_domain_hint(base_url)
    if hinted != base_url:
        return hinted

    # 4. Enlaces directos a .pdf
    try:
        link = page.locator(
            'a[href$=".pdf"], a[href*=".pdf?"], a[href*="/pdf/"], '
            'a[href*="download"], a[download]'
        ).first
        if link.count() > 0:
            href = link.get_attribute("href")
            if href:
                return urllib.parse.urljoin(base_url, href)
    except Exception:
        pass

    # 5. iframes / embeds (todos)
    try:
        for iframe in page.locator("iframe, embed").all():
            src = iframe.get_attribute("src")
            if src and ("pdf" in src.lower() or "epdf" in src.lower()):
                return urllib.parse.urljoin(base_url, src)
    except Exception:
        pass

    # 6. Frames anidados
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        try:
            meta = frame.locator('meta[name="citation_pdf_url"]').first
            if meta.count() > 0:
                c = meta.get_attribute("content")
                if c:
                    return urllib.parse.urljoin(base_url, c)
        except Exception:
            continue

    # 7. Buscar URLs de PDF embebidas en scripts
    try:
        html = page.content()
        for pattern in (
            r'"(https?://[^"]+\.pdf[^"]*)"',
            r'"(/[^"]+\.pdf[^"]*)"',
            r'citation_pdf_url["\']?\s*[:=]\s*["\']([^"\']+)',
        ):
            m = re.search(pattern, html, re.IGNORECASE)
            if m:
                candidate = m.group(1)
                return urllib.parse.urljoin(base_url, candidate)
    except Exception:
        pass

    return None


def _try_chromium_single(
    item, url: str, context, verbose: bool, timeout_ms: int
) -> tuple[Optional[bytes], str]:
    """Un solo intento de Chromium sobre una URL concreta."""
    page = context.new_page()
    downloaded_files = []
    page.on("download", lambda d: downloaded_files.append(d))

    # Forzar descarga de PDFs en lugar de abrir el visor nativo
    def _force_download(route):
        try:
            response = route.fetch()
            headers = dict(response.headers)
            headers["content-disposition"] = "attachment"
            route.fulfill(response=response, headers=headers)
        except Exception:
            route.continue_()

    try:
        page.route("**/*.pdf", _force_download)
    except Exception:
        pass

    try:
        if verbose:
            log(verbose, f"Navegando: {url[:80]}...")

        response = None
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(3000)
        except Exception as e:
            err = str(e)
            if "Download is starting" in err or "ERR_ABORTED" in err:
                for _ in range(40):
                    if downloaded_files:
                        break
                    page.wait_for_timeout(500)
                if downloaded_files:
                    content = _extract_pdf_from_download(page, downloaded_files[0])
                    if content:
                        return content, "chromium_direct_pdf"
                return None, "chromium_download_sin_archivo"
            raise

        # Extraer DOI de meta tags si no lo tenemos
        if not item.get("doi"):
            try:
                meta = page.locator('meta[name="citation_doi"]').first
                if meta.count() > 0:
                    c = meta.get_attribute("content")
                    if c:
                        doi = _clean_doi(c.strip())
                        if doi:
                            item["doi"] = doi
                            item["doi_source"] = "chromium_meta"
                            _emit(f"    ✓ DOI extraído de meta: {doi}")
            except Exception:
                pass

        # ResearchGate: el botón "Download" está bajo "More"
        if "researchgate.net" in url:
            try:
                more_btn = page.locator('button:has-text("More")').first
                if more_btn.count() > 0:
                    more_btn.click()
                    page.wait_for_timeout(1000)
                    dl_btn = page.locator(
                        'a:has-text("Download"), button:has-text("Download")'
                    ).first
                    if dl_btn.count() > 0:
                        dl_btn.click()
                        for _ in range(40):
                            if downloaded_files:
                                break
                            page.wait_for_timeout(500)
                        if downloaded_files:
                            content = _extract_pdf_from_download(
                                page, downloaded_files[0]
                            )
                            if content:
                                return content, "chromium_researchgate"
            except Exception:
                pass

        if response:
            ct = response.headers.get("content-type", "").lower()
            if "application/pdf" in ct:
                cookies = context.cookies()
                cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
                try:
                    r = http_get(
                        url,
                        timeout=TIMEOUT_PDF,
                        headers={
                            "User-Agent": UA_DESKTOP,
                            "Referer": url,
                            "Cookie": cookie_str,
                        },
                        allow_redirects=True,
                    )
                    if r is not None and r.status_code == 200 and is_pdf(r.content):
                        return r.content, "chromium_pdf_content_type"
                except Exception:
                    pass
                content = _try_expect_download(page, url, timeout_ms)
                if content:
                    return content, "chromium_direct_pdf"
                return None, "chromium_pdf_no_descargado"

        pdf_url = _find_pdf_url_in_page(page, url)
        if not pdf_url:
            return None, "chromium_sin_enlace_pdf"

        # Estrategia A: context.request
        try:
            resp = context.request.get(pdf_url, timeout=timeout_ms)
            if resp.ok:
                body = resp.body()
                if is_pdf(body):
                    return body, "chromium_stealth_A"
        except Exception as e:
            log(verbose, f"A falló: {str(e)[:80]}")

        # Estrategia B: curl_cffi con cookies
        try:
            cookies = context.cookies()
            cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
            r = http_get(
                pdf_url,
                timeout=TIMEOUT_PDF,
                headers={
                    "User-Agent": UA_DESKTOP,
                    "Referer": url,
                    "Accept": "application/pdf,*/*",
                    "Cookie": cookie_str,
                },
                allow_redirects=True,
            )
            if r is not None and r.status_code == 200 and is_pdf(r.content):
                return r.content, "chromium_stealth_B"
        except Exception as e:
            log(verbose, f"B falló: {str(e)[:80]}")

        # Estrategia C: expect_download
        content = _try_expect_download(page, pdf_url, timeout_ms)
        if content:
            return content, "chromium_stealth_C"

        if downloaded_files:
            content = _extract_pdf_from_download(page, downloaded_files[0])
            if content:
                return content, "chromium_direct_pdf"

        return None, "chromium_tres_estrategias_fallaron"

    except PlaywrightTimeoutError:
        return None, "chromium_timeout_navegacion"
    except Exception as e:
        return None, f"chromium_excepcion: {str(e)[:120]}"
    finally:
        try:
            page.close()
        except Exception:
            pass


def try_chromium(
    item: dict, context, verbose: bool = False, timeout_ms: int = 45000
) -> tuple[Optional[bytes], str]:
    """Prueba la URL original y, si hay DOI, también doi.org/{doi}."""
    # Fast path: PMC (URL directa, sin Chromium)
    pmcid = _extract_pmcid(item["url"])
    if pmcid:
        content, reason = try_pmc_pdf(pmcid)
        if content:
            return content, reason

    urls_to_try = [item["url"]]
    if item.get("doi"):
        doi_url = f"https://doi.org/{item['doi']}"
        if doi_url not in urls_to_try:
            urls_to_try.append(doi_url)

    last_reason = "chromium_sin_pdf"
    for i, url in enumerate(urls_to_try):
        if i > 0:
            _emit(f"    ↻ Reintento vía {url[:70]}")
        content, reason = _try_chromium_single(item, url, context, verbose, timeout_ms)
        if content:
            return content, reason
        last_reason = f"{reason} (url {i + 1})"
    return None, last_reason


def _resolve_with_strategies(item: dict, context, verbose: bool = False):
    """Resuelve el PDF con el motor de estrategias genéricas + Chromium (CDP)."""
    try:
        from strategies import make_context, resolve_pdf
    except ImportError:
        return None, "strategies_no_disponible"

    page = context.new_page()
    try:
        ctx = make_context(
            item["url"],
            doi=item.get("doi"),
            titulo=item.get("titulo", ""),
            autor=item.get("autor", ""),
            chromium_page=page,
        )
        content, reason = resolve_pdf(ctx)
        return content, reason
    finally:
        try:
            page.close()
        except Exception:
            pass


# =============================================================================
# ORQUESTADOR DE DESCARGAS
# =============================================================================

PHASE_NAMES = ["0", "0.5", "1", "2", "3", "4", "5", "6"]
POST_PHASES = ["extract", "steps", "merge"]
PHASE_ALIASES = {"7": "extract", "8": "steps", "9": "merge"}
ALL_PHASES = PHASE_NAMES + POST_PHASES


def _mid_of(m: dict) -> str:
    return m.get("id") or m["nombre"].lower().replace(" ", "-").replace("ó", "o")


def ensure_edge_running(cdp_url: str) -> bool:
    """Verifica si Edge CDP está corriendo; si no, lo abre."""
    import urllib.request

    try:
        with urllib.request.urlopen(
            f"{cdp_url.rstrip('/')}/json/version", timeout=3
        ) as resp:
            return True
    except Exception:
        pass

    # Extraer el puerto del cdp_url (default 9222)
    port = "9222"
    m = re.search(r":(\d+)", cdp_url)
    if m:
        port = m.group(1)

    tmp = os.environ.get("TEMP") or os.environ.get("TMP") or "."
    edge_path = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    subprocess.Popen(
        [
            edge_path,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={tmp}\\edge-cdp",
            "--profile-directory=Default",
            "--disable-blink-features=AutomationControlled",
        ]
    )
    time.sleep(5)
    return True


def download_pdfs(
    base_dir: Path = BASE,
    skip_existing: bool = True,
    mailto: str = "",
    verbose: bool = False,
    mirrors_scihub: Optional[list[str]] = None,
    enabled_phases: Optional[set[str]] = None,
    cdp_url: str = "",
    methods: Optional[list[str]] = None,
    force: bool = False,
    url_overrides: Optional[dict[str, str]] = None,
):
    mirrors_scihub = mirrors_scihub or DEFAULT_SCIHUB_MIRRORS
    enabled = set(enabled_phases) if enabled_phases else set(PHASE_NAMES)
    methods_filter = {m.lower() for m in (methods or [])}

    methods = load_json(base_dir / "data" / "metodos_completos.json")
    if not methods:
        raise SystemExit("No encontré metodos_completos.json.")

    pdf_dir = base_dir / "data" / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    refs = load_json(base_dir / "data" / "pdf_refs.json")
    state = load_json(base_dir / "data" / "download_state.json")

    if not mailto:
        mailto = os.environ.get("UNPAYWALL_EMAIL", "").strip()

    if not mailto and "2" in enabled:
        print("  [!] Sin email para Unpaywall (--mailto o UNPAYWALL_EMAIL).")
        print(
            "      Fase 2 (Unpaywall) deshabilitada; los items no se marcarán como fallo."
        )
        enabled.discard("2")

    stats = {
        "direct_pdf": 0,
        "openalex": 0,
        "unpaywall": 0,
        "pypaperflow": 0,
        "zotero_oa": 0,
        "scihub": 0,
        "chromium_stealth": 0,
    }
    skipped = 0

    queue = []
    changed = False
    for m in methods:
        mid = _mid_of(m)
        if methods_filter and mid.lower() not in methods_filter:
            continue
        if force:
            # Re-descarga: limpia refs y texto previos para no duplicar
            refs.pop(mid, None)
            texts = load_json(base_dir / "data" / "pdf_texts.json")
            texts.pop(mid, None)
            save_json(base_dir / "data" / "pdf_texts.json", texts)
        if skip_existing and not force and mid in refs and refs[mid]:
            skipped += len(refs[mid])
            continue
        url = (url_overrides or {}).get(mid) or m.get("ejemplo", {}).get("url", "")
        if url_overrides and mid in url_overrides:
            # Reemplaza la URL y la persiste en metodos_completos.json
            m.setdefault("ejemplo", {})["url"] = url
            changed = True
        if not url.startswith("http"):
            continue
        fname = f"{mid}_0.pdf"
        if skip_existing and not force and (pdf_dir / fname).exists():
            skipped += 1
            continue
        queue.append(
            {
                "mid": mid,
                "url": url,
                "fname": fname,
                "m": m,
                "titulo": m.get("titulo", "") or m.get("title", ""),
                "autor": m.get("autor", ""),
                "doi": None,
                "doi_source": None,
            }
        )

    if changed:
        save_json(base_dir / "data" / "metodos_completos.json", methods)

    if not queue:
        print("No hay archivos nuevos por procesar.")
        return

    print(f"\nItems en cola: {len(queue)}")
    print(f"Fases habilitadas: {sorted(enabled)}")
    if HAS_CURL_CFFI:
        print("HTTP client: curl_cffi (impersonate=chrome)")
    else:
        print("HTTP client: requests (sin impersonate) — instala curl_cffi")
    if cdp_url and "6" in enabled:
        print(f"Chromium: CDP a {cdp_url}")
    if not HAS_TQDM:
        print("Barra: fallback (instala tqdm para mejor experiencia)")

    def save_success(item, content, source):
        (pdf_dir / item["fname"]).write_bytes(content)
        refs.setdefault(item["mid"], []).append(
            {
                "url": item["url"],
                "file": item["fname"],
                "status": "downloaded",
                "source": source,
            }
        )
        save_json(base_dir / "data" / "pdf_refs.json", refs)
        stats[source] = stats.get(source, 0) + 1
        _emit(f"  ✓ {item['m']['nombre'][:38]:38} → {source}")

    def set_state(item, fase: str, ok: bool, reason: str):
        state.setdefault(item["mid"], {"url": item["url"]})
        state[item["mid"]][fase] = {"ok": ok, "razon": reason}

    def persist():
        save_json(base_dir / "data" / "download_state.json", state)

    def persist_dois():
        """Guarda DOI + fuente resueltos para reusar entre corridas."""
        dois_file = base_dir / "data" / "dois_resueltos.json"
        existing = load_json(dois_file)
        for item in queue:
            if item.get("doi"):
                existing[item["mid"]] = {
                    "doi": item["doi"],
                    "source": item.get("doi_source"),
                    "url": item["url"],
                }
        save_json(dois_file, existing)

    def restore_dois():
        """Restaura DOIs guardados en corridas anteriores."""
        dois_file = base_dir / "data" / "dois_resueltos.json"
        existing = load_json(dois_file)
        restored = 0
        for item in queue:
            if item["mid"] in existing and not item.get("doi"):
                item["doi"] = existing[item["mid"]].get("doi")
                item["doi_source"] = existing[item["mid"]].get("source", "cache")
                if item["doi"]:
                    restored += 1
        if restored:
            print(f"  → {restored} DOIs restaurados de corridas previas")

    # ---- Fase 0 ----
    if "0" in enabled:
        print(f"\n=== FASE 0: Resolución de DOIs ===")
        # Detectar impersonate una sola vez
        _detect_working_impersonate()
        # Restaurar DOIs resueltos en corridas previas
        restore_dois()

        pbar = _progress(queue, desc="Fase 0 DOI  ", total=len(queue))
        n_ok = 0
        try:
            for item in pbar:
                # Log visible del item actual (no rompe la barra)
                _set_postfix(pbar, ok=n_ok, current=item["mid"][:22])
                _emit(f"  → [{item['mid'][:40]}] {item['url'][:70]}")

                try:
                    doi, fuente = resolve_doi(
                        item["url"], item["titulo"], item["autor"], mailto, verbose
                    )
                except Exception as e:
                    _emit(f"    [!] excepción: {type(e).__name__}: {str(e)[:80]}")
                    doi, fuente = None, "exception"

                item["doi"] = doi
                item["doi_source"] = fuente
                if doi:
                    n_ok += 1
                    _emit(f"    ✓ DOI={doi} ({fuente})")
                else:
                    _emit(f"    ✗ sin DOI ({fuente})")

                time.sleep(DELAY_BETWEEN_ITEMS)
        except KeyboardInterrupt:
            _emit("\n  [interrumpido por el usuario]")
        finally:
            if HAS_TQDM and hasattr(pbar, "close"):
                pbar.close()
        print(f"  → {n_ok}/{len(queue)} DOIs resueltos")
        persist_dois()

    # ---- Fase 0.5 ----
    pending = queue
    if "0.5" in enabled:
        print(f"\n=== FASE 0.5: PDFs directos ===")
        nq = []
        pbar = _progress(pending, desc="Fase 0.5 dir", total=len(pending))
        try:
            for item in pbar:
                if not looks_like_direct_pdf(item["url"]):
                    nq.append(item)
                    _set_postfix(
                        pbar,
                        ok=stats["direct_pdf"],
                        pend=len(nq),
                        current=item["mid"][:22],
                    )
                    continue
                content, reason = try_direct_pdf(item["url"], verbose)
                set_state(item, "direct_pdf", bool(content), reason)
                if content:
                    save_success(item, content, "direct_pdf")
                else:
                    nq.append(item)
                _set_postfix(
                    pbar, ok=stats["direct_pdf"], pend=len(nq), current=item["mid"][:22]
                )
                time.sleep(DELAY_BETWEEN_ITEMS)
        finally:
            if HAS_TQDM and hasattr(pbar, "close"):
                pbar.close()
        persist()
        print(f"  → {stats['direct_pdf']} OK, {len(nq)} pendientes")
        pending = nq

    # ---- Fase 1: OpenAlex ----
    if "1" in enabled and pending:
        print(f"\n=== FASE 1: OpenAlex ===")
        nq = []
        pbar = _progress(pending, desc="Fase 1 OA   ", total=len(pending))
        try:
            for item in pbar:
                content, found_doi, reason = try_openalex(
                    item["url"], item["doi"], verbose
                )
                if found_doi and not item["doi"]:
                    item["doi"] = found_doi
                    item["doi_source"] = "openalex"
                if reason != "sin_identificador":
                    set_state(item, "openalex", bool(content), reason)
                if content:
                    save_success(item, content, "openalex")
                else:
                    nq.append(item)
                _set_postfix(
                    pbar, ok=stats["openalex"], pend=len(nq), current=item["mid"][:22]
                )
                time.sleep(DELAY_BETWEEN_ITEMS)
        finally:
            if HAS_TQDM and hasattr(pbar, "close"):
                pbar.close()
        persist()
        pending = nq

    # ---- Fase 2: Unpaywall ----
    if "2" in enabled and pending:
        print(f"\n=== FASE 2: Unpaywall ===")
        nq = []
        pbar = _progress(pending, desc="Fase 2 UPW  ", total=len(pending))
        try:
            for item in pbar:
                content, reason = try_unpaywall(item["doi"], mailto, verbose)
                set_state(item, "unpaywall", bool(content), reason)
                if content:
                    save_success(item, content, "unpaywall")
                else:
                    nq.append(item)
                _set_postfix(
                    pbar, ok=stats["unpaywall"], pend=len(nq), current=item["mid"][:22]
                )
                time.sleep(DELAY_BETWEEN_ITEMS)
        finally:
            if HAS_TQDM and hasattr(pbar, "close"):
                pbar.close()
        persist()
        pending = nq

    # ---- Fase 3: pyPaperFlow ----
    if "3" in enabled and pending:
        if shutil.which("paperflow") is None:
            print(f"\n=== FASE 3: pyPaperFlow ===")
            print(f"  [!] paperflow CLI no está en PATH.")
            print(f"  [!] Saltando fase. Para instalarlo:")
            print(f"      pip uninstall pyPaperFlow")
            print(f"      pip install pyPaperFlow")
            # pasar al siguiente bloque sin perder items
        else:
            print(f"\n=== FASE 3: pyPaperFlow ===")
            nq = []
            available = True
            pbar = _progress(pending, desc="Fase 3 PPF  ", total=len(pending))
            try:
                for item in pbar:
                    if not available:
                        nq.append(item)
                        _set_postfix(
                            pbar,
                            ok=stats["pypaperflow"],
                            pend=len(nq),
                            skipped=1,
                            current=item["mid"][:22],
                        )
                        continue
                    content, reason = try_pypaperflow(
                        item["url"], item["doi"], verbose, base_dir
                    )
                    if reason.startswith("no_instalado"):
                        available = False
                        _emit(f"  [!] pyPaperFlow no instalado, saltando")
                        nq.append(item)
                        continue
                    set_state(item, "pypaperflow", bool(content), reason)
                    if content:
                        save_success(item, content, "pypaperflow")
                    else:
                        nq.append(item)
                    _set_postfix(
                        pbar,
                        ok=stats["pypaperflow"],
                        pend=len(nq),
                        current=item["mid"][:22],
                    )
                    time.sleep(DELAY_BETWEEN_ITEMS)
            finally:
                if HAS_TQDM and hasattr(pbar, "close"):
                    pbar.close()
            persist()
            pending = nq

    # ---- Fase 4: Zotero OA ----
    if "4" in enabled and pending:
        print(f"\n=== FASE 4: Zotero OA ===")
        nq = []
        pbar = _progress(pending, desc="Fase 4 Zot  ", total=len(pending))
        try:
            for item in pbar:
                content, reason = try_zotero_oa(item["doi"], verbose)
                set_state(item, "zotero_oa", bool(content), reason)
                if content:
                    save_success(item, content, "zotero_oa")
                else:
                    nq.append(item)
                _set_postfix(
                    pbar, ok=stats["zotero_oa"], pend=len(nq), current=item["mid"][:22]
                )
                time.sleep(DELAY_BETWEEN_ITEMS)
        finally:
            if HAS_TQDM and hasattr(pbar, "close"):
                pbar.close()
        persist()
        pending = nq

    # ---- Fase 6: Chromium ----
    if "6" in enabled and pending:
        print(f"\n=== FASE 6: Chromium vía CDP ===")

        if not cdp_url:
            print("  [!] ERROR: Fase 6 requiere --cdp-url.")
            print(
                "      No se lanza Chromium de Playwright (es detectado por Cloudflare)."
            )
            print("      Arranca Edge/Chrome con debugging remoto y vuelve a correr.")
            print(f"      {len(pending)} items quedan pendientes.")
        else:
            # NOTA: NO usamos Stealth().use_sync() aquí.
            # Stealth está diseñado para parchear un browser lanzado por Playwright.
            # Al conectar por CDP a un browser externo (Edge real), sus hooks
            # se cuelgan y revientan a los 180s. Además, un Edge real con
            # perfil warmeado ya es indetectable por Cloudflare.
            ensure_edge_running(cdp_url)

            with sync_playwright() as p:
                try:
                    browser = p.chromium.connect_over_cdp(cdp_url, timeout=15000)
                except Exception as e:
                    print(f"  [!] No se pudo conectar a CDP: {e}")
                    print(f"      Verifica que Edge esté corriendo en {cdp_url}")
                    print(f"      Prueba: curl {cdp_url}/json/version")
                    return

                contexts = browser.contexts
                if contexts:
                    context = contexts[0]
                else:
                    print(
                        "  [!] El browser CDP no tiene contextos. Abriendo uno nuevo..."
                    )
                    context = browser.new_context(
                        viewport={"width": 1920, "height": 1080},
                        accept_downloads=True,
                    )

                # Diagnóstico del contexto
                cookies = context.cookies()
                pages = context.pages
                print(f"  ✓ Conectado a Edge vía CDP: {cdp_url}")
                print(f"  ✓ Contextos: {len(contexts)}")
                print(f"  ✓ Pestañas abiertas: {len(pages)}")
                print(f"  ✓ Cookies en el contexto: {len(cookies)}")

                if len(cookies) < 10:
                    print(f"  ⚠ ADVERTENCIA: pocas cookies ({len(cookies)}).")
                    print(f"    Cloudflare probablemente te pida verificación.")
                    print(f"    Calienta el perfil: visita los sitios manualmente")
                    print(f"    y resuelve el captcha UNA VEZ.")

                # Cerrar pestañas residuales del warmup (excepto la primera)
                # para no saturar el browser. La primera la dejamos como "ancla".
                if len(pages) > 3:
                    print(
                        f"  → Cerrando {len(pages) - 1} pestañas residuales del warmup..."
                    )
                    for pg in pages[1:]:
                        try:
                            pg.close()
                        except Exception:
                            pass

                nq = []
                pbar = _progress(pending, desc="Fase 6 Chr  ", total=len(pending))
                try:
                    for item in pbar:
                        content, reason = _resolve_with_strategies(
                            item, context, verbose
                        )
                        if not content:
                            content, reason = try_chromium(item, context, verbose)
                        set_state(item, "chromium", bool(content), reason)
                        if content:
                            save_success(item, content, "chromium_stealth")
                        else:
                            nq.append(item)
                            _emit(f"  ✗ {item['m']['nombre'][:38]:38} → {reason}")
                        _set_postfix(
                            pbar,
                            ok=stats["chromium_stealth"],
                            fail=len(nq),
                            current=item["mid"][:22],
                        )
                        time.sleep(DELAY_BETWEEN_ITEMS)
                finally:
                    if HAS_TQDM and hasattr(pbar, "close"):
                        pbar.close()

                # No cerramos browser: es el Edge del usuario
                # browser.close() ← NO

                # ---- Fase 6.5: Sci-Hub vía Chromium (sobre los que fallaron) ----
                if "5" in enabled and nq:
                    print(f"\n=== FASE 6.5: Sci-Hub vía Chromium ===")
                    mirrors_activos = _probe_scihub_mirrors(mirrors_scihub)
                    if not mirrors_activos:
                        print(
                            "  [!] Ningún mirror de Sci-Hub responde. Sub-fase saltada."
                        )
                    else:
                        nq_sh = []
                        pbar = _progress(nq, desc="Fase 6.5 SH  ", total=len(nq))
                        try:
                            for item in pbar:
                                content, reason = try_scihub(
                                    item["doi"], mirrors_activos, context, verbose, pbar
                                )
                                set_state(item, "scihub", bool(content), reason)
                                if content:
                                    save_success(item, content, "scihub")
                                else:
                                    nq_sh.append(item)
                                _set_postfix(
                                    pbar,
                                    ok=stats["scihub"],
                                    pend=len(nq_sh),
                                    current=item["mid"][:22],
                                )
                                time.sleep(DELAY_BETWEEN_ITEMS)
                        finally:
                            if HAS_TQDM and hasattr(pbar, "close"):
                                pbar.close()
                        nq = nq_sh

            persist()

            # ---- Fase 6.6: re-pasar Unpaywall sobre DOIs nuevos ----
            nuevos = [
                i for i in nq if i.get("doi") and i.get("doi_source") == "chromium_meta"
            ]
            if nuevos:
                print(f"\n=== FASE 6.6: Unpaywall sobre {len(nuevos)} DOIs nuevos ===")
                # Unpaywall
                nq2 = []
                pbar = _progress(nuevos, desc="Fase 6.6 UPW", total=len(nuevos))
                try:
                    for item in pbar:
                        content, reason = try_unpaywall(item["doi"], mailto, verbose)
                        set_state(item, "unpaywall", bool(content), reason)
                        if content:
                            save_success(item, content, "unpaywall")
                        else:
                            nq2.append(item)
                        _set_postfix(
                            pbar,
                            ok=stats["unpaywall"],
                            pend=len(nq2),
                            current=item["mid"][:22],
                        )
                        time.sleep(DELAY_BETWEEN_ITEMS)
                finally:
                    if HAS_TQDM and hasattr(pbar, "close"):
                        pbar.close()
                persist()

    # ---- Resumen ----
    total = sum(stats.values())
    print(f"\n{'=' * 78}")
    print("RESUMEN FINAL")
    print(f"{'=' * 78}")
    print(f"  Total descargados : {total}")
    print(f"    ↳ Fase 0.5 (PDF directo) : {stats['direct_pdf']}")
    print(f"    ↳ Fase 1   (OpenAlex)    : {stats['openalex']}")
    print(f"    ↳ Fase 2   (Unpaywall)   : {stats['unpaywall']}")
    print(f"    ↳ Fase 3   (pyPaperFlow) : {stats['pypaperflow']}")
    print(f"    ↳ Fase 4   (Zotero OA)   : {stats['zotero_oa']}")
    print(f"    ↳ Fase 6.5 (Sci-Hub)     : {stats['scihub']}")
    if "5" in enabled:
        print(f"      (mirrors activos: {len(mirrors_scihub)})")
    print(f"    ↳ Fase 6   (Chromium)    : {stats['chromium_stealth']}")
    print(f"  Ya existían       : {skipped}")
    print(f"  Sin DOI resoluble : {sum(1 for i in queue if not i.get('doi'))}")

    fallos = {}
    for mid, st in state.items():
        for fase, info in st.items():
            if isinstance(info, dict) and "ok" in info and not info["ok"]:
                key = f"{fase}: {info.get('razon', '?')}"
                fallos[key] = fallos.get(key, 0) + 1
    if fallos:
        print(f"\n--- RAZONES DE FALLO (top 15) ---")
        for razon, n in sorted(fallos.items(), key=lambda x: -x[1])[:15]:
            print(f"  [{n:2}] {razon}")

    fallidos = [
        f"{i['m']['nombre'][:38]:38} {i['url']}"
        for i in queue
        if not any(d.get("status") == "downloaded" for d in refs.get(i["mid"], []))
    ]
    if fallidos:
        print(f"\n--- DESCARGAS MANUALES ({len(fallidos)}) ---")
        for f in fallidos:
            print(f"  {f}")
    print(f"{'=' * 78}\n")


# =============================================================================
# EXTRACCIÓN DE TEXTO
# =============================================================================


def extract_text_from_pdfs(
    base_dir: Path = BASE,
    max_pages: int = 20,
    max_chars: int = 100000,
    methods: list | None = None,
    force: bool = False,
):
    try:
        import pdfplumber
    except ImportError:
        raise SystemExit("Necesitas: pip install pdfplumber")

    methods = {m.lower() for m in (methods or [])}
    pdf_dir = base_dir / "data" / "pdfs"
    refs = load_json(base_dir / "data" / "pdf_refs.json")
    texts = load_json(base_dir / "data" / "pdf_texts.json")

    # --- Escanear la carpeta pdfs/ y registrar PDFs huérfanos (descargas manuales) ---
    # El pipeline registra en pdf_refs.json solo lo que él mismo descarga. Si el
    # usuario dejó PDFs a mano en data/pdfs/, no aparecen en refs y extract los
    # ignoraría. Aquí los detectamos y los añadimos con un mid derivado del nombre.
    if pdf_dir.exists():
        known_files = {
            d.get("file") for docs in refs.values() for d in docs if d.get("file")
        }
        for fpath in sorted(pdf_dir.glob("*.pdf")):
            fname = fpath.name
            if fname in known_files:
                continue
            # El pipeline nombra {mid}_0.pdf; si no coincide, usar el stem del archivo
            m = re.match(r"^(.+)_\d+\.pdf$", fname)
            mid = m.group(1) if m else fpath.stem
            refs.setdefault(mid, []).append(
                {
                    "url": "",
                    "file": fname,
                    "status": "downloaded",
                    "source": "manual",
                }
            )
            known_files.add(fname)
            _emit(f"  -> PDF manual detectado: {fname} (mid={mid})")
        if refs:
            save_json(base_dir / "data" / "pdf_refs.json", refs)

    # Filtrar solo los que tienen descargas válidas y no están en texts
    todo = []
    for mid, docs in refs.items():
        if methods and mid.lower() not in methods:
            continue
        if not force and mid in texts:
            continue
        valid_docs = [d for d in docs if d.get("status") == "downloaded"]
        if valid_docs:
            todo.append((mid, valid_docs))

    count, errors = 0, []
    pbar = _progress(todo, desc="Extraer texto", total=len(todo))
    for mid, docs in pbar:
        texts[mid] = []
        for doc in docs:
            fpath = pdf_dir / doc["file"]
            if not fpath.exists():
                texts[mid].append({"file": doc["file"], "status": "file_not_found"})
                continue
            try:
                with pdfplumber.open(fpath) as pdf:
                    pages = min(len(pdf.pages), max_pages)
                    text = "\n\n".join(
                        page.extract_text() or "" for page in pdf.pages[:pages]
                    )[:max_chars]
                    texts[mid].append(
                        {
                            "file": doc["file"],
                            "url": doc["url"],
                            "status": "extracted",
                            "pages": pages,
                            "chars": len(text),
                            "text": text,
                        }
                    )
                count += 1
            except Exception as e:
                texts[mid].append(
                    {
                        "file": doc["file"],
                        "url": doc["url"],
                        "status": "error",
                        "error": str(e)[:200],
                    }
                )
                errors.append(f"{mid} {doc['file']}: {str(e)[:60]}")
        _set_postfix(pbar, ok=count, fail=len(errors))
    save_json(base_dir / "data" / "pdf_texts.json", texts)
    print(f"\n{count} PDFs procesados, {len(errors)} errores")


# =============================================================================
# EXTRACCIÓN DE PASOS CON TOGETHER AI (JSON Schema estructurado)
# =============================================================================

# --- Sistema de clasificación metodológica en 5 fases ---
SYSTEM_PROMPT = """Eres un experto en metodología de investigación cualitativa y extracción de datos estructurados.
Tu tarea es analizar el texto proporcionado (extraído de un documento académico) e identificar los pasos procedimentales específicos del método cualitativo descrito.

### OBJETIVO
Extraer, clasificar metodológicamente y estructurar las acciones investigativas en un formato JSON estricto para permitir la replicación exacta del estudio.

### DEFINICIONES DE FASES PROCEDIMENTALES
Clasifica los pasos extraídos estrictamente en las siguientes categorías. Si una categoría no se menciona, devuelve un arreglo vacío [].
1. pre_analisis: Curación de literatura inicial, marco teórico, diseño del estudio, estrategias de muestreo, recolección de datos y organización física/digital de las fuentes.
2. interpretacion_fuentes: Exploración inicial, inmersión en los datos, primeras impresiones, lecturas superficiales u ordenamiento de transcripciones.
3. analisis_profundo: Aplicación rigurosa de la técnica: codificación (abierta, axial, selectiva), categorización, triangulación, uso de software cualitativo y descubrimiento de patrones.
4. sintesis: Redacción de memos analíticos, construcción de modelos teóricos, integración de resultados teóricos/empíricos y elaboración del reporte o borrador final.
5. pasos_adicionales: Cualquier otro procedimiento técnico, validación por pares o proceso iterativo crucial para la reproducción metodológica.

### REGLAS DE EXTRACCIÓN
- Convierte las descripciones teóricas en ACCIONES CONCRETAS (ej. en lugar de "El paradigma asume la iteración", extrae "Realizar ciclos iterativos de comparación de datos").
- Mantén un "orden_global" secuencial continuo a través de todas las fases (1, 2, 3...).
- Extrae los metadatos basándote en cabeceras o referencias iniciales presentes en el texto.
"""

JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "metadatos": {
            "type": "object",
            "properties": {
                "tipo_documento": {
                    "type": "string",
                    "enum": ["paper", "tesis", "libro", "desconocido"],
                },
                "titulo": {"type": ["string", "null"]},
                "autores": {"type": "array", "items": {"type": "string"}},
                "anio_publicacion": {"type": ["string", "null"]},
            },
            "required": ["tipo_documento", "titulo", "autores", "anio_publicacion"],
        },
        "metodo_identificado": {"type": "string"},
        "fases_procedimentales": {
            "type": "object",
            "properties": {
                "pre_analisis": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "orden_global": {"type": "integer"},
                            "accion": {"type": "string"},
                        },
                        "required": ["orden_global", "accion"],
                    },
                },
                "interpretacion_fuentes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "orden_global": {"type": "integer"},
                            "accion": {"type": "string"},
                        },
                        "required": ["orden_global", "accion"],
                    },
                },
                "analisis_profundo": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "orden_global": {"type": "integer"},
                            "accion": {"type": "string"},
                        },
                        "required": ["orden_global", "accion"],
                    },
                },
                "sintesis": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "orden_global": {"type": "integer"},
                            "accion": {"type": "string"},
                        },
                        "required": ["orden_global", "accion"],
                    },
                },
                "pasos_adicionales": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "orden_global": {"type": "integer"},
                            "accion": {"type": "string"},
                        },
                        "required": ["orden_global", "accion"],
                    },
                },
            },
            "required": [
                "pre_analisis",
                "interpretacion_fuentes",
                "analisis_profundo",
                "sintesis",
                "pasos_adicionales",
            ],
        },
        "confianza_extraccion": {"type": "string", "enum": ["alta", "media", "baja"]},
        "notas_limitaciones": {"type": "string"},
    },
    "required": [
        "metadatos",
        "metodo_identificado",
        "fases_procedimentales",
        "confianza_extraccion",
        "notas_limitaciones",
    ],
}

PHASE_KEYS = [
    "pre_analisis",
    "interpretacion_fuentes",
    "analisis_profundo",
    "sintesis",
    "pasos_adicionales",
]


def extract_steps_together(
    base_dir: Path = BASE,
    model: str = "deepseek-ai/DeepSeek-V4-Flash-0731",
    api_key: str = "",
    methods: list | None = None,
    force: bool = False,
):
    """Llama a Together AI con JSON Schema estructurado.

    ``methods``: lista opcional de ids de método a procesar. Si se omite,
    se procesan todos los que aún no tienen un resultado válido.

    ``force``: re-procesa aunque el método ya tenga un resultado válido.
    """
    if not api_key:
        api_key = os.environ.get("TOGETHER_API_KEY")
    if not api_key:
        raise SystemExit(
            "Necesitas: export TOGETHER_API_KEY=tu_clave "
            "(obtén en https://api.together.xyz/)"
        )

    methods = methods or []
    methods = {m.lower() for m in methods}
    all_methods = load_json(base_dir / "data" / "metodos_completos.json")
    texts = load_json(base_dir / "data" / "pdf_texts.json")
    steps = load_json(base_dir / "data" / "procedural_steps.json")

    if not all_methods:
        raise SystemExit("No encontré metodos_completos.json.")

    # Filtrar los que realmente se van a procesar.
    # Un mid se reprocesa si NO tiene un resultado válido (error, parse_error,
    # o nunca se procesó). Así los fallos se reintentan en la siguiente ronda.
    todo = []
    for m in all_methods:
        mid = _mid_of(m)
        if methods and mid.lower() not in methods:
            continue
        if not force and _steps_ok(steps, mid):
            continue
        if mid not in texts or not texts[mid]:
            continue
        # Solo si tiene al menos un texto extraído
        if not any(
            t.get("status") == "extracted" and t.get("text") for t in texts[mid]
        ):
            continue
        todo.append(m)

    count, errors = 0, []
    pbar = _progress(todo, desc="Together AI ", total=len(todo))
    for m in pbar:
        mid = _mid_of(m)
        # Limpiar entradas viejas (errores de rondas anteriores) antes de reintentar
        steps[mid] = []

        for i, t in enumerate(texts[mid]):
            if t.get("status") != "extracted" or not t.get("text"):
                steps[mid].append(
                    {
                        "file": t.get("file"),
                        "status": "skipped",
                        "razon": t.get("status", "sin texto"),
                    }
                )
                continue

            try:
                log(True, f"{mid} [{i}] llamando a Together AI (JSON Schema)")
                r = http_post(
                    TOGETHER_CHAT_ENDPOINT,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {
                                "role": "user",
                                "content": f"--- DOCUMENTO ---\n{t['text']}",
                            },
                        ],
                        "response_format": {
                            "type": "json_object",
                            "schema": JSON_SCHEMA,
                        },
                        "max_tokens": 4000,
                        "temperature": 0.1,
                        "top_p": 0.9,
                        "repetition_penalty": 1.0,
                    },
                    timeout=120,
                )

                if r.status_code != 200:
                    raise ValueError(f"HTTP {r.status_code}: {r.text[:200]}")

                result = r.json()
                output = (
                    result.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    .strip()
                )

                try:
                    parsed = json.loads(output)
                except json.JSONDecodeError:
                    parsed = {
                        "metodo_identificado": m["nombre"],
                        "raw_output": output,
                        "parse_error": True,
                    }

                steps[mid].append(
                    {
                        "file": t["file"],
                        "status": "processed",
                        "resultado": parsed,
                    }
                )
                count += 1
                _set_postfix(pbar, ok=count, fail=len(errors))
                time.sleep(1)
            except Exception as e:
                steps[mid].append(
                    {
                        "file": t["file"],
                        "status": "error",
                        "error": str(e)[:200],
                    }
                )
                errors.append(f"{mid} {t['file']}: {str(e)[:100]}")
                _set_postfix(pbar, ok=count, fail=len(errors))

    save_json(base_dir / "data" / "procedural_steps.json", steps)
    print(f"\n{count} métodos procesados, {len(errors)} errores")


# =============================================================================
# MERGE: integra fases estructuradas en metodos_completos.json
# =============================================================================


def _flatten_phases(fases: dict) -> list:
    """Aplana el dict de fases a una lista ordenada por orden_global."""
    flat = []
    for fase_name in PHASE_KEYS:
        for step in fases.get(fase_name) or []:
            if isinstance(step, dict) and "accion" in step:
                flat.append(
                    {
                        "fase": fase_name,
                        "orden_global": step.get("orden_global"),
                        "paso": step["accion"],
                    }
                )
    flat.sort(key=lambda x: x.get("orden_global") or 9999)
    return flat


def merge_steps_into_content(
    base_dir: Path = BASE,
    methods: list | None = None,
    force: bool = False,
):
    """
    Integra los pasos extraídos (con estructura de fases) en metodos_completos.json.

    Por cada método, añade:
      - pasos_procedimentales: dict con las 5 fases (estructura rica)
      - pasos_procedimentales_lineales: lista plana ordenada
      - metadatos_extraidos: título, autores, año, tipo, confianza, notas

    ``methods``: lista opcional de ids de método a procesar.
    ``force``: re-integra aunque el método ya tenga pasos estructurados.
    """
    all_methods = load_json(base_dir / "data" / "metodos_completos.json")
    steps = load_json(base_dir / "data" / "procedural_steps.json")
    if not all_methods:
        raise SystemExit("No encontré metodos_completos.json")

    methods_filter = {m.lower() for m in (methods or [])}

    updated = 0
    missing = []
    pbar = _progress(all_methods, desc="Merge      ", total=len(all_methods))
    for m in pbar:
        mid = _mid_of(m)
        if methods_filter and mid.lower() not in methods_filter:
            continue
        if not force and m.get("pasos_procedimentales"):
            # Ya integrado; sin force no se re-escribe
            _set_postfix(pbar, ok=updated)
            continue
        if not _steps_ok(steps, mid):
            missing.append(mid)
            _set_postfix(pbar, ok=updated)
            continue

        # Tomar el primer documento procesado con éxito
        merged = None
        for doc in steps[mid]:
            if doc.get("status") != "processed":
                continue
            res = doc.get("resultado") or {}
            if not isinstance(res, dict):
                continue
            if res.get("parse_error"):
                continue
            merged = res
            break

        if not merged:
            _set_postfix(pbar, ok=updated)
            continue

        # 1) Estructura por fases (cruda del LLM)
        fases = merged.get("fases_procedimentales") or {}
        m["pasos_procedimentales"] = fases
        m["pasos_procedimentales_fuente"] = "together_ai"

        # 2) Lista plana ordenada para consumo rápido
        m["pasos_procedimentales_lineales"] = _flatten_phases(fases)

        # 3) Metadatos enriquecidos
        m["metadatos_extraidos"] = {
            "tipo_documento": (merged.get("metadatos") or {}).get("tipo_documento"),
            "titulo": (merged.get("metadatos") or {}).get("titulo"),
            "autores": (merged.get("metadatos") or {}).get("autores") or [],
            "anio_publicacion": (merged.get("metadatos") or {}).get("anio_publicacion"),
            "metodo_identificado": merged.get("metodo_identificado"),
            "confianza": merged.get("confianza_extraccion"),
            "notas_limitaciones": merged.get("notas_limitaciones"),
        }

        updated += 1
        _set_postfix(pbar, ok=updated)

    save_json(base_dir / "data" / "metodos_completos.json", all_methods)
    print(
        f"\nmetodos_completos.json actualizado: {updated} métodos con pasos estructurados"
    )
    if missing:
        print(f"  -> Faltan {len(missing)} métodos: {', '.join(missing)}")
    return missing


def _steps_ok(steps: dict, mid: str) -> bool:
    """True si el mid tiene al menos un resultado procesado y sin parse_error."""
    for doc in steps.get(mid) or []:
        if doc.get("status") != "processed":
            continue
        res = doc.get("resultado") or {}
        if isinstance(res, dict) and not res.get("parse_error"):
            return True
    return False


def retry_until_complete(
    base_dir: Path = BASE,
    model: str = "deepseek-ai/DeepSeek-V4-Flash-0731",
    api_key: str = "",
    max_rounds: int = 20,
    methods: list | None = None,
    force: bool = False,
):
    """Bucle de reintentos: steps (Together) + merge hasta completar los 48.

    Cada ronda:
      1. extract_steps_together reintenta los mids sin resultado válido
         (errores, parse_errors y los que nunca se procesaron).
      2. merge_steps_into_content integra lo que ya tiene pasos y devuelve
         los mids que todavía faltan.
      3. Si faltan, se repite la ronda (volviendo a Together).

    ``methods``: lista opcional de ids de método a procesar.
    ``force``: re-procesa aunque el método ya tenga un resultado válido.
    """
    for ronda in range(1, max_rounds + 1):
        print(f"\n{'=' * 60}")
        print(f"RONDA {ronda} — reintentos Together + merge")
        print(f"{'=' * 60}")

        extract_steps_together(base_dir, model, api_key, methods=methods, force=force)
        missing = merge_steps_into_content(base_dir)

        if not missing:
            print("\n✓ Todos los métodos tienen pasos estructurados.")
            return
        print(f"\n  -> Quedan {len(missing)} métodos sin pasos; reintentando...")

    print(f"\n[!] Se alcanzó el máximo de {max_rounds} rondas sin completar.")


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    ap.add_argument(
        "cmd", choices=["download", "extract", "steps", "merge", "retry", "all"]
    )
    ap.add_argument(
        "--model",
        default="deepseek-ai/DeepSeek-V4-Flash-0731",
        help="Modelo de Together AI (recomendado: deepseek-ai/DeepSeek-V4-Flash-0731)",
    )
    ap.add_argument("--api-key", default="")
    ap.add_argument(
        "--mailto", default="", help="Email Unpaywall (o export UNPAYWALL_EMAIL)"
    )
    ap.add_argument("--mirrors", default="", help="Mirrors Sci-Hub separados por coma")
    ap.add_argument(
        "--phases",
        default="",
        help=f"Fases a ejecutar (coma-separadas). Descarga: {','.join(PHASE_NAMES)}; "
        f"post-descarga: {','.join(POST_PHASES)} (o 7, 8, 9). "
        "Ej: --phases 0,1,7,8,9",
    )
    ap.add_argument(
        "--methods",
        default="",
        help="Ids de método a procesar (coma-separados). Ej: --methods etnometodologia,fenomenografia",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Re-descargar y/o re-extraer aunque ya exista (con --methods).",
    )
    ap.add_argument(
        "--url",
        default="",
        help="Reemplaza la URL de métodos concretos (formato mid:url, separados por coma). "
        "Ej: --url etnometodologia:https://x.pdf,fenomenografia:https://y.pdf",
    )
    ap.add_argument(
        "--cdp-url",
        default="",
        help="URL CDP de Chrome real (ej: http://localhost:9222)",
    )
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    mirrors = (
        [m.strip() for m in a.mirrors.split(",") if m.strip()]
        if a.mirrors
        else DEFAULT_SCIHUB_MIRRORS
    )
    enabled = (
        set(p.strip() for p in a.phases.split(",") if p.strip()) if a.phases else None
    )
    if enabled is not None:
        enabled = {PHASE_ALIASES.get(p, p) for p in enabled}
        desconocidas = enabled - set(ALL_PHASES)
        if desconocidas:
            print(f"  [!] Fases desconocidas ignoradas: {sorted(desconocidas)}")
            enabled &= set(ALL_PHASES)
    methods = [m.strip() for m in a.methods.split(",") if m.strip()] or None

    url_overrides = {}
    if a.url:
        for part in a.url.split(","):
            if ":" in part:
                mid, u = part.split(":", 1)
                url_overrides[mid.strip()] = u.strip()

    if a.cmd in ("download", "all"):
        dl = enabled if enabled is None else enabled & set(PHASE_NAMES)
        if dl is not None and not dl:
            print("  (ninguna fase de descarga en --phases; se omite download)")
        else:
            print("Pipeline de descargas...")
            download_pdfs(
                BASE,
                mailto=a.mailto,
                verbose=a.verbose,
                mirrors_scihub=mirrors,
                enabled_phases=dl,
                cdp_url=a.cdp_url,
                methods=methods,
                force=a.force,
                url_overrides=url_overrides,
            )
    if a.cmd in ("extract", "all") and (enabled is None or "extract" in enabled):
        print("\nExtrayendo texto de PDFs...")
        extract_text_from_pdfs(BASE, methods=methods, force=a.force)
    if a.cmd in ("steps", "all") and (enabled is None or "steps" in enabled):
        print("\nExtrayendo pasos con Together AI (JSON Schema)...")
        extract_steps_together(BASE, a.model, a.api_key, methods=methods, force=a.force)
    if a.cmd in ("merge", "all") and (enabled is None or "merge" in enabled):
        print("\nIntegrando pasos en metodos_completos.json...")
        merge_steps_into_content(BASE, methods=methods, force=a.force)
    if a.cmd == "retry":
        print("\nBucle de reintentos hasta completar todos los métodos...")
        retry_until_complete(BASE, a.model, a.api_key, methods=methods, force=a.force)
