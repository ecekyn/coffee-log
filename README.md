# ☕ Coffee Log

Ends the "whose turn is it?" argument. When the grinder runs in the morning, the kitchen camera
checks who's standing there and ticks their column. If it can't tell, it asks on your phones:
through the Home Assistant app, or through [withHuman](https://withhuman.ai) (see 5). Whoever has made fewer coffees in total
is up next.

```
grinder plug (Tapo P110) ──power──▶ Home Assistant ──▶ Coffee Log add-on ──▶ scoreboard website
                                          ▲                  │
kitchen camera (Tapo C110) ──frames───────┘                  └──▶ phone notification if unsure
```

Everything runs on your home server. Camera frames are only grabbed for a few seconds while the
grinder starts, matched in memory and thrown away (unless you turn on `keep_snapshots`).

## 1. Hardware

1. **Plug:** put the grinder on the Tapo P110. Set it up in the Tapo app.
2. **Camera:** Tapo C110 at roughly head height, looking at the face of whoever stands at the grinder.
   In the Tapo app: camera settings → Advanced Settings → **Camera Account**, create a username and
   password (this is what Home Assistant uses, not your TP-Link login).
3. Give both a fixed IP in your router (a DHCP reservation).

## 2. Home Assistant

1. **Settings → Devices & services → Add integration → TP-Link Smart Home.** Add the plug (it asks
   for your TP-Link account once). You get a power sensor, something like
   `sensor.grinder_current_consumption`.
2. Add the camera the same way (recent HA versions support Tapo cameras in that integration, using
   the Camera Account from step 1). If yours doesn't, use the **ONVIF** integration on port 2020.
   Note its entity, e.g. `camera.kitchen`.
3. Unless you'll use withHuman for the questions (section 5): install the **Home Assistant
   Companion app** on both phones and sign in. Each phone shows up as
   a `notify.mobile_app_<phone_name>` service (Developer tools → Actions to check the names).
4. Grind a dose and look at the power sensor's history. Idle should be near 0 W and grinding well
   above 20 W. If not, change `power_threshold_w`.

## 3. Install the add-on

For **Home Assistant OS / Supervised**:

1. Copy this whole folder to `/addons/coffee_log` on the HA host (the Samba share or
   SSH add-on makes this easy).
2. **Settings → Add-ons → Add-on store → ⋮ → Check for updates.** "Coffee Log" appears under
   *Local add-ons*. Install it (it builds on the box, a few minutes).
3. On the **Configuration** tab set `power_sensor`, `camera_entity` and `notify_services`. Start it.
4. Open it from the sidebar (☕ Coffee Log), or on any phone at `http://<server-ip>:8099`.

For **NixOS** (Home Assistant from `services.home-assistant`, or in a container):

1. Add the module. With flakes, add the input
   `inputs.coffee-log.url = "github:ecekyn/coffee-log";` and put
   `inputs.coffee-log.nixosModules.default` in your system's `modules`. Without flakes:
   ```nix
   imports = [ "${builtins.fetchTarball "https://github.com/ecekyn/coffee-log/archive/main.tar.gz"}/nix/module.nix" ];
   ```
2. Save a long-lived access token (your HA profile → Security) where only root can read it.
   Paste it, press Enter, then Ctrl-D:
   ```bash
   sudo install -D -m 600 /dev/stdin /var/lib/secrets/coffee-log-ha-token
   ```
   (agenix or sops-nix work too: point `haTokenFile` at their file.)
3. Configure it:
   ```nix
   services.coffee-log = {
     enable = true;
     openFirewall = true;  # port 8099, for the phones
     haTokenFile = "/var/lib/secrets/coffee-log-ha-token";
     settings = {
       ha_url = "http://127.0.0.1:8123";
       power_sensor = "sensor.grinder_current_consumption";
       camera_entity = "camera.kitchen";
       power_threshold_w = 20;
     };
   };
   ```
   `settings` takes any key from the settings table below. Tokens never go in `settings`, since
   the Nix store is readable by every user; a withHuman key goes in `withhumanTokenFile`.
4. `sudo nixos-rebuild switch`, then watch it start with `journalctl -u coffee-log -f`.
   Data lives in `/var/lib/coffee-log`. Open the scoreboard at `http://<server-ip>:8099`.

For **Home Assistant in a plain container** (no add-on store): copy `config.example.json` to
`data/config.json`, fill in `ha_url` and a long-lived access token (your HA profile → Security),
then `docker compose up -d --build`.

## 4. Teach it your faces

On the website, open **Setup**. Each of you stands at the grinder and taps **Use kitchen camera**
3–5 times: different angles, glasses on and off, in morning light. You can also upload photos, but
frames from the kitchen camera match best.

Until then (or whenever it isn't sure) it just asks on your phones, so nothing gets missed.

## 5. Optional: ask through withHuman

Instead of the Companion app, the "who made it?" question can go through withHuman, which
notifies you on its phone app (or Slack, WhatsApp, email…) and keeps a record of every answer.
withHuman decisions are approve/deny, so each of you gets **your own** request,
`claim_coffee` with `arguments.person` set to your name:

- **Approve** = "I made it". You get the tick and the other person's request is withdrawn.
- **Deny** = "not me". If you both deny, it wasn't coffee and the round is removed.
- Nobody answers within `withhuman_timeout` (12h) → it stays as a question on the website.

Set it up once in withHuman (both of you must be members of the same organization):

1. **Agents → New agent:** a *service* agent with slug `coffee-log`. On it, add an instance
   with **Generate an API key**. That key goes into `withhuman_token`.
2. **Escalation paths:** one called `ece` with only Ece as reviewer, one called `chris` with
   only Chris.
3. **Entry pipeline:** at the top, a branch for agent `coffee-log` + tool `claim_coffee` +
   `arguments.person == "Ece"` → human review on path `ece`; the same for Chris → `chris`.
   Below them, deny anything else from `coffee-log`, so a misconfigured request can't tick
   anyone by itself.
4. Install the withHuman phone app and turn on notifications.

Asking through withHuman replaces the Home Assistant notifications; leave `notify_services` empty.

## How it decides

- A **coffee round** starts when the grinder goes above `power_threshold_w` between
  `window_start` and `window_end`. More grinds within `merge_minutes` (a second drink, a V60 after
  an espresso) count as the same round.
- It grabs `snapshot_count` frames, one every `snapshot_interval_s`, and looks for known faces.
  Exactly one of you recognised → that's a tick. Both of you, or nobody → it asks on the phones
  ("Ece / Chris / Not coffee", or one approve/deny each on withHuman). Answering clears the
  question for the other person too.
- If a later grind in the same round does recognise someone, it fills in the tick and clears the
  question.
- **Whose turn:** fewest total ticks. On a tie, whoever made one longest ago.
- **Only the camera ticks.** There's no adding or reassigning by hand. When the camera can't tell
  (or can't be reached), the coffee waits in the website's Recent coffees list with
  "Ece / Chris / Not coffee" buttons, and once answered it's settled.
- With announcing on, the camera says the name out loud after it ticks (see below).

## Optional: say who got the tick (Tapo cameras)

The NixOS module can play the name through a Tapo camera's speaker. It runs a private
[go2rtc](https://github.com/AlexxIT/go2rtc) for the camera's two-way audio, and Home Assistant's
text-to-speech makes the words:

```nix
services.coffee-log = {
  settings.spoken_names.Ece = "Edge-ay";  # spell names the way they're said
  announce = {
    enable = true;
    cameraAddress = "192.168.1.50";
    tapoPasswordFile = "/var/lib/secrets/coffee-log-tapo-password";
  };
};
```

The password is your TP-Link cloud account's (the Tapo app's), not the camera account's: the
camera's two-way audio only takes that one. Put it in a root-only file outside the Nix store.
Tapo two-way audio can break with camera firmware updates; if it does, ticks still work, and the
log says `Couldn't announce`.

## Getting to the website away from home

Install Tailscale on the server and both phones and it works anywhere, for just you two. On
iPhone, Safari → Share → **Add to Home Screen** makes it feel like an app.

There's no login on port 8099, so don't expose it to the internet directly. (Opened through the
HA sidebar it goes through HA's own login.)

## Trying it without the hardware

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python download_models.py models
.venv/bin/python -m app --demo
```

Open http://localhost:8099. It's three weeks of made-up coffees, plus a **Simulate a grind** button
under Setup.

Tests: `.venv/bin/pip install pytest pytest-asyncio && .venv/bin/pytest` (or `nix flake check`)

## Settings

| Setting | Default | |
|---|---|---|
| `people` | Ece, Chris | Order matters only for the notification buttons |
| `power_sensor` | `sensor.grinder_current_consumption` | The P110's power sensor |
| `power_threshold_w` | 20 | Watts that mean "grinding" |
| `camera_entity` | `camera.kitchen` | |
| `window_start` / `window_end` | 06:30 / 08:00 | Grinds outside this window are ignored |
| `timezone` | Europe/London | |
| `merge_minutes` | 15 | Grinds this close together are one round |
| `notify_services` | none | e.g. `notify.mobile_app_eces_iphone` |
| `withhuman_token` | none | API key of the `coffee-log` agent's instance; asks through withHuman when set |
| `withhuman_url` | `https://app.withhuman.ai` | Change for a self-hosted withHuman |
| `withhuman_timeout` | 12h | How long a question waits for an answer |
| `face_threshold` | 0.363 | Higher means stricter (fewer wrong ticks, more questions) |
| `snapshot_count` / `snapshot_interval_s` | 4 / 1.0 | Frames grabbed per grind |
| `keep_snapshots` | false | Save frames to `/data/snapshots` for debugging |
| `spoken_names` | none | How to say a name out loud, e.g. `{"Ece": "Edge-ay"}` |
| `announce_text` | `{name}` | What the camera says after a tick |
| `tts_engine` | `tts.google_translate_en_com` | Home Assistant text-to-speech entity |
| `announce_go2rtc_url` / `announce_stream` | none / `kitchen_camera` | Set by the NixOS module's `announce` |

## Known limits

- Home Assistant polls the P110 every few seconds, so a very short grind (under ~5 s) can be
  missed. A normal espresso or V60 dose takes longer.
- If both of you are in frame, it asks rather than guessing.
