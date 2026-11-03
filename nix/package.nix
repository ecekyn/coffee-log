# Coffee Log with its Python dependencies and the two face models, all from Nix.
{
  lib,
  stdenvNoCC,
  fetchurl,
  linkFarm,
  makeWrapper,
  python3,
}:

let
  dependencies = ps: [
    ps.aiohttp
    ps.numpy
    ps.opencv4
    ps.tzdata
  ];
  python = python3.withPackages dependencies;

  # Same files and checksums as download_models.py.
  zoo = "https://github.com/opencv/opencv_zoo/raw/main/models";
  models = linkFarm "coffee-log-models" [
    {
      name = "face_detection_yunet_2023mar.onnx";
      path = fetchurl {
        url = "${zoo}/face_detection_yunet/face_detection_yunet_2023mar.onnx";
        sha256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4";
      };
    }
    {
      name = "face_recognition_sface_2021dec.onnx";
      path = fetchurl {
        url = "${zoo}/face_recognition_sface/face_recognition_sface_2021dec.onnx";
        sha256 = "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79";
      };
    }
  ];
in
stdenvNoCC.mkDerivation {
  pname = "coffee-log";
  version = "0.2.0";

  src = lib.fileset.toSource {
    root = ../.;
    fileset = ../app;
  };

  nativeBuildInputs = [ makeWrapper ];

  installPhase = ''
    mkdir -p $out/share/coffee-log
    cp -r app $out/share/coffee-log/
    makeWrapper ${python}/bin/python $out/bin/coffee-log \
      --add-flags "-m app" \
      --prefix PYTHONPATH : $out/share/coffee-log
  '';

  passthru = { inherit models python dependencies; };

  meta = {
    description = "Ticks whoever makes the morning coffee";
    homepage = "https://github.com/ecekyn/coffee-log";
    mainProgram = "coffee-log";
    platforms = lib.platforms.linux;
  };
}
