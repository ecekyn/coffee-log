# services.coffee-log: runs Coffee Log as a systemd service next to Home Assistant.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.services.coffee-log;
  announce = cfg.announce;
  format = pkgs.formats.json { };
  configFile = format.generate "coffee-log.json" (
    cfg.settings
    // {
      data_dir = "/var/lib/coffee-log";
      models_dir = "${cfg.package.models}";
    }
    // lib.optionalAttrs announce.enable {
      announce_go2rtc_url = "http://127.0.0.1:${toString announce.port}";
      announce_stream = "kitchen_camera";
    }
  );

  # go2rtc reads the camera's address and password from a config written at start-up, so the
  # password only ever exists in the service's private runtime directory, never in the Nix store.
  go2rtcStart = pkgs.writeShellScript "coffee-log-go2rtc" ''
    set -eu
    pw=$(${lib.getExe pkgs.python3Minimal} -c 'import sys, urllib.parse; print(urllib.parse.quote(open(sys.argv[1]).read().strip(), safe=""))' "$CREDENTIALS_DIRECTORY/tapo_password")
    umask 077
    cat > "$RUNTIME_DIRECTORY/go2rtc.yaml" <<EOF
    api:
      listen: "127.0.0.1:${toString announce.port}"
    rtsp:
      listen: ""
    webrtc:
      listen: ""
    srtp:
      listen: ""
    ffmpeg:
      bin: "${lib.getExe' pkgs.ffmpeg-headless "ffmpeg"}"
    streams:
      kitchen_camera: "tapo://$pw@${announce.cameraAddress}"
    EOF
    unset pw
    exec ${lib.getExe pkgs.go2rtc} -config "$RUNTIME_DIRECTORY/go2rtc.yaml"
  '';

  hardening = {
    DynamicUser = true;
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

    announce = {
      enable = lib.mkEnableOption ''
        saying who got the tick out of a Tapo camera's speaker. Runs a private go2rtc for the camera's
        two-way audio; Home Assistant's text-to-speech makes the words (see tts_engine and spoken_names)
      '';

      cameraAddress = lib.mkOption {
        type = lib.types.str;
        example = "192.168.1.50";
        description = "IP address of the Tapo camera to talk through.";
      };

      tapoPasswordFile = lib.mkOption {
        type = lib.types.str;
        example = "/run/secrets/coffee-log-tapo-password";
        description = ''
          File holding the TP-Link cloud account password (the Tapo app's, not the camera account's).
          The camera's two-way audio only accepts that one. Keep it outside the Nix store.
        '';
      };

      port = lib.mkOption {
        type = lib.types.port;
        default = 1984;
        description = "Port for the private go2rtc's API, on 127.0.0.1 only.";
      };
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
        assertion = !(announce.enable && lib.hasPrefix builtins.storeDir announce.tapoPasswordFile);
        message = "services.coffee-log.announce.tapoPasswordFile must be outside the Nix store.";
      }
      {
        assertion = !(cfg.settings ? ha_token || cfg.settings ? withhuman_token);
        message = "Put Coffee Log's tokens in haTokenFile / withhumanTokenFile, not in settings (the Nix store is world-readable).";
      }
    ];

    systemd.services.coffee-log = {
      description = "Coffee Log";
      wantedBy = [ "multi-user.target" ];
      wants = [ "network-online.target" ] ++ lib.optional announce.enable "coffee-log-go2rtc.service";
      after =
        [ "network-online.target" ]
        ++ lib.optional config.services.home-assistant.enable "home-assistant.service"
        ++ lib.optional announce.enable "coffee-log-go2rtc.service";
      environment.COFFEE_CONFIG = configFile;
      serviceConfig = hardening // {
        ExecStart = lib.getExe cfg.package;
        Restart = "always";
        RestartSec = 5;

        # The tokens arrive as systemd credentials, readable only by this service.
        LoadCredential = [
          "ha_token:${cfg.haTokenFile}"
        ] ++ lib.optional (cfg.withhumanTokenFile != null) "withhuman_token:${cfg.withhumanTokenFile}";

        StateDirectory = "coffee-log"; # coffee.db and the reference photos
      };
    };

    systemd.services.coffee-log-go2rtc = lib.mkIf announce.enable {
      description = "go2rtc for Coffee Log's announcements";
      wantedBy = [ "multi-user.target" ];
      wants = [ "network-online.target" ];
      after = [ "network-online.target" ];
      serviceConfig = hardening // {
        ExecStart = go2rtcStart;
        Restart = "always";
        RestartSec = 5;
        LoadCredential = [ "tapo_password:${announce.tapoPasswordFile}" ];
        RuntimeDirectory = "coffee-log-go2rtc";
        RuntimeDirectoryMode = "0700";
      };
    };

    networking.firewall.allowedTCPPorts = lib.mkIf cfg.openFirewall [ cfg.settings.port ];
  };
}
