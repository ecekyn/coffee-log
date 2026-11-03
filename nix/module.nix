# services.coffee-log: runs Coffee Log as a systemd service next to Home Assistant.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.services.coffee-log;
  format = pkgs.formats.json { };
  configFile = format.generate "coffee-log.json" (
    cfg.settings
    // {
      data_dir = "/var/lib/coffee-log";
      models_dir = "${cfg.package.models}";
    }
  );
in
{
  options.services.coffee-log = {
    enable = lib.mkEnableOption "Coffee Log, which ticks whoever makes the morning coffee";

    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.callPackage ./package.nix { };
      defaultText = lib.literalExpression "pkgs.callPackage ./package.nix { }";
      description = "The Coffee Log package to run.";
    };

    settings = lib.mkOption {
      type = lib.types.submodule {
        freeformType = format.type;
        options.port = lib.mkOption {
          type = lib.types.port;
          default = 8099;
          description = "Port for the scoreboard website.";
        };
      };
      default = { };
      example = lib.literalExpression ''
        {
          ha_url = "http://127.0.0.1:8123";
          power_sensor = "sensor.grinder_current_consumption";
          camera_entity = "camera.kitchen";
        }
      '';
      description = ''
        Coffee Log settings, the same keys as config.example.json (see the README's settings table).
        Tokens don't belong here, because everything here ends up in the world-readable Nix store:
        use haTokenFile and withhumanTokenFile.
      '';
    };

    haTokenFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      example = "/run/secrets/coffee-log-ha-token";
      description = "File holding a Home Assistant long-lived access token. Keep it outside the Nix store.";
    };

    withhumanTokenFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "File holding the withHuman API key, to ask \"who made coffee?\" through withHuman.";
    };

    openFirewall = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Open the scoreboard's port so phones on the network can reach it.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.haTokenFile != null;
        message = "services.coffee-log.haTokenFile must name a file with a Home Assistant long-lived access token.";
      }
      {
        assertion = !(cfg.settings ? ha_token || cfg.settings ? withhuman_token);
        message = "Put Coffee Log's tokens in haTokenFile / withhumanTokenFile, not in settings (the Nix store is world-readable).";
      }
    ];

    systemd.services.coffee-log = {
      description = "Coffee Log";
      wantedBy = [ "multi-user.target" ];
      wants = [ "network-online.target" ];
      after = [ "network-online.target" ] ++ lib.optional config.services.home-assistant.enable "home-assistant.service";
      environment.COFFEE_CONFIG = configFile;
      serviceConfig = {
        ExecStart = lib.getExe cfg.package;
        Restart = "always";
        RestartSec = 5;

        # The tokens arrive as systemd credentials, readable only by this service.
        LoadCredential = [
          "ha_token:${cfg.haTokenFile}"
        ] ++ lib.optional (cfg.withhumanTokenFile != null) "withhuman_token:${cfg.withhumanTokenFile}";

        DynamicUser = true;
        StateDirectory = "coffee-log"; # coffee.db and the reference photos
        NoNewPrivileges = true;
        PrivateTmp = true;
        PrivateDevices = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
        RestrictAddressFamilies = [
          "AF_INET"
          "AF_INET6"
          "AF_UNIX"
        ];
        RestrictNamespaces = true;
        LockPersonality = true;
        SystemCallArchitectures = "native";
      };
    };

    networking.firewall.allowedTCPPorts = lib.mkIf cfg.openFirewall [ cfg.settings.port ];
  };
}
