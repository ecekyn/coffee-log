# ☕ Coffee Log

Ends the "whose turn is it?" argument. When the grinder runs in the morning, the kitchen camera
checks who's standing there and ticks their column. If it can't tell, both phones get a
"Who made coffee?" notification with a button per person. Whoever has made fewer coffees in total
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
3. Install the **Home Assistant Companion app** on both phones and sign in. Each phone shows up as
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

For **Home Assistant in a plain container** (no add-on store): copy `config.example.json` to
`data/config.json`, fill in `ha_url` and a long-lived access token (your HA profile → Security),
then `docker compose up -d --build`.

## 4. Teach it your faces

On the website, open **Setup**. Each of you stands at the grinder and taps **Use kitchen camera**
3–5 times: different angles, glasses on and off, in morning light. You can also upload photos, but
frames from the kitchen camera match best.

Until then (or whenever it isn't sure) it just asks on your phones, so nothing gets missed.

## How it decides

- A **coffee round** starts when the grinder goes above `power_threshold_w` between
  `window_start` and `window_end`. More grinds within `merge_minutes` (a second drink, a V60 after
  an espresso) count as the same round.
- It grabs `snapshot_count` frames, one every `snapshot_interval_s`, and looks for known faces.
  Exactly one of you recognised → that's a tick. Both of you, or nobody → it asks on the phones
  ("Ece / Chris / Not coffee"). Answering on one phone clears the question on the other.
- If a later grind in the same round does recognise someone, it fills in the tick and clears the
  question.
- **Whose turn:** fewest total ticks. On a tie, whoever made one longest ago.
- Anything can be fixed on the website: tap a row to reassign or delete it, or use
  "+ Ece made one" for coffee made outside the window.

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

Tests: `.venv/bin/pip install pytest pytest-asyncio && .venv/bin/pytest`

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
| `face_threshold` | 0.363 | Higher means stricter (fewer wrong ticks, more questions) |
| `snapshot_count` / `snapshot_interval_s` | 4 / 1.0 | Frames grabbed per grind |
| `keep_snapshots` | false | Save frames to `/data/snapshots` for debugging |

## Known limits

- Home Assistant polls the P110 every few seconds, so a very short grind (under ~5 s) can be
  missed. A normal espresso or V60 dose takes longer.
- If both of you are in frame, it asks rather than guessing.
