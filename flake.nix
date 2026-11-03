{
  description = "Coffee Log: ticks whoever makes the morning coffee";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs =
    { self, nixpkgs }:
    let
      forEachSystem = f: nixpkgs.lib.genAttrs [ "x86_64-linux" "aarch64-linux" ] (s: f nixpkgs.legacyPackages.${s});
    in
    {
      nixosModules.default = ./nix/module.nix;

      packages = forEachSystem (pkgs: {
        default = pkgs.callPackage ./nix/package.nix { };
      });

      checks = forEachSystem (pkgs: {
        tests =
          let
            src = pkgs.lib.fileset.toSource {
              root = ./.;
              fileset = pkgs.lib.fileset.unions [ ./app ./tests ./pytest.ini ];
            };
            inherit (self.packages.${pkgs.stdenv.hostPlatform.system}.default) dependencies;
            python = pkgs.python3.withPackages (
              ps:
              dependencies ps
              ++ [
                ps.pytest
                ps.pytest-asyncio
              ]
            );
          in
          pkgs.runCommand "coffee-log-tests" { } ''
            cp -r ${src} src && chmod -R u+w src && cd src
            ${python}/bin/python -m pytest -q -p no:cacheprovider
            touch $out
          '';
      });
    };
}
