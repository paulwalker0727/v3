"""Script training Tesseract pentru modele custom (buletine românești noi).

USAGE:
    # 1. Colectează sample-uri în training_data/{tip}/
    #    Fiecare sample: XXX.tif + XXX.gt.txt (ground truth: doar textul)
    #
    # 2. Rulează:
    python -m ai_scanare.training make --model ron_cimitir --start-from ron

DEPENDENȚE (instalare manuală):
    - Tesseract 5.x cu sursele de training: git clone https://github.com/tesseract-ocr/tesstrain
    - Setează variabila TESSTRAIN_DIR să indice către folderul clonat

ARHITECTURA:
    ron_cimitir e un model FINE-TUNED peste modelul standard „ron" (limba română).
    Adaugă vocabular specific: SPCLEP, DEPABD, SERIA/NR, CNP, formate de adresă românești,
    și fonturi tipice de tipar oficial românesc.

Dacă tesstrain / lstmtraining nu sunt instalate, scriptul emite un raport cu ce trebuie făcut.

Alternativ, modul „feedback_learning" din pachetul curent construiește un dicționar de
corecții din fiecare scanare confirmată de utilizator — funcționează fără tesstrain.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

# Structura de foldere
try:
    import database as _db
    DATA_DIR = Path(_db.DATA_DIR)
except Exception:
    DATA_DIR = Path(os.path.expanduser("~/.local/share/Cimitir"))

TRAIN_ROOT = DATA_DIR / "ai_training"
SAMPLES_DIR = TRAIN_ROOT / "training_data"   # buletin_new/, buletin_old/, adeverinta_adresa/, certificat_deces/
MODELS_DIR = TRAIN_ROOT / "models"           # ron_cimitir.traineddata etc.
BUILD_DIR = TRAIN_ROOT / "build"


DOCUMENT_TYPES = ["buletin_new", "buletin_old", "adeverinta_adresa", "certificat_deces"]


def ensure_dirs() -> None:
    for d in [TRAIN_ROOT, SAMPLES_DIR, MODELS_DIR, BUILD_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    for dtype in DOCUMENT_TYPES:
        (SAMPLES_DIR / dtype).mkdir(parents=True, exist_ok=True)
        # placeholder README
        readme = SAMPLES_DIR / dtype / "README.md"
        if not readme.exists():
            readme.write_text(
                f"# Sample-uri pentru {dtype}\n\n"
                f"Adaugă poze reale (JPG/PNG) în acest folder, câte una pentru fiecare document.\n"
                f"Pentru fiecare `foo.jpg` adaugă și `foo.gt.txt` cu textul exact din document.\n\n"
                f"Recomandare: minim 30-50 sample-uri pentru rezultate utile la fine-tuning.\n",
                encoding="utf-8",
            )


def collect_samples() -> dict[str, list[Path]]:
    """Colectează perechile (imagine, gt.txt) din fiecare tip document."""
    ensure_dirs()
    collected: dict[str, list[Path]] = {}
    for dtype in DOCUMENT_TYPES:
        folder = SAMPLES_DIR / dtype
        pairs = []
        for img in folder.iterdir():
            if img.suffix.lower() not in (".jpg", ".jpeg", ".png", ".tif", ".tiff"):
                continue
            gt = img.with_suffix(".gt.txt")
            if gt.exists():
                pairs.append((img, gt))
        collected[dtype] = pairs
    return collected


def report_status() -> dict:
    """Raport rapid: câte sample-uri sunt disponibile pentru fiecare tip."""
    ensure_dirs()
    samples = collect_samples()
    report = {
        "data_root": str(SAMPLES_DIR),
        "models_root": str(MODELS_DIR),
        "counts": {k: len(v) for k, v in samples.items()},
        "total_samples": sum(len(v) for v in samples.values()),
        "tesstrain_dir": os.environ.get("TESSTRAIN_DIR", "(nu e setat)"),
        "tesseract_installed": shutil.which("tesseract") is not None,
        "lstmtraining_installed": shutil.which("lstmtraining") is not None,
        "trained_models": [p.name for p in MODELS_DIR.glob("*.traineddata")],
    }
    return report


def check_prerequisites() -> tuple[bool, list[str]]:
    """Verifică dacă avem tot necesarul pentru training."""
    problems = []
    if not shutil.which("tesseract"):
        problems.append("Tesseract nu e instalat.")
    if not shutil.which("lstmtraining"):
        problems.append(
            "lstmtraining nu e instalat. Pe Debian: `apt-get install tesseract-ocr-legacy` "
            "și clonează https://github.com/tesseract-ocr/tesseract cu source-uri complete."
        )
    tesstrain = os.environ.get("TESSTRAIN_DIR")
    if not tesstrain or not Path(tesstrain).exists():
        problems.append(
            "TESSTRAIN_DIR nu e setat sau folderul nu există.\n"
            "  git clone https://github.com/tesseract-ocr/tesstrain\n"
            "  export TESSTRAIN_DIR=/path/to/tesstrain"
        )
    samples = collect_samples()
    total = sum(len(v) for v in samples.values())
    if total < 10:
        problems.append(
            f"Prea puține sample-uri ({total}). Adaugă min. 30 în "
            f"{SAMPLES_DIR}/<tip>/"
        )
    return len(problems) == 0, problems


def run_tesstrain(base_model: str = "ron", model_name: str = "ron_cimitir",
                  max_iterations: int = 10000) -> int:
    """Rulează pipeline-ul oficial tesstrain (make ...).

    Returnează codul de ieșire al procesului (0 = succes).
    """
    ensure_dirs()
    ok, problems = check_prerequisites()
    if not ok:
        print("❌ Prerequisite lipsă:", file=sys.stderr)
        for p in problems:
            print(f"  · {p}", file=sys.stderr)
        return 2

    tesstrain_dir = Path(os.environ["TESSTRAIN_DIR"])

    # 1. Aducem toate sample-urile într-un folder unic (structura tesstrain)
    ground_truth = BUILD_DIR / f"{model_name}-ground-truth"
    if ground_truth.exists():
        shutil.rmtree(ground_truth)
    ground_truth.mkdir(parents=True)

    all_samples = collect_samples()
    idx = 0
    for dtype, pairs in all_samples.items():
        for img, gt in pairs:
            new_img = ground_truth / f"{dtype}_{idx:04d}{img.suffix.lower()}"
            new_gt = ground_truth / f"{dtype}_{idx:04d}.gt.txt"
            shutil.copy2(img, new_img)
            shutil.copy2(gt, new_gt)
            idx += 1
    print(f"✅ {idx} sample-uri copiate în {ground_truth}")

    # 2. Rulează make
    cmd = [
        "make", "training",
        f"MODEL_NAME={model_name}",
        f"START_MODEL={base_model}",
        f"GROUND_TRUTH_DIR={ground_truth}",
        f"DATA_DIR={BUILD_DIR}",
        f"MAX_ITERATIONS={max_iterations}",
        f"TESSDATA={os.environ.get('TESSDATA_PREFIX', '/usr/share/tesseract-ocr/5/tessdata')}",
    ]
    print(f"\n$ {' '.join(cmd)}\n(în {tesstrain_dir})\n")
    try:
        rc = subprocess.call(cmd, cwd=str(tesstrain_dir))
    except FileNotFoundError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 3

    if rc != 0:
        print(f"❌ Training eșuat, exit code {rc}", file=sys.stderr)
        return rc

    # 3. Copiem modelul rezultat
    built = BUILD_DIR / f"{model_name}.traineddata"
    if built.exists():
        dest = MODELS_DIR / built.name
        shutil.copy2(built, dest)
        print(f"✅ Model salvat: {dest}")
        # Activăm modelul: îl copiem și în tessdata pentru a fi găsit de tesseract
        tessdata_prefix = os.environ.get("TESSDATA_PREFIX", "/usr/share/tesseract-ocr/5/tessdata")
        try:
            shutil.copy2(dest, os.path.join(tessdata_prefix, dest.name))
            print(f"✅ Model activat în {tessdata_prefix}")
        except PermissionError:
            print(f"⚠  Nu am putut copia în {tessdata_prefix} (permisiuni). "
                  f"Copiază manual: sudo cp {dest} {tessdata_prefix}/")
        return 0
    print(f"❌ Modelul {built} nu a fost generat.", file=sys.stderr)
    return 4


def preferred_language() -> str:
    """Dacă avem un model custom instalat, îl folosim la OCR."""
    tessdata = os.environ.get("TESSDATA_PREFIX", "/usr/share/tesseract-ocr/5/tessdata")
    custom = Path(tessdata) / "ron_cimitir.traineddata"
    if custom.exists():
        return "ron_cimitir+ron+eng"
    return "ron+eng"


# ============ CLI ============

def main():
    parser = argparse.ArgumentParser(description="Tesseract training pentru modul AI Scanare")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="Creează folderele necesare + README-uri")
    sub.add_parser("status", help="Afișează statutul sample-urilor și prerequisite-lor")

    p_make = sub.add_parser("make", help="Rulează antrenarea Tesseract")
    p_make.add_argument("--model", default="ron_cimitir")
    p_make.add_argument("--start-from", default="ron")
    p_make.add_argument("--max-iter", type=int, default=10000)

    p_export = sub.add_parser("export-feedback",
                              help="Exportă sample-urile din feedback-learning în training_data")
    p_export.add_argument("--dest", default=str(SAMPLES_DIR / "auto_from_feedback"))

    args = parser.parse_args()

    if args.cmd == "init":
        ensure_dirs()
        print(f"✅ Foldere pregătite:")
        print(f"  {SAMPLES_DIR}")
        print(f"  {MODELS_DIR}")
        return 0

    if args.cmd == "status":
        rep = report_status()
        print(json.dumps(rep, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "make":
        return run_tesstrain(args.start_from, args.model, args.max_iter)

    if args.cmd == "export-feedback":
        from .feedback_learning import export_samples_for_training
        n = export_samples_for_training(args.dest)
        print(f"✅ {n} sample-uri exportate în {args.dest}")
        return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
