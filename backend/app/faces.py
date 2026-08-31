"""
Face detection and embedding.

This is the only genuinely uncertain part of the product, so it lives in one
small module with no web framework and no database anywhere near it. You can
import it from a script, a worker, or a test without starting anything else.

Two models, both Apache 2.0, both from OpenCV Zoo:

  YuNet  finds faces and returns five landmarks per face
  SFace  turns one aligned face into 128 numbers

The landmarks matter more than they look. SFace expects a face aligned to a
canonical position, and `alignCrop` uses YuNet's landmarks to do that warp.
Feeding it a raw crop instead measurably degrades matching.
"""

from __future__ import annotations

import pathlib
import threading
from dataclasses import dataclass

import cv2
import numpy as np

MODELS = pathlib.Path(__file__).resolve().parent.parent / "models"
DETECTOR_PATH = MODELS / "face_detection_yunet_2023mar.onnx"
RECOGNIZER_PATH = MODELS / "face_recognition_sface_2021dec.onnx"

EMBEDDING_DIM = 128

# Selfie quality gates. Separate from the indexing gate, which measures faces in
# a full-size photograph; a selfie arrives already downscaled by the client.
MIN_SELFIE_FACE_PX = 72
SELFIE_FACE_FRACTION = 0.16

# Detection runs on a downscaled copy. Faces stay well above the size we care
# about, and a 24-megapixel frame would otherwise cost far more than it needs to.
DETECT_LONG_EDGE = 1600


@dataclass(frozen=True)
class DetectedFace:
    """One face, already embedded. `bbox` is in original-image coordinates."""

    embedding: np.ndarray  # float32, L2-normalised, shape (128,)
    bbox: tuple[int, int, int, int]  # x, y, w, h
    det_score: float
    blur: float


class FaceEngine:
    """
    Holds the two OpenCV models.

    Not thread-safe: cv2 model objects carry internal state across calls, so a
    lock guards them. Worker concurrency comes from running several processes,
    not several threads, which is also how you avoid the GIL here.
    """

    def __init__(self, min_face_px: int = 80, min_detect_score: float = 0.7) -> None:
        for p in (DETECTOR_PATH, RECOGNIZER_PATH):
            if not p.exists():
                raise FileNotFoundError(
                    f"{p.name} is missing. Run:  python scripts/download_models.py"
                )

        self.min_face_px = min_face_px
        self.min_detect_score = min_detect_score
        self._lock = threading.Lock()

        # Input size is set per image before every detect() call.
        self._detector = cv2.FaceDetectorYN.create(
            str(DETECTOR_PATH), "", (320, 320), min_detect_score, 0.3, 5000
        )
        self._recognizer = cv2.FaceRecognizerSF.create(str(RECOGNIZER_PATH), "")

    # ── internals ──────────────────────────────────────────────────────

    @staticmethod
    def _blur_score(gray: np.ndarray) -> float:
        """
        Variance of the Laplacian. Low means smooth, which at face scale means
        out of focus or motion blurred. Receptions are full of both, and a
        blurred face produces an embedding that will happily match a stranger.
        """
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())

    def detect_and_embed(self, image_bgr: np.ndarray) -> list[DetectedFace]:
        h, w = image_bgr.shape[:2]

        scale = min(1.0, DETECT_LONG_EDGE / max(h, w))
        small = (
            cv2.resize(image_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
            if scale < 1.0
            else image_bgr
        )
        sh, sw = small.shape[:2]

        with self._lock:
            self._detector.setInputSize((sw, sh))
            _, raw = self._detector.detect(small)

        if raw is None:
            return []

        out: list[DetectedFace] = []
        for row in raw:
            score = float(row[14])
            if score < self.min_detect_score:
                continue

            # Reject small faces before spending anything on them. Below roughly
            # 80px there is not enough detail for a stable embedding, and a
            # meaningless embedding is what produces a false match.
            fw_full = row[2] / scale
            fh_full = row[3] / scale
            if min(fw_full, fh_full) < self.min_face_px:
                continue

            with self._lock:
                aligned = self._recognizer.alignCrop(small, row)
                feat = self._recognizer.feature(aligned)

            vec = np.asarray(feat, dtype=np.float32).ravel()
            norm = float(np.linalg.norm(vec))
            if norm == 0.0:
                continue
            # Normalise here, once. Cosine similarity then reduces to a dot
            # product, and pgvector's <=> operator behaves predictably.
            vec = vec / norm

            gray = cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY)

            x = int(row[0] / scale)
            y = int(row[1] / scale)
            out.append(
                DetectedFace(
                    embedding=vec,
                    bbox=(max(0, x), max(0, y), int(fw_full), int(fh_full)),
                    det_score=score,
                    blur=self._blur_score(gray),
                )
            )
        return out

    def embed_single(self, image_bgr: np.ndarray) -> DetectedFace | None:
        """
        For a guest selfie. Returns the largest face, or None.

        Callers need to know *why* it failed, so `selfie_error` classifies it
        into the four codes the guest app already has retake copy for.
        """
        faces = self.detect_and_embed(image_bgr)
        if not faces:
            return None
        return max(faces, key=lambda f: f.bbox[2] * f.bbox[3])


def selfie_error(engine: FaceEngine, image_bgr: np.ndarray) -> tuple[str | None, dict]:
    """
    Classifies a rejected selfie, or returns None when it is usable.

    Returns (code, measurements). The measurements are logged rather than shown:
    "hold the camera closer" is the right thing to tell a guest, and useless to
    anybody trying to work out why a particular camera keeps being rejected.

    The size rule is relative to the frame, not an absolute pixel count. A
    selfie is already downscaled before it is sent, so a fixed threshold means
    a 720p webcam and a phone are judged by different standards even when the
    face fills the frame identically.
    """
    h, w = image_bgr.shape[:2]
    scale = min(1.0, DETECT_LONG_EDGE / max(h, w))
    small = (
        cv2.resize(image_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        if scale < 1.0
        else image_bgr
    )
    sh, sw = small.shape[:2]

    stats: dict = {"image": f"{w}x{h}"}

    with engine._lock:  # noqa: SLF001 - same module, deliberate
        engine._detector.setInputSize((sw, sh))
        _, raw = engine._detector.detect(small)

    if raw is None or len(raw) == 0:
        return "NO_FACE_DETECTED", stats
    if len(raw) > 1:
        stats["faces"] = len(raw)
        return "MULTIPLE_FACES", stats

    row = raw[0]
    face_px = min(row[2] / scale, row[3] / scale)
    # Whichever is larger: enough pixels for a stable embedding, or a face that
    # actually fills a reasonable part of the frame.
    floor = max(MIN_SELFIE_FACE_PX, SELFIE_FACE_FRACTION * min(w, h))
    stats.update({"face_px": round(face_px), "needed_px": round(floor)})

    if face_px < floor:
        return "FACE_TOO_SMALL", stats

    with engine._lock:  # noqa: SLF001
        aligned = engine._recognizer.alignCrop(small, row)
    gray = cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY)
    blur = FaceEngine._blur_score(gray)
    stats["blur"] = round(blur, 1)
    if blur < 40.0:
        return "FACE_TOO_BLURRY", stats

    return None, stats


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Both vectors are already unit length, so this is just a dot product."""
    return float(np.dot(a, b))


_engine: FaceEngine | None = None


def get_engine(min_face_px: int = 80, min_detect_score: float = 0.7) -> FaceEngine:
    """Process-wide singleton. Loading SFace takes a moment; do it once."""
    global _engine
    if _engine is None:
        _engine = FaceEngine(min_face_px, min_detect_score)
    return _engine
