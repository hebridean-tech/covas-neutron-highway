# COVAS Neutron Highway

A [COVAS:NEXT](https://ratherrude.github.io/Elite-Dangerous-AI-Integration/) plugin that plots neutron highway routes via the [Spansh API](https://www.spansh.co.uk/) and tracks your progress jump-by-jump, keeping the AI informed of your route status at all times.

## Features

- **Spansh neutron highway routing** — plots routes optimised for neutron-star supercharge boosts
- **Automatic jump tracking** — follows your `FSDJump` and `CarrierJump` events against the active route
- **System matching by ID** — uses `id64`/`SystemAddress` for reliable matching (falls back to name comparison)
- **Boost detection** — warns when a neutron jump is made without scooping (missed the cone)
- **HUD projection** — live route state exposed as a `NeutronRouteProjection` for on-screen overlays
- **Status generator** — route info injected into every LLM turn automatically
- **Persistence** — route survives restarts
- **AI notifications** — speaks up on route plotted, route lost, and route complete

## Actions

| Action | Description |
|---|---|
| `plot_neutron_route` | Plot a neutron highway route (auto-detects current system and jump range) |
| `get_route_status` | Query current route progress |
| `clear_route` | Clear the active route |

## Install

1. Download the latest [release](https://github.com/hebridean-tech/covas-neutron-highway/releases)
2. Extract into your COVAS:NEXT plugins folder:
   ```
   %appdata%\com.covas-next.ui\plugins\NeutronHighway\
   ```
   The folder should contain `manifest.json` and `NeutronHighway.py`
3. Restart COVAS:NEXT

## Usage

Ask the AI naturally:

> "Plot a neutron highway route to Colonia"
> "What's my route status?"
> "Clear my route"

## HUD Overlay Prompt

After plotting a route, ask the AI to render an on-screen overlay:

> "Show a neutron highway overlay on the HUD. It should display a progress bar at the top showing jumps completed out of total jumps, with the percentage. Below that show the current system and next system on one line. Then show distance remaining in Ly. Mark neutron star systems with a ⚡ icon. Use the NeutronRouteProjection for all the data. Keep it compact and dark-themed so it doesn't block my view."

## Requirements

- COVAS:NEXT (latest recommended)
- Internet access for Spansh API

## License

MIT
