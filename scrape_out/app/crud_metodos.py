#!/usr/bin/env python3
"""CRUD de métodos para la app de metodologías cualitativas.

Crea, edita o elimina métodos en data/metodos_completos.json y reconstruye la
app automáticamente (build_app.py) para que los cambios se reflejen al instante.

Uso:
  python crud_metodos.py list
  python crud_metodos.py show <id>
  python crud_metodos.py create --nombre "..." [opciones]
  python crud_metodos.py edit <id> --campo "valor" ...
  python crud_metodos.py delete <id>

Tras create/edit/delete se re-ejecuta build_app.py automáticamente
(--no-rebuild para saltarlo). Con --clasificar también se re-clasifican las
tareas del método con DeepSeek (requiere TOGETHER_API_KEY).

Opciones de create/edit:
  --nombre --familia --categoria --subcategoria --ideal-para
  --descripcion-caso --hallazgos --ejemplo-titulo --ejemplo-url
  --tipo-documento --titulo --autores "a, b" --anio 2024 --metodo
  --confianza --notas --image-prompt
  --pasos-json <archivo>   JSON con pasos_procedimentales (fases -> pasos)
  --imagen <archivo.png>   copia la imagen a data/img/<id>.png
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_ROOT = HERE.parent

FAMILIAS = ("Analisis Interno", "Analisis Formal")
PHASE_KEYS = (
    "pre_analisis",
    "interpretacion_fuentes",
    "analisis_profundo",
    "sintesis",
    "pasos_adicionales",
)

# flag CLI -> ruta dentro del método
CAMPOS = {
    "nombre": "nombre",
    "familia": "familia",
    "categoria": "categoria",
    "subcategoria": "subcategoria",
    "ideal_para": "ideal_para",
    "descripcion_caso": "descripcion_caso",
    "hallazgos": "hallazgos",
    "image_prompt": "image_prompt",
    "ejemplo_titulo": "ejemplo.titulo",
    "ejemplo_url": "ejemplo.url",
    "tipo_documento": "metadatos_extraidos.tipo_documento",
    "titulo": "metadatos_extraidos.titulo",
    "autores": "metadatos_extraidos.autores",
    "anio": "metadatos_extraidos.anio_publicacion",
    "metodo": "metadatos_extraidos.metodo_identificado",
    "confianza": "metadatos_extraidos.confianza",
    "notas": "metadatos_extraidos.notas_limitaciones",
}


def slug(s):
    s = s.lower()
    for a, b in (
        ("á", "a"),
        ("é", "e"),
        ("í", "i"),
        ("ó", "o"),
        ("ú", "u"),
        ("ñ", "n"),
    ):
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "metodo"


def load(root):
    return json.load(open(root / "data" / "metodos_completos.json", encoding="utf-8"))


def save(root, metodos):
    p = root / "data" / "metodos_completos.json"
    json.dump(metodos, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("->", p)


def find(metodos, mid):
    return next((m for m in metodos if m["id"] == mid), None)


def set_path(m, path, value):
    parts = path.split(".")
    cur = m
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def _flatten(fases):
    """Convierte pasos_procedimentales (dict de fases) en la lista lineal."""
    flat = []
    for fase in PHASE_KEYS:
        for step in fases.get(fase) or []:
            if isinstance(step, dict) and "accion" in step:
                flat.append(
                    {
                        "fase": fase,
                        "orden_global": step.get("orden_global"),
                        "paso": step["accion"],
                    }
                )
    flat.sort(key=lambda x: x.get("orden_global") or 9999)
    return flat


def _copy_imagen(root, mid, path):
    if not path:
        return None
    src = Path(path)
    if not src.exists():
        print(f"  ! imagen no encontrada: {src}", file=sys.stderr)
        return None
    dst = root / "data" / "img" / (mid.replace("-", "_") + ".png")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dst)
    print("  imagen ->", dst)
    return f"data/img/{mid.replace('-', '_')}.png"


def rebuild(root, mid, clasificar, no_rebuild):
    if no_rebuild:
        print("\n(--no-rebuild) No se reconstruyó la app.")
        return
    print("\nReconstruyendo la app...")
    if clasificar:
        print("  classify_tasks.py --solo", mid)
        subprocess.run(
            [
                sys.executable,
                str(HERE / "classify_tasks.py"),
                "--root",
                str(root),
                "--solo",
                mid,
            ],
            check=False,
        )
    print("  build_app.py")
    subprocess.run(
        [sys.executable, str(HERE / "build_app.py"), str(root), str(HERE)],
        check=False,
    )


def cmd_list(root, args):
    metodos = load(root)
    print(f"{len(metodos)} métodos:")
    for m in metodos:
        print(
            f"  {m['id']:<55} {m['nombre']}  "
            f"[{m['familia']} / {m['categoria']} / {m['subcategoria']}]"
        )


def cmd_show(root, args):
    metodos = load(root)
    m = find(metodos, args.id)
    if not m:
        sys.exit(f"No existe el método '{args.id}'.")
    print(json.dumps(m, ensure_ascii=False, indent=2))


def _nuevo_metodo(args, mid):
    return {
        "id": mid,
        "nombre": args.nombre,
        "familia": args.familia or "Analisis Interno",
        "categoria": args.categoria or "",
        "subcategoria": args.subcategoria or "",
        "descripcion_caso": args.descripcion_caso or "",
        "ideal_para": args.ideal_para or "",
        "ejemplo": {"titulo": args.ejemplo_titulo or "", "url": args.ejemplo_url or ""},
        "hallazgos": args.hallazgos or "",
        "image_prompt": args.image_prompt or "",
        "imagen": None,
        "pasos_procedimentales": {},
        "pasos_procedimentales_fuente": "manual",
        "pasos_procedimentales_lineales": [],
        "metadatos_extraidos": {
            "tipo_documento": args.tipo_documento or "",
            "titulo": args.titulo or "",
            "autores": [
                a.strip() for a in (args.autores or "").split(",") if a.strip()
            ],
            "anio_publicacion": args.anio,
            "metodo_identificado": args.metodo or args.nombre,
            "confianza": args.confianza or "media",
            "notas_limitaciones": args.notas or "",
        },
    }


def cmd_create(root, args):
    metodos = load(root)
    if not args.nombre:
        sys.exit("create requiere --nombre.")
    mid = slug(args.nombre)
    if find(metodos, mid):
        sys.exit(f"Ya existe un método con id '{mid}'. Usa edit.")
    if args.familia and args.familia not in FAMILIAS:
        print(f"  ! familia '{args.familia}' no está en {FAMILIAS}", file=sys.stderr)
    m = _nuevo_metodo(args, mid)
    if args.pasos_json:
        pasos = json.load(open(args.pasos_json, encoding="utf-8"))
        m["pasos_procedimentales"] = pasos
        m["pasos_procedimentales_fuente"] = "manual"
        m["pasos_procedimentales_lineales"] = _flatten(pasos)
    m["imagen"] = _copy_imagen(root, mid, args.imagen)
    metodos.append(m)
    save(root, metodos)
    print(f"Creado '{mid}'.")
    rebuild(root, mid, args.clasificar, args.no_rebuild)


def cmd_edit(root, args):
    metodos = load(root)
    m = find(metodos, args.id)
    if not m:
        sys.exit(f"No existe el método '{args.id}'.")
    for flag, path in CAMPOS.items():
        val = getattr(args, flag, None)
        if val is None:
            continue
        if flag == "autores":
            val = [a.strip() for a in val.split(",") if a.strip()]
        elif flag == "anio":
            val = int(val) if val else None
        set_path(m, path, val)
    if args.pasos_json:
        pasos = json.load(open(args.pasos_json, encoding="utf-8"))
        m["pasos_procedimentales"] = pasos
        m["pasos_procedimentales_fuente"] = "manual"
        m["pasos_procedimentales_lineales"] = _flatten(pasos)
    if args.imagen:
        m["imagen"] = _copy_imagen(root, args.id, args.imagen)
    save(root, metodos)
    print(f"Editado '{args.id}'.")
    rebuild(root, args.id, args.clasificar, args.no_rebuild)


def cmd_delete(root, args):
    metodos = load(root)
    m = find(metodos, args.id)
    if not m:
        sys.exit(f"No existe el método '{args.id}'.")
    metodos = [x for x in metodos if x["id"] != args.id]
    save(root, metodos)
    if args.borrar_imagen:
        img = root / "data" / "img" / (args.id.replace("-", "_") + ".png")
        if img.exists():
            img.unlink()
            print("  imagen borrada:", img)
    print(f"Eliminado '{args.id}'.")
    rebuild(root, args.id, args.clasificar, args.no_rebuild)


def _add_campos(p):
    p.add_argument("--nombre")
    p.add_argument("--familia")
    p.add_argument("--categoria")
    p.add_argument("--subcategoria")
    p.add_argument("--ideal-para", dest="ideal_para")
    p.add_argument("--descripcion-caso", dest="descripcion_caso")
    p.add_argument("--hallazgos")
    p.add_argument("--ejemplo-titulo", dest="ejemplo_titulo")
    p.add_argument("--ejemplo-url", dest="ejemplo_url")
    p.add_argument("--tipo-documento", dest="tipo_documento")
    p.add_argument("--titulo")
    p.add_argument("--autores")
    p.add_argument("--anio", type=int)
    p.add_argument("--metodo")
    p.add_argument("--confianza")
    p.add_argument("--notas")
    p.add_argument("--image-prompt", dest="image_prompt")


def main():
    # Atajo: `crud_metodos.py <id>` equivale a `show <id>`.
    # Busca el primer argumento posicional, ignorando flags y el valor de --root.
    args = sys.argv[1:]
    skip = False
    pos = None
    for a in args:
        if skip:
            skip = False
            continue
        if a == "--root":
            skip = True
            continue
        if not a.startswith("-"):
            pos = a
            break
    if pos and pos not in ("list", "show", "create", "edit", "delete"):
        sys.argv.insert(sys.argv.index(pos), "show")

    ap = argparse.ArgumentParser(description="CRUD de métodos para la app.")
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="Lista los métodos")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("show", help="Muestra un método")
    p.add_argument("id")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("create", help="Crea un método")
    _add_campos(p)
    p.add_argument("--pasos-json")
    p.add_argument("--imagen")
    p.add_argument("--clasificar", action="store_true")
    p.add_argument("--no-rebuild", action="store_true")
    p.set_defaults(fn=cmd_create)

    p = sub.add_parser("edit", help="Edita un método")
    p.add_argument("id")
    _add_campos(p)
    p.add_argument("--pasos-json")
    p.add_argument("--imagen")
    p.add_argument("--clasificar", action="store_true")
    p.add_argument("--no-rebuild", action="store_true")
    p.set_defaults(fn=cmd_edit)

    p = sub.add_parser("delete", help="Elimina un método")
    p.add_argument("id")
    p.add_argument("--borrar-imagen", action="store_true")
    p.add_argument("--clasificar", action="store_true")
    p.add_argument("--no-rebuild", action="store_true")
    p.set_defaults(fn=cmd_delete)

    a = ap.parse_args()
    a.fn(Path(a.root), a)


if __name__ == "__main__":
    main()
