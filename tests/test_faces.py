"""Real models on two sample photos from OpenCV's repo (two different people)."""

from pathlib import Path

import pytest

from app.faces import DETECTOR_MODEL, Faces

ROOT = Path(__file__).parent.parent
MODELS = ROOT / "models"
SAMPLES = ROOT / ".testdata"  # lena.jpg, messi5.jpg from opencv/samples/data

pytestmark = pytest.mark.skipif(
    not (MODELS / DETECTOR_MODEL).exists() or not (SAMPLES / "lena.jpg").exists(),
    reason="run download_models.py and fetch the OpenCV sample photos first",
)


def photo(name: str) -> bytes:
    return (SAMPLES / name).read_bytes()


def test_enroll_then_recognise(tmp_path):
    faces = Faces(MODELS, tmp_path, ["Lena", "Lionel"], 0.363)
    ok, msg = faces.enroll("Lena", photo("lena.jpg"))
    assert ok, msg

    match = faces.identify([photo("lena.jpg")])
    assert match.person == "Lena" and match.score > 0.9

    stranger = faces.identify([photo("messi5.jpg")])
    assert stranger.person is None and stranger.faces >= 1
    assert "didn't recognise" in stranger.reason


def test_references_survive_restart(tmp_path):
    Faces(MODELS, tmp_path, ["Lena", "Lionel"], 0.363).enroll("Lena", photo("lena.jpg"))
    again = Faces(MODELS, tmp_path, ["Lena", "Lionel"], 0.363)
    assert len(again.references["Lena"]) == 1
    assert again.identify([photo("lena.jpg")]).person == "Lena"


def test_rejects_non_images(tmp_path):
    faces = Faces(MODELS, tmp_path, ["Lena", "Lionel"], 0.363)
    assert faces.enroll("Lena", b"not a jpeg") == (False, "That isn't an image I can read.")
    assert faces.identify([b"garbage"]).reason == "no face in view"
