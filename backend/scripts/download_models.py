"""
Fetches the ONNX models into models/.

    python scripts/download_models.py              detection + sface
    python scripts/download_models.py --auraface   also fetch AuraFace

Licensing is the reason this file has a docstring at all. This product is sold
commercially, and the model everyone reaches for first (InsightFace buffalo_l
or antelopev2) is licensed for non-commercial research only. CompreFace does
not fix that either, since its Apache licence covers Exadel's code and not the
InsightFace weights it loads.

  YuNet     detection    ~230 KB   Apache 2.0, OpenCV Zoo
  SFace     recognition  ~37 MB    Apache 2.0, OpenCV Zoo, 128-dim
  AuraFace  recognition  ~250 MB   Apache 2.0, fal, ResNet100 ArcFace, 512-dim

AuraFace is optional and not what ships. It is here so the accuracy ceiling can
be measured rather than argued about: it is the only high-accuracy ArcFace
lineage model I found whose weights are commercially usable, because fal trained
it on commercially licensed data instead of Glint360K or MS1MV2. Needs
onnxruntime, and it is several times heavier than SFace on CPU, which is a
capacity question before it is an accuracy one. Bench with tune_threshold.py.
"""

import argparse
import pathlib
import sys

import requests

ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models"

CORE = {
    "face_detection_yunet_2023mar.onnx": f"{ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "face_recognition_sface_2021dec.onnx": f"{ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
}

# Renamed on the way in. Upstream calls it glintr100.onnx after the architecture,
# which says nothing about which weights are in the file.
EXTRA = {
    "auraface_glintr100.onnx": "https://huggingface.co/fal/AuraFace-v1/resolve/main/glintr100.onnx",
}

OUT = pathlib.Path(__file__).resolve().parent.parent / "models"


def fetch(name: str, url: str) -> None:
    dest = OUT / name
    if dest.exists() and dest.stat().st_size > 1000:
        print(f"  have  {name}")
        return
    print(f"  fetch {name} ...", end="", flush=True)
    # Streamed: AuraFace is ~250 MB and holding it all in memory first is rude
    # on a laptop that is also running Postgres and a worker.
    with requests.get(url, timeout=600, allow_redirects=True, stream=True) as r:
        r.raise_for_status()
        tmp = dest.with_suffix(dest.suffix + ".part")
        size = 0
        with tmp.open("wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
                size += len(chunk)
        # Rename only once it is complete, so an interrupted download does not
        # leave a truncated file that looks present to the next run.
        tmp.replace(dest)
    print(f" {size // 1024} KB")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auraface", action="store_true", help="also fetch AuraFace (~250 MB)")
    args = ap.parse_args()

    OUT.mkdir(exist_ok=True)
    wanted = dict(CORE)
    if args.auraface:
        wanted.update(EXTRA)

    for name, url in wanted.items():
        fetch(name, url)

    print("\nModels ready in", OUT)
    if args.auraface:
        print("AuraFace also needs:  pip install onnxruntime")
    return 0


if __name__ == "__main__":
    sys.exit(main())
