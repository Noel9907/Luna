"""
Fetches the two ONNX models into models/.

Both come from OpenCV Zoo and both are Apache 2.0, which matters: this product
is sold commercially, and the model everyone reaches for first (InsightFace
buffalo_l) is licensed for non-commercial research only. CompreFace does not
fix that either, since its Apache licence covers Exadel's code and not the
InsightFace weights it loads.

  YuNet  detection    ~230 KB
  SFace  recognition  ~37 MB, 128-dimensional embeddings
"""

import pathlib
import sys
import requests

BASE = "https://github.com/opencv/opencv_zoo/raw/main/models"
MODELS = {
    "face_detection_yunet_2023mar.onnx": f"{BASE}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "face_recognition_sface_2021dec.onnx": f"{BASE}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
}

OUT = pathlib.Path(__file__).resolve().parent.parent / "models"


def main() -> int:
    OUT.mkdir(exist_ok=True)
    for name, url in MODELS.items():
        dest = OUT / name
        if dest.exists() and dest.stat().st_size > 1000:
            print(f"  have  {name}")
            continue
        print(f"  fetch {name} ...", end="", flush=True)
        r = requests.get(url, timeout=180, allow_redirects=True)
        r.raise_for_status()
        dest.write_bytes(r.content)
        print(f" {len(r.content) // 1024} KB")
    print("\nModels ready in", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
