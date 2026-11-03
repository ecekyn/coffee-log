"""Fetch the face detection (YuNet) and recognition (SFace) models from the OpenCV model zoo."""

import hashlib
import sys
import urllib.request
from pathlib import Path

ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models"
MODELS = {
    "face_detection_yunet_2023mar.onnx": (
        f"{ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    ),
    "face_recognition_sface_2021dec.onnx": (
        f"{ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    ),
}


def main(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name, (url, sha256) in MODELS.items():
        path = dest / name
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == sha256:
            continue
        print(f"Downloading {name}")
        data = urllib.request.urlopen(url, timeout=120).read()
        if hashlib.sha256(data).hexdigest() != sha256:
            sys.exit(f"{name}: checksum mismatch, refusing to use it")
        path.write_bytes(data)


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "models"))
