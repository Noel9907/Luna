"""
Week 2. Find the threshold that fits your photographs.

Do not copy a number off a blog. The right value depends on the model and on
your photographs, and the difference between a good threshold and a guessed one
is the difference between a product people trust and one they do not.

Point it at a folder of folders, one per person:

    photos/
      arun/     three or more photographs of Arun
      sneha/    three or more of Sneha
      ...

    python scripts/tune_threshold.py photos/

It compares every pair, splits them into same-person and different-person, and
prints precision and recall across a sweep.

Pick for PRECISION. A guest who misses a photograph is disappointed; a guest
who sees a stranger's photographs is a privacy incident at somebody's wedding.
Target 99% precision even if recall falls to 70%.

Use photographs from the venues you will actually serve. These models were
benchmarked mostly on Western faces in good light. You are serving Kerala
weddings: dim halls, heavy warm uplighting, jewellery near the face, and a lot
of children, who match poorly against adult-trained models. A threshold tuned on
bright daylight portraits will embarrass you at 9pm in a banquet hall.
"""

from __future__ import annotations

import itertools
import pathlib
import sys

import cv2
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.faces import cosine, get_engine  # noqa: E402

EXT = {".jpg", ".jpeg", ".png", ".webp"}


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    root = pathlib.Path(sys.argv[1])
    if not root.is_dir():
        print(f"{root} is not a folder")
        return 2

    engine = get_engine()
    people: dict[str, list[np.ndarray]] = {}

    for person_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        vecs = []
        for img_path in sorted(person_dir.iterdir()):
            if img_path.suffix.lower() not in EXT:
                continue
            img = cv2.imread(str(img_path))
            if img is None:
                continue
            found = engine.detect_and_embed(img)
            if not found:
                print(f"  skip  {img_path.name}: no usable face")
                continue
            vecs.append(max(found, key=lambda f: f.bbox[2] * f.bbox[3]).embedding)
        if len(vecs) >= 2:
            people[person_dir.name] = vecs
            print(f"  {person_dir.name:<20} {len(vecs)} face(s)")
        else:
            print(f"  {person_dir.name:<20} skipped, needs at least 2 usable photographs")

    if len(people) < 2:
        print("\nNeed at least two people with two usable photographs each.")
        return 1

    same: list[float] = []
    diff: list[float] = []

    for vecs in people.values():
        for a, b in itertools.combinations(vecs, 2):
            same.append(cosine(a, b))

    for (n1, v1), (n2, v2) in itertools.combinations(people.items(), 2):
        if n1 == n2:
            continue
        for a in v1:
            for b in v2:
                diff.append(cosine(a, b))

    print(f"\n  {len(same)} same-person pairs, {len(diff)} different-person pairs")
    print(f"  same       min {min(same):+.3f}  mean {np.mean(same):+.3f}  max {max(same):+.3f}")
    print(f"  different  min {min(diff):+.3f}  mean {np.mean(diff):+.3f}  max {max(diff):+.3f}")

    print(f"\n  {'threshold':>10} {'precision':>10} {'recall':>8} {'false matches':>14}")
    best = None
    for t in [x / 100 for x in range(20, 81, 2)]:
        tp = sum(1 for s in same if s >= t)
        fp = sum(1 for s in diff if s >= t)
        fn = len(same) - tp
        precision = tp / (tp + fp) if (tp + fp) else 1.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        mark = ""
        if precision >= 0.99 and best is None and recall > 0:
            best = t
            mark = "  <- first threshold at 99% precision"
        print(f"  {t:>10.2f} {precision:>10.1%} {recall:>8.1%} {fp:>14}{mark}")

    if best is not None:
        print(f"\n  Put this in .env:   MATCH_THRESHOLD={best}")
        print("  Then re-run one real event's photographs and eyeball the galleries.")
    else:
        print("\n  No threshold reached 99% precision on this set.")
        print("  Usually that means the photographs are too few, too similar")
        print("  between people, or too low quality. Add more before deciding")
        print("  the models are wrong.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
