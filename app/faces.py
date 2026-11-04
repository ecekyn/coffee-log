"""Who is standing at the grinder? Local face recognition with OpenCV's YuNet + SFace models."""

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

DETECTOR_MODEL = "face_detection_yunet_2023mar.onnx"
RECOGNIZER_MODEL = "face_recognition_sface_2021dec.onnx"
MIN_FACE_SCORE = 0.8


@dataclass
class Match:
    person: str | None  # set only when exactly one known person was seen
    score: float | None = None
    seen: dict[str, float] = field(default_factory=dict)  # every known person spotted, best score each
    faces: int = 0  # faces found in all frames, known or not

    @property
    def reason(self) -> str:
        if self.person:
            return f"recognised {self.person}"
        if len(self.seen) > 1:
            return "saw " + " and ".join(self.seen) + ", can't tell who made it"
        if self.faces:
            return "saw a face but didn't recognise it"
        return "no face in view"


class Faces:
    def __init__(self, models_dir: Path, faces_dir: Path, people: list[str], threshold: float):
        self.faces_dir = faces_dir
        self.people = people
        self.threshold = threshold
        self._lock = threading.Lock()  # the OpenCV models aren't thread-safe
        self._detector = cv2.FaceDetectorYN.create(str(models_dir / DETECTOR_MODEL), "", (320, 320), MIN_FACE_SCORE)
        self._recognizer = cv2.FaceRecognizerSF.create(str(models_dir / RECOGNIZER_MODEL), "")
        self.references: dict[str, list[np.ndarray]] = {}
        self.reload()

    def person_dir(self, person: str) -> Path:
        return self.faces_dir / person.lower()

    def reload(self) -> None:
        """Rebuild reference embeddings from the photos in faces/<name>/."""
        self.references = {}
        for person in self.people:
            features = []
            for path in sorted(self.person_dir(person).glob("*.jp*g")):
                img = cv2.imread(str(path))
                found = self._faces(img) if img is not None else []
                if not found:
                    log.warning("No face found in reference photo %s, skipping", path)
                    continue
                features.append(self._feature(img, _largest(found)))
            self.references[person] = features
            log.info("%s: %d reference photo(s)", person, len(features))

    def enroll(self, person: str, image: bytes) -> tuple[bool, str]:
        """Save a reference photo. It must show exactly one face."""
        img = _decode(image)
        if img is None:
            return False, "That isn't an image I can read."
        found = self._faces(img)
        if not found:
            return False, "Couldn't see a face. Look straight at the camera, with nothing in front of your face."
        if len(found) > 1:
            return False, f"Saw {len(found)} faces. One person at a time."
        folder = self.person_dir(person)
        folder.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(folder / f"{int(time.time() * 1000)}.jpg"), img)
        self.references.setdefault(person, []).append(self._feature(img, found[0]))
        return True, f"Saved. {person} now has {len(self.references[person])} reference photo(s)."

    def clearest(self, frames: list[bytes]) -> bytes:
        """The frame showing one face most clearly, or else the last frame."""
        best, best_score = frames[-1], -1.0
        for frame in frames:
            img = _decode(frame)
            found = self._faces(img) if img is not None else []
            if len(found) == 1 and found[0][-1] > best_score:
                best, best_score = frame, float(found[0][-1])
        return best

    def identify(self, frames: list[bytes]) -> Match:
        seen: dict[str, float] = {}
        face_count = 0
        for frame in frames:
            img = _decode(frame)
            if img is None:
                continue
            for face in self._faces(img):
                face_count += 1
                feature = self._feature(img, face)
                scores = {p: max((_cosine(feature, ref) for ref in refs), default=-1.0)
                          for p, refs in self.references.items()}
                if not scores:
                    continue
                best = max(scores, key=scores.get)
                if scores[best] >= self.threshold:
                    seen[best] = max(seen.get(best, -1.0), scores[best])
        if len(seen) == 1:
            person, score = next(iter(seen.items()))
            return Match(person, score, seen, face_count)
        return Match(None, None, seen, face_count)

    def _faces(self, img: np.ndarray) -> list[np.ndarray]:
        h, w = img.shape[:2]
        with self._lock:
            self._detector.setInputSize((w, h))
            _, faces = self._detector.detect(img)
        return [] if faces is None else list(faces)

    def _feature(self, img: np.ndarray, face: np.ndarray) -> np.ndarray:
        with self._lock:
            return self._recognizer.feature(self._recognizer.alignCrop(img, face)).flatten()


def _decode(data: bytes) -> np.ndarray | None:
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def _largest(faces: list[np.ndarray]) -> np.ndarray:
    return max(faces, key=lambda f: f[2] * f[3])


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
