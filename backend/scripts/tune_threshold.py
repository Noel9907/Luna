"""
Find the threshold that fits your photographs, and check a threshold can work at all.

Point it at a folder of folders, one per person:

    photos/
      arun/     three or more photographs of Arun
      sneha/    three or more of Sneha
      ...

    python scripts/tune_threshold.py photos/
    python scripts/tune_threshold.py photos/ --compare      every configuration
    python scripts/tune_threshold.py photos/ --backend auraface

WHAT THIS MEASURES, AND WHY IT IS NOT JUST A THRESHOLD SWEEP

A threshold can only separate two distributions that are already separated. If
same-person scores and different-person scores overlap, every threshold fails:
a high one misses real photographs, a low one shows strangers, and trying both
and finding both bad is the signature of overlap rather than of mistuning.

So the first number here is SEPARATION (d'), not precision. d' is the gap
between the two means measured in standard deviations. Below about 2 no
threshold will save you and the fix is upstream, in image quality or alignment,
not in this file. Above 4 the two clouds barely touch and picking a threshold is
easy. Fix separation first, then tune.

The second thing it does is tell you what you are NOT entitled to conclude. With
P impostor pairs the smallest false match rate you can observe is 1/P, so a
threshold read off 2,000 pairs says nothing about behaviour at one in a million.
The report prints that floor. Respect it.

WHY 1:N CHANGES THE ANSWER

A guest is compared against every face at the wedding, roughly 14,000 of them.
A per-pair false match rate that looks tiny in verification happens 14,000 times
per guest. The report converts each threshold into expected false photographs
per guest at that scale, which is the number that decides the product.

Use photographs from the venues you will actually serve. These models were
benchmarked mostly on Western faces in good light. You are serving Kerala
weddings: dim halls, heavy warm uplighting, jewellery near the face, and a lot
of children, who match poorly against adult-trained models. A threshold tuned on
bright daylight portraits will embarrass you at 9pm in a banquet hall.
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

from app.faces import DETECT_LONG_EDGE, DetectedFace, cosine, get_engine  # noqa: E402

EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# Faces per event, from the real run: 432 faces across 174 photographs, scaled
# to a full wedding. This is what turns a per-pair rate into a per-guest one.
FACES_PER_EVENT = 14_000


def load_people(
    root: pathlib.Path, engine, pre_downscale: bool, quiet: bool = False
) -> dict[str, list[DetectedFace]]:
    """One entry per person, holding the largest face found in each photograph."""
    people: dict[str, list[DetectedFace]] = {}

    for person_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        faces: list[DetectedFace] = []
        for img_path in sorted(person_dir.iterdir()):
            if img_path.suffix.lower() not in EXT:
                continue
            img = cv2.imread(str(img_path))
            if img is None:
                continue

            if pre_downscale:
                # Reproduces the old behaviour, where alignment happened on the
                # detection copy. Shrinking the input first makes the internal
                # scale 1.0, so the recogniser sees exactly what it used to.
                h, w = img.shape[:2]
                s = min(1.0, DETECT_LONG_EDGE / max(h, w))
                if s < 1.0:
                    img = cv2.resize(
                        img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA
                    )

            found = engine.detect_and_embed(img)
            if not found:
                if not quiet:
                    print(f"    skip  {person_dir.name}/{img_path.name}: no usable face")
                continue
            faces.append(max(found, key=lambda f: f.bbox[2] * f.bbox[3]))

        if len(faces) >= 2:
            people[person_dir.name] = faces
        elif not quiet:
            print(f"    {person_dir.name}: skipped, needs 2+ usable photographs")

    return people


def pairs(people: dict[str, list[DetectedFace]], min_blur: float) -> tuple[list, list]:
    """Same-person and different-person cosine scores, above a blur floor."""
    kept = {
        name: [f for f in faces if f.blur >= min_blur] for name, faces in people.items()
    }
    kept = {n: f for n, f in kept.items() if len(f) >= 2}

    same = [
        cosine(a.embedding, b.embedding)
        for faces in kept.values()
        for a, b in itertools.combinations(faces, 2)
    ]
    diff = [
        cosine(a.embedding, b.embedding)
        for (_, v1), (_, v2) in itertools.combinations(kept.items(), 2)
        for a in v1
        for b in v2
    ]
    return same, diff


def dprime(same: list[float], diff: list[float]) -> float:
    """
    Separation between the two clouds, in pooled standard deviations.

    The one number worth looking at before any threshold. It does not care where
    you put the cut, only whether a cut exists.
    """
    if len(same) < 2 or len(diff) < 2:
        return float("nan")
    vs, vd = float(np.var(same, ddof=1)), float(np.var(diff, ddof=1))
    pooled = ((vs + vd) / 2) ** 0.5
    if pooled == 0.0:
        return float("inf")
    return (float(np.mean(same)) - float(np.mean(diff))) / pooled


def report(name: str, same: list[float], diff: list[float], elapsed: float) -> float | None:
    """Prints one configuration's numbers. Returns the chosen threshold, if any."""
    print(f"\n{'=' * 74}\n{name}\n{'=' * 74}")
    if len(same) < 2 or len(diff) < 2:
        print("  not enough pairs to say anything")
        return None

    d = dprime(same, diff)
    resolution = 1.0 / len(diff)

    print(f"  {len(same)} same-person pairs, {len(diff)} different-person pairs")
    print(f"  same       min {min(same):+.3f}  mean {np.mean(same):+.3f}  max {max(same):+.3f}")
    print(f"  different  min {min(diff):+.3f}  mean {np.mean(diff):+.3f}  max {max(diff):+.3f}")
    print(f"  overlap    worst genuine {min(same):+.3f}  vs  best impostor {max(diff):+.3f}")
    print(f"  embedding time {elapsed:.0f} ms/face")

    verdict = (
        "no threshold can work, fix image quality or alignment first"
        if d < 2.0
        else "workable, but expect to trade recall for precision"
        if d < 4.0
        else "cleanly separated"
    )
    print(f"\n  SEPARATION d' = {d:.2f}   {verdict}")
    print(f"  smallest measurable false match rate: 1/{len(diff)} = {resolution:.2e}")
    print("  any threshold with 0 false matches here is only known to be")
    print(f"  better than {resolution:.2e}, which at {FACES_PER_EVENT:,} faces per event")
    print(f"  is up to {resolution * FACES_PER_EVENT:.1f} false photographs per guest.")

    print(
        f"\n  {'thresh':>7} {'precis':>8} {'recall':>8} {'fp':>6} "
        f"{'pair FMR':>10} {'false photos/guest':>19}"
    )
    chosen = None
    for t in [x / 100 for x in range(20, 91, 2)]:
        tp = sum(1 for s in same if s >= t)
        fp = sum(1 for s in diff if s >= t)
        precision = tp / (tp + fp) if (tp + fp) else 1.0
        recall = tp / len(same)
        fmr = fp / len(diff)
        # Upper bound when nothing false was seen: you cannot measure below 1/P.
        projected = max(fmr, resolution) * FACES_PER_EVENT
        mark = ""
        if precision >= 0.99 and recall > 0 and chosen is None:
            chosen = t
            mark = "  <- 99% precision"
        print(
            f"  {t:>7.2f} {precision:>8.1%} {recall:>8.1%} {fp:>6} "
            f"{fmr:>10.2e} {projected:>19.1f}{mark}"
        )

    if chosen is None:
        print("\n  Nothing reached 99% precision on this set.")
    return chosen


def blur_sweep(people: dict[str, list[DetectedFace]]) -> None:
    """
    What a blur floor buys, and what it costs.

    Indexed photographs currently have no blur gate at all; the lowest face seen
    on the real run scored 2.6. Blurred faces give unstable embeddings, so they
    are the likeliest source of a false match. This says where to put the floor
    instead of guessing.
    """
    blurs = sorted(f.blur for faces in people.values() for f in faces)
    if not blurs:
        return
    print(f"\n{'=' * 74}\nBLUR FLOOR\n{'=' * 74}")
    pct = [np.percentile(blurs, p) for p in (5, 25, 50, 75, 95)]
    print(
        "  face blur percentiles   "
        + "  ".join(f"p{p}={v:.0f}" for p, v in zip((5, 25, 50, 75, 95), pct))
    )
    print(f"\n  {'floor':>7} {'faces kept':>11} {'d-prime':>9}")
    for floor in (0.0, 10.0, 20.0, 40.0, 80.0, 150.0):
        kept = sum(1 for f in blurs if f >= floor)
        s, d = pairs(people, floor)
        val = dprime(s, d)
        shown = "-" if np.isnan(val) else f"{val:.2f}"
        print(f"  {floor:>7.0f} {kept:>11} {shown:>9}")
    print("\n  Take the floor where d' stops improving. Past that you are")
    print("  discarding photographs guests wanted for no accuracy in return.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", help="folder of per-person folders")
    ap.add_argument("--backend", default="sface", choices=["sface", "auraface"])
    ap.add_argument(
        "--compare",
        action="store_true",
        help="run old path, fixed path, and auraface if present",
    )
    ap.add_argument("--min-face-px", type=int, default=112)
    args = ap.parse_args()

    root = pathlib.Path(args.folder)
    if not root.is_dir():
        print(f"{root} is not a folder")
        return 2

    # Blur floor off while measuring. The sweep decides where it belongs, and a
    # gate applied during loading would hide the faces the sweep needs to see.
    configs: list[tuple[str, str, bool, int]] = []
    if args.compare:
        configs = [
            ("1. SFace, aligned from the 1600px detection copy (the old bug)", "sface", True, 80),
            ("2. SFace, aligned from the full-resolution image", "sface", False, args.min_face_px),
            ("3. AuraFace, aligned from the full-resolution image", "auraface", False, args.min_face_px),
        ]
    else:
        configs = [(f"{args.backend}, full resolution", args.backend, False, args.min_face_px)]

    loaded: dict[str, list[DetectedFace]] = {}
    results: list[tuple[str, float | None]] = []

    for label, backend, pre, min_px in configs:
        print(f"\n  loading: {label}")
        try:
            engine = get_engine(min_px, 0.7, 0.0, backend)
        except (FileNotFoundError, ImportError) as e:
            print(f"    skipped: {e}")
            continue

        t0 = time.perf_counter()
        people = load_people(root, engine, pre)
        wall = (time.perf_counter() - t0) * 1000
        n_faces = sum(len(v) for v in people.values())
        if len(people) < 2:
            print("    need at least two people with two usable photographs each")
            continue

        for name, faces in people.items():
            print(f"    {name:<20} {len(faces)} face(s)")

        same, diff = pairs(people, 0.0)
        chosen = report(label, same, diff, wall / max(n_faces, 1))
        results.append((label, chosen))
        if not pre and backend == args.backend and not loaded:
            loaded = people

    if loaded:
        blur_sweep(loaded)

    print(f"\n{'=' * 74}\nWHAT TO DO\n{'=' * 74}")
    for label, chosen in results:
        got = f"MATCH_THRESHOLD={chosen}" if chosen else "no threshold reached 99% precision"
        print(f"  {label}\n      {got}")
    print("\n  Compare d' between configurations before choosing a model. If the")
    print("  fixed SFace row separates well, the bug was the problem and a")
    print("  heavier model buys nothing but CPU cost.")
    print("\n  Then re-index one real event and read the galleries yourself.")
    print("  scripts/review_matches.py shows matches weakest first.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
