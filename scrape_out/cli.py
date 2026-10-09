#!/usr/bin/env python3
"""Interfaz de línea de comandos de la aplicación.

Consulta el mismo modelo de datos que el frontend HTML (vía ``core``) y permite
explorar los métodos cualitativos desde la terminal.

Uso:
    python cli.py list [--familia "Analisis Interno"]
    python cli.py search "entrevistas"
    python cli.py show etnometodologia
    python cli.py build [--salida dist]
"""

import argparse
import sys
from pathlib import Path

from core import ENT, EST, FASES, build, leyenda, load_data

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def _cargar():
    metodos, refs, pdft = load_data(DATA)
    return build(metodos, refs, pdft), leyenda()


def _norm(s):
    import unicodedata

    return (
        unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode().lower()
    )


def cmd_list(args):
    metodos, _ = _cargar()
    if args.familia:
        metodos = [m for m in metodos if _norm(m["familia"]) == _norm(args.familia)]
    if not metodos:
        print("Sin métodos.")
        return
    for m in metodos:
        pasos = m["resumen"]["pasos"]
        print(f"{m['id']:<28} {m['nombre']:<40} {pasos:>3} pasos  {m['familia']}")


def cmd_search(args):
    metodos, _ = _cargar()
    q = _norm(args.query)
    hits = [
        m
        for m in metodos
        if q
        in _norm(
            " ".join(
                [
                    m["nombre"],
                    m["descripcion"],
                    m["ideal_para"],
                    m["estudio"]["titulo"],
                    m["subcategoria"],
                    m["categoria"],
                ]
            )
        )
    ]
    print(f"{len(hits)} resultado(s) para «{args.query}»:\n")
    for m in hits:
        print(f"  {m['id']:<28} {m['nombre']}")
        print(f"      {m['familia']} › {m['categoria']} › {m['subcategoria']}")


def cmd_show(args):
    metodos, ley = _cargar()
    m = next((x for x in metodos if x["id"] == args.id), None)
    if not m:
        print(f"Método «{args.id}» no encontrado.")
        sys.exit(1)
    e = m["estudio"]
    print(f"{m['nombre']}")
    print(f"  {m['familia']} › {m['categoria']} › {m['subcategoria']}")
    print(f"  Ideal para: {m['ideal_para']}")
    print(f"\n  {m['descripcion']}")
    print(f"\n  Estudio: {e['titulo']}")
    if e["autores"]:
        print(f"  Autores: {', '.join(e['autores'])}")
    if e["anio"]:
        print(f"  Año: {e['anio']}")
    print(f"  Confianza: {e['confianza']}")
    if e["url"]:
        print(f"  Enlace: {e['url']}")
    if m["entradas"]:
        print("  Entradas: " + ", ".join(ENT[x["id"]][0] for x in m["entradas"]))
    print("\n  Fases:")
    for f in m["fases"]:
        print(f"    {f['nombre']} ({len(f['pasos'])} pasos)")
        for p in f["pasos"]:
            est = EST[p["e"]][0]
            print(f"      {p['n']:>3}. [{est}] {p['t']}")


def cmd_build(args):
    from app import build_app

    argv = [sys.argv[0]]
    if args.salida:
        argv.append(args.salida)
    build_app.main(argv)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="Lista los métodos")
    p_list.add_argument("--familia", help="Filtra por familia")
    p_list.set_defaults(func=cmd_list)

    p_search = sub.add_parser("search", help="Busca métodos por texto")
    p_search.add_argument("query", help="Texto a buscar")
    p_search.set_defaults(func=cmd_search)

    p_show = sub.add_parser("show", help="Muestra el detalle de un método")
    p_show.add_argument("id", help="Identificador del método")
    p_show.set_defaults(func=cmd_show)

    p_build = sub.add_parser("build", help="Genera el frontend HTML")
    p_build.add_argument("--salida", help="Carpeta de salida")
    p_build.set_defaults(func=cmd_build)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
