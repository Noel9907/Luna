"""
Face detection and embedding.

This is the only genuinely uncertain part of the product, so it lives in one
small module with no web framework and no database anywhere near it. You can
import it from a script, a worker, or a test without starting anything else.

Detection is YuNet, Apache 2.0, OpenCV Zoo. It finds faces and returns five
landmarks per face.

Recognition is pluggable, because the licensing here is a minefield and the
accuracy ceiling is the open product risk. Two backends:

  sface     OpenCV Zoo, Apache 2.0, 128-dim. The default and what ships.
  auraface  fal/AuraFace-v1, Apache 2.0, ResNet100 ArcFace, 512-dim. Trained
            on commercially licensed data, unlike InsightFace's buffalo packs,
            whose weights are non-commercial research only. Heavier: bench it
            before believing it is worth the compute.

The landmarks matter more than they look. Both recognisers expect a face warped
to a canonical position, and that warp is driven by the landmarks. Feeding a
raw crop instead measurably degrades matching.
"""

from __future__ import annotations

import pathlib
import threading
from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np

MODELS = pathlib.Path(__file__).resolve().parent.parent / "models"
DETECTOR_PATH = MODELS / "face_detection_yunet_2023mar.onnx"
SFACE_PATH = MODELS / "face_recognition_sface_2021dec.onnx"
AURAFACE_PATH = MODELS / "auraface_glintr100.onnx"

DEFAULT_BACKEND = "sface"

# Embedding width per backend. This is schema, not a preference: pgvector columns
# are declared with a fixed dimension, so changing backend needs a migration and
# a full re-index. models.py reads this; migrations/versions/0006 does the move.
BACKEND_DIMS = {"sface": 128, "auraface": 512}

# Stamped onto every row. Embeddings from different models are not comparable,
# so a mixed table has to be detectable rather than merely wrong.
BACKEND_VERSIONS = {"sface": "sface-2021dec", "auraface": "auraface-v1"}

# Selfie quality gates. Separate from the indexing gate, which measures faces in
# a full-size photograph; a selfie arrives already downscaled by the client.
MIN_SELFIE_FACE_PX = 72
SELFIE_FACE_FRACTION = 0.16
MIN_SELFIE_BLUR = 40.0

# Detection runs on a downscaled copy. Faces stay well above the size we care
# about, and a 24-megapixel frame would otherwise cost far more than it needs to.
# Embedding does NOT run on this copy. See _to_full_scale.
DETECT_LONG_EDGE = 1600


@dataclass(frozen=True)
class DetectedFace:
    """One face, already embedded. `bbox` is in original-image coordinates."""

    embedding: np.ndarray  # float32, L2-normalised
    bbox: tuple[int, int, int, int]  # x, y, w, h
    det_score: float
    blur: float


def _to_full_scale(row: np.ndarray, scale: float) -> np.ndarray:
    """
    Map one YuNet detection from the downscaled image back to the original.

    Columns 0-3 are the box, 4-13 are five landmark pairs, 14 is the score. Only
    the coordinates move.

    This is why it exists: detection runs on a 1600px copy, so on a 6000px frame
    a face that is 300px in the source is 80px here. Aligning from the small copy
    hands the recogniser 80px of detail upscaled to 112x112. The size gate two
    lines away measures at full scale and says the face is fine, so nothing looks
    wrong while every embedding is quietly built from invented pixels. Genuine
    and impostor scores then overlap and no threshold can separate them.
    """
    out = row.copy()
    out[:14] = row[:14] / scale
    return out


class Recognizer(Protocol):
    name: str
    dim: int
    version: str

    def align(self, image_bgr: np.ndarray, row: np.ndarray) -> np.ndarray: ...

    def embed(self, aligned_bgr: np.ndarray) -> np.ndarray: ...


class SFaceRecognizer:
    """OpenCV's SFace. Owns its own alignment, which uses YuNet's row directly."""

    name = "sface"
    dim = BACKEND_DIMS["sface"]
    version = BACKEND_VERSIONS["sface"]

    def __init__(self) -> None:
        if not SFACE_PATH.exists():
            raise FileNotFoundError(
                f"{SFACE_PATH.name} is missing. Run:  python scripts/download_models.py"
            )
        self._m = cv2.FaceRecognizerSF.create(str(SFACE_PATH), "")

    def align(self, image_bgr: np.ndarray, row: np.ndarray) -> np.ndarray:
        return self._m.alignCrop(image_bgr, row)

    def embed(self, aligned_bgr: np.ndarray) -> np.ndarray:
        return np.asarray(self._m.feature(aligned_bgr), dtype=np.float32).ravel()


# The canonical 112x112 five-point template every ArcFace-lineage model is
# trained against. Order is image-left eye, image-right eye, nose, image-left
# mouth corner, image-right mouth corner, which is also YuNet's order.
ARCFACE_TEMPLATE = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)


class AuraFaceRecognizer:
    """
    fal/AuraFace-v1, ResNet100 with ArcFace loss, 512-dim, Apache 2.0.

    Kept behind a lazy onnxruntime import so the default install stays as it is.
    """

    name = "auraface"
    dim = BACKEND_DIMS["auraface"]
    version = BACKEND_VERSIONS["auraface"]

    def __init__(self) -> None:
        if not AURAFACE_PATH.exists():
            raise FileNotFoundError(
                f"{AURAFACE_PATH.name} is missing. Run:  "
                "python scripts/download_models.py --auraface"
            )
        try:
            import onnxruntime
        except ImportError as e:
            raise ImportError("AuraFace needs onnxruntime:  pip install onnxruntime") from e

        self._sess = onnxruntime.InferenceSession(
            str(AURAFACE_PATH), providers=["CPUExecutionProvider"]
        )
        self._input = self._sess.get_inputs()[0].name

    def align(self, image_bgr: np.ndarray, row: np.ndarray) -> np.ndarray:
        pts = row[4:14].reshape(5, 2).astype(np.float32)
        # Partial affine: rotation, uniform scale and translation only. A full
        # affine would let the warp shear the face to fit the template.
        m, _ = cv2.estimateAffinePartial2D(pts, ARCFACE_TEMPLATE, method=cv2.LMEDS)
        if m is None:
            raise ValueError("could not solve alignment transform")
        return cv2.warpAffine(image_bgr, m, (112, 112), borderValue=0.0)

    def embed(self, aligned_bgr: np.ndarray) -> np.ndarray:
        blob = cv2.dnn.blobFromImage(
            aligned_bgr, 1.0 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=True
        )
        out = self._sess.run(None, {self._input: blob})[0]
        return np.asarray(out, dtype=np.float32).ravel()


def make_recognizer(backend: str) -> Recognizer:
    if backend == "sface":
        return SFaceRecognizer()
    if backend == "auraface":
        return AuraFaceRecognizer()
    raise ValueError(f"unknown recogniser backend {backend!r}, expected sface or auraface")


class FaceEngine:
    """
    Holds the detector and one recogniser.

    Not thread-safe: cv2 model objects carry internal state across calls, so a
    lock guards them. Worker concurrency comes from running several processes,
    not several threads, which is also how you avoid the GIL here.
    """

    def __init__(
        self,
        min_face_px: int = 112,
        min_detect_score: float = 0.7,
        min_blur: float = 0.0,
        backend: str = DEFAULT_BACKEND,
    ) -> None:
        if not DETECTOR_PATH.exists():
            raise FileNotFoundError(
                f"{DETECTOR_PATH.name} is missing. Run:  python scripts/download_models.py"
            )

        self.min_face_px = min_face_px
        self.min_detect_score = min_detect_score
        # 0.0 disables the gate, which is what the tuning script wants: it needs
        # to see rejected faces to work out where the floor belongs.
        self.min_blur = min_blur
        self._lock = threading.Lock()

        # Input size is set per image before every detect() call.
        self._detector = cv2.FaceDetectorYN.create(
            str(DETECTOR_PATH), "", (320, 320), min_detect_score, 0.3, 5000
        )
        self._rec = make_recognizer(backend)

    @property
    def backend(self) -> str:
        return self._rec.name

    @property
    def model_version(self) -> str:
        """Goes on every face row, so a mixed table is findable with a WHERE."""
        return self._rec.version

    @property
    def dim(self) -> int:
        return self._rec.dim

    # ── internals ──────────────────────────────────────────────────────

    @staticmethod
    def _blur_score(gray: np.ndarray) -> float:
        """
        Variance of the Laplacian. Low means smooth, which at face scale means
        out of focus or motion blurred. Receptions are full of both, and a
        blurred face produces an embedding that will happily match a stranger.
        """
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())

    def _downscale(self, image_bgr: np.ndarray) -> tuple[np.ndarray, float]:
        h, w = image_bgr.shape[:2]
        scale = min(1.0, DETECT_LONG_EDGE / max(h, w))
        if scale >= 1.0:
            return image_bgr, 1.0
        small = cv2.resize(
            image_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA
        )
        return small, scale

    def _detect(self, small: np.ndarray) -> np.ndarray | None:
        sh, sw = small.shape[:2]
        with self._lock:
            self._detector.setInputSize((sw, sh))
            _, raw = self._detector.detect(small)
        return raw

    def detect_and_embed(self, image_bgr: np.ndarray) -> list[DetectedFace]:
        small, scale = self._downscale(image_bgr)
        raw = self._detect(small)
        if raw is None:
            return []

        out: list[DetectedFace] = []
        for row in raw:
            score = float(row[14])
            if score < self.min_detect_score:
                continue

            # Reject small faces before spending anything on them. Below the
            # recogniser's own 112x112 input there is nothing to embed but
            # interpolation, and a meaningless embedding is a false match.
            full = _to_full_scale(row, scale)
            fw, fh = float(full[2]), float(full[3])
            if min(fw, fh) < self.min_face_px:
                continue

            # Aligned from the ORIGINAL, not from `small`. See _to_full_scale.
            with self._lock:
                try:
                    aligned = self._rec.align(image_bgr, full)
                except ValueError:
                    continue
                feat = self._rec.embed(aligned)

            norm = float(np.linalg.norm(feat))
            if norm == 0.0:
                continue
            # Normalise here, once. Cosine similarity then reduces to a dot
            # product, and pgvector's <=> operator behaves predictably.
            vec = feat / norm

            blur = self._blur_score(cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY))
            if blur < self.min_blur:
                continue

            out.append(
                DetectedFace(
                    embedding=vec,
                    bbox=(max(0, int(full[0])), max(0, int(full[1])), int(fw), int(fh)),
                    det_score=score,
                    blur=blur,
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
    small, scale = engine._downscale(image_bgr)  # noqa: SLF001 - same module, deliberate
    stats: dict = {"image": f"{w}x{h}"}

    raw = engine._detect(small)  # noqa: SLF001
    if raw is None or len(raw) == 0:
        return "NO_FACE_DETECTED", stats
    if len(raw) > 1:
        stats["faces"] = len(raw)
        return "MULTIPLE_FACES", stats

    full = _to_full_scale(raw[0], scale)
    face_px = min(float(full[2]), float(full[3]))
    # Whichever is larger: enough pixels for a stable embedding, or a face that
    # actually fills a reasonable part of the frame.
    floor = max(MIN_SELFIE_FACE_PX, SELFIE_FACE_FRACTION * min(w, h))
    stats.update({"face_px": round(face_px), "needed_px": round(floor)})

    if face_px < floor:
        return "FACE_TOO_SMALL", stats

    with engine._lock:  # noqa: SLF001
        aligned = engine._rec.align(image_bgr, full)  # noqa: SLF001
    blur = FaceEngine._blur_score(cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY))
    stats["blur"] = round(blur, 1)
    if blur < MIN_SELFIE_BLUR:
        return "FACE_TOO_BLURRY", stats

    return None, stats


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Both vectors are already unit length, so this is just a dot product."""
    return float(np.dot(a, b))


_engines: dict[tuple, FaceEngine] = {}
_engines_lock = threading.Lock()


def get_engine(
    min_face_px: int = 112,
    min_detect_score: float = 0.7,
    min_blur: float = 0.0,
    backend: str = DEFAULT_BACKEND,
) -> FaceEngine:
    """
    Per-configuration singleton. Loading a recogniser takes a moment; do it once.

    Keyed on the arguments rather than cached globally, because the tuning script
    builds several engines in one process and a single cached instance would hand
    every configuration back the first one it happened to build.
    """
    key = (min_face_px, min_detect_score, min_blur, backend)
    with _engines_lock:
        if key not in _engines:
            _engines[key] = FaceEngine(min_face_px, min_detect_score, min_blur, backend)
        return _engines[key]
