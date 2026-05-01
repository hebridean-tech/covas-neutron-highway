# COVAS:NEXT Neutron Highway Plugin

**Plots neutron highway routes via [Spansh](https://spansh.co.uk) and tracks your progress jump-by-jump, keeping the AI informed of route status at all times.**

[Plugin Releases](https://github.com/hebridean-tech/covas-neutron-highway/releases) · [Report Issues](https://github.com/hebridean-tech/covas-neutron-highway/issues)

---

## Features

- **One-command route plotting** — tell the AI to plot a neutron highway route and it handles the Spansh API call, polling, and state tracking automatically
- **Jump-by-jump tracking** — monitors `FSDJump` and `CarrierJump` journal events, matches systems by `SystemAddress` (id64), and advances the route index
- **Neutron scoop detection** — warns when a neutron boost was missed (`BoostUsed` not present in `FSDJump` event)
- **Projection state** — maintains a live `NeutronRouteProjection` so the AI always knows your current route status without asking
- **Status generator** — adds route summary to the AI's status context (current position, next systems, distance remaining, completion percentage)
- **Route persistence** — saves/loads routes to disk so they survive chat sessions and app restarts
- **Enable/disable toggle** — plugin settings panel with on/off switch; when disabled, no actions or events are registered
- **Auto source/range** — uses the commander's current star system and ship's max jump range from COVAS built-in projections when not explicitly provided

## How It Works

1. **Plot**: The AI calls the `plot_neutron_route` action with a destination system name. The plugin submits a job to Spansh's `/api/route` endpoint (form-encoded POST) and polls `/api/results/{job_id}` with exponential backoff until the route is ready.
2. **Track**: On every game event, the sideeffect checks for `FSDJump` or `CarrierJump` events. It matches the arrived system's `SystemAddress` against the route's `id64` field, falling back to system name matching. If the commander veers off-route, it tracks by proximity.
3. **Inform**: The projection state is updated after each jump. The status generator feeds the AI a concise summary (jump X of Y, next system, distance left) as part of its context — no explicit prompting needed.
4. **Complete**: When the final system is reached, the route is marked complete, total distance is recorded, and the projection reflects completion.

## Installation

1. Download `NeutronHighway.zip` from the [latest release](https://github.com/hebridean-tech/covas-neutron-highway/releases/latest)
2. Extract into your COVAS:NEXT plugins folder:
   - **Windows**: `%appdata%\com.covas-next.ui\plugins\NeutronHighway\`
   - **Linux (Flatpak)**: `~/.var/app/com.covasnext.ui/data/com.covas-next.ui/plugins/NeutronHighway/`
   - **macOS**: `~/Library/Application Support/com.covas-next.ui/plugins/NeutronHighway/`
3. Restart COVAS:NEXT
4. Enable the plugin in Settings → Neutron Highway → Enabled

## Actions

| Action | Description |
|--------|-------------|
| `plot_neutron_route` | Plot a neutron highway route to a destination system |
| `get_route_status` | Get the current route status and next N systems |
| `clear_route` | Clear the active route |

## Events

| Event | When |
|-------|------|
| `neutron_route_plotted` | A new route has been plotted successfully |
| `neutron_route_updated` | A jump was completed on the route |
| `neutron_route_completed` | The final destination has been reached |
| `neutron_route_cleared` | The route was manually cleared |
| `neutron_boost_missed` | A neutron star was passed without scooping (warning) |

## Requirements

- COVAS:NEXT with plugin support
- Internet connection (for Spansh API)

## Development

Built against COVAS:NEXT plugin API:
- `PluginBase` / `PluginManifest` for lifecycle
- `PluginHelper` for action, projection, sideeffect, and event registration
- `Projection` subclass with Pydantic `StateModel` for persistent state
- `PluginEvent` for dispatching custom events to the AI conversation

### Key Technical Decisions

- **Spansh API is form-encoded**, not JSON — discovered through testing
- **Poll endpoint is `/api/results/{job_id}`** — reverse-engineered from the Spansh frontend
- **id64 = SystemAddress** — confirmed via Frontier forums; Spansh's `id64` matches ED journal's `SystemAddress` exactly
- **Primary match on id64, fallback on system name** — handles edge cases where id64 is missing from the route
- **Status generator over UI tab** — COVAS tabs aren't extensible, so route info is injected via the status context

## Credits

- [Spansh](https://spansh.co.uk) — route calculation API
- [COVAS:NEXT](https://github.com/RatherRude/Elite-Dangerous-AI-Integration) — AI integration framework
- [Rune](https://github.com/hebridean-tech) — development

## License

MIT
