"""
How often does the model call two different people the same person?

    python scripts/measure_impostors.py storage/events/<event-id>/photos
    python scripts/measure_impostors.py <folder> --compare

No labels needed, which is the point. Two faces in the SAME photograph are
different people, so every photograph with two or more faces donates impostor
pairs for free. That gives an honest false match rate on your own venues,
lighting and guests without anyone sorting photographs into folders first.

WHY THIS IS THE MEASUREMENT THAT MATTERS

A guest selfie is compared against every face at the wedding, roughly 14,000 of
them. So a false match rate that sounds negligible in verification is not:

    FMR 1e-2  ->  140 strangers' photographs in one guest's gallery
    FMR 1e-3  ->  14
    FMR 1e-4  ->  1.4
    FMR 1e-5  ->  0.14

Precision over recall is a product decision, but the threshold that delivers it
is arithmetic, and this is the arithmetic.

WHAT THIS DOES NOT TELL YOU

Only the impostor half. It says where a threshold must sit to keep strangers
out; it says nothing about how many real photographs survive that threshold.
For the other half you need labelled folders and scripts/tune_threshold.py.
A threshold of 1.0 scores perfectly here and returns nothing.

Also: with P pairs you cannot observe a rate below 1/P. A few hundred
photographs give ~1e-3, which is two orders short of what the product needs.
Treat a clean run as "not ruled out" rather than as proof.
"""

from __future__ import annotations

import argparse
import itertools
import pathlib
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.faces import cosine, get_engine  # noqa: E402

EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
FACES_PER_EVENT = 14_000
THRESHOLDS = (0.20, 0.25, 0.30, 0.363, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70)


def _iou(a: tuple, b: tuple) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def measure(folder: pathlib.Path, backend: str, min_face_px: int) -> None:
    engine = get_engine(min_face_px, 0.7, 0.0, backend)
    files = sorted(p for p in folder.rglob("*") if p.suffix.lower() in EXT)
    if not files:
        print(f"  no images under {folder}")
        return

    scores: list[float] = []
    n_faces = 0
    t0 = time.perf_counter()
    for p in files:
        img = cv2.imread(str(p))
        if img is None:
            continue
        faces = engine.detect_and_embed(img)
        n_faces += len(faces)
        for a, b in itertools.combinations(faces, 2):
            # Overlapping boxes would be one face detected twice, which is a
            # detector artefact and not evidence about the recogniser.
            if _iou(a.bbox, b.bbox) >= 0.1:
                continue
            scores.append(cosine(a.embedding, b.embedding))
    wall = time.perf_counter() - t0

    print(f"\n{'=' * 70}")
    print(f"{backend}   {n_faces} faces in {len(files)} photographs   "
          f"{wall / max(n_faces, 1) * 1000:.0f} ms/face")
    print("=" * 70)
    if len(scores) < 20:
        print("  too few multi-face photographs to say anything")
        return

    a = np.array(scores)
    floor = 1.0 / len(a)
    print(f"  {len(a)} impostor pairs")
    print(f"  mean {a.mean():+.3f}   sd {a.std():.3f}   max {a.max():+.3f}")
    print("  " + "  ".join(f"p{q}={np.percentile(a, q):+.3f}" for q in (50, 90, 99)))
    print(f"  cannot measure below 1/{len(a)} = {floor:.1e} "
          f"({floor * FACES_PER_EVENT:.0f} photos/guest)")

    print(f"\n  {'thresh':>7} {'pairs':>7} {'FMR':>10} {'stranger photos/guest':>23}")
    for t in THRESHOLDS:
        n = int((a >= t).sum())
        fmr = n / len(a)
        note = "  <- below 1" if fmr * FACES_PER_EVENT < 1 else ""
        print(f"  {t:>7.3f} {n:>7} {fmr:>10.2e} "
              f"{fmr * FACES_PER_EVENT:>23.1f}{note}")

    # The highest-scoring pairs are worth opening. A genuine 0.9 between two
    # people is a model failure; more often it is one person photographed twice
    # in the same frame, in a mirror or a portrait on display.
    top = np.sort(a)[-5:][::-1]
    print("\n  highest impostor pairs: " + "  ".join(f"{v:.3f}" for v in top))
    print("  Open those photographs before believing them. The same person")
    print("  appearing twice in one frame is not a false match.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--backend", default="sface", choices=["sface", "auraface"])
    ap.add_argument("--compare", action="store_true", help="run both backends")
    ap.add_argument("--min-face-px", type=int, default=112)
    args = ap.parse_args()

    folder = pathlib.Path(args.folder)
    if not folder.is_dir():
        print(f"{folder} is not a folder")
        return 2

    for backend in (["sface", "auraface"] if args.compare else [args.backend]):
        try:
            measure(folder, backend, args.min_face_px)
        except (FileNotFoundError, ImportError) as e:
            print(f"\n  {backend}: skipped, {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
