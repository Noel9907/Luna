"""
Day 2. The ninety minutes that decide whether this product is viable.

No web framework, no database, no Docker. Three photographs in, three numbers
out. If the numbers are wrong you change models or thresholds while it costs an
evening, instead of discovering it the week of a wedding.

    python scripts/prove_matching.py me1.jpg me2.jpg someone_else.jpg

What you want to see:

    me1 vs me2           0.55 or higher   same person
    me1 vs someone_else  0.25 or lower    different people

The gap between those two is the entire product. A wide gap means a threshold
exists that separates them cleanly. A narrow gap means it does not, and no
amount of API code will fix that.
"""

from __future__ import annotations

import pathlib
import sys

import cv2

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.faces import cosine, get_engine  # noqa: E402


def load(path: str):
    img = cv2.imread(path)
    if img is None:
        raise SystemExit(f"Could not read {path}")
    return img


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__)
        return 2

    a_path, b_path, c_path = sys.argv[1:4]
    engine = get_engine()

    faces = {}
    for label, path in (("A", a_path), ("B", b_path), ("C", c_path)):
        img = load(path)
        found = engine.detect_and_embed(img)
        if not found:
            print(f"  {label}  no face found in {path}")
            print("\n     Try a clearer, closer, better lit photograph.")
            return 1
        biggest = max(found, key=lambda f: f.bbox[2] * f.bbox[3])
        faces[label] = biggest
        print(
            f"  {label}  {pathlib.Path(path).name:<28} "
            f"faces={len(found)}  size={biggest.bbox[2]}x{biggest.bbox[3]}  "
            f"score={biggest.det_score:.2f}  blur={biggest.blur:.0f}"
        )

    same = cosine(faces["A"].embedding, faces["B"].embedding)
    diff_1 = cosine(faces["A"].embedding, faces["C"].embedding)
    diff_2 = cosine(faces["B"].embedding, faces["C"].embedding)

    print("\n  similarity")
    print(f"    A vs B  (should be the same person)   {same:+.3f}")
    print(f"    A vs C  (should be different people)  {diff_1:+.3f}")
    print(f"    B vs C  (should be different people)  {diff_2:+.3f}")

    gap = same - max(diff_1, diff_2)
    print(f"\n  gap  {gap:+.3f}")

    if same > 0.5 and max(diff_1, diff_2) < 0.3:
        print("\n  PASS. The models separate these people cleanly.")
        print("  Build the rest.")
        return 0
    if gap > 0.2:
        print("\n  MARGINAL. There is a gap, but a narrow one.")
        print("  Usable, but tune the threshold carefully on real event photographs")
        print("  before anyone relies on it.")
        return 0

    print("\n  FAIL. These embeddings do not separate these people.")
    print("  Check that A and B really are the same person, that faces are")
    print("  reasonably large and lit, then try other photographs before")
    print("  concluding the models are wrong.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
