"""Neutron Highway plugin for COVAS:NEXT.

Plots neutron highway routes via the Spansh API and tracks the commander's
progress jump-by-jump, keeping the AI informed of route status at all times.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, override

from pydantic import BaseModel, Field
from typing_extensions import override

from lib.PluginBase import PluginBase, PluginManifest
from lib.PluginSettingDefinitions import PluginSettings, SettingsGrid, ToggleSetting
from lib.PluginHelper import PluginHelper, PluginEvent, Projection

logger = logging.getLogger("NeutronHighway")

SPANSH_ROUTE_URL = "https://www.spansh.co.uk/api/route"
SPANSH_RESULTS_URL = "https://www.spansh.co.uk/api/results"
POLL_INITIAL_S = 0.25
POLL_MAX_S = 16.0


# -----------------------------------------------------------------------
# Plugin events (for registration with PluginBase super().__init__)
# -----------------------------------------------------------------------
# COVAS:NEXT event classes must subclass Event and use @dataclass.
# For plugin events we re-use PluginEvent (kind='plugin') dispatched
# through helper.dispatch_event — no custom event class registration
# is needed.


# -----------------------------------------------------------------------
# Projection state model
# -----------------------------------------------------------------------

class NeutronRouteState(BaseModel):
    """Live route state exposed to HUD components and status generator."""
    active: bool = False
    source: str = ""
    destination: str = ""
    total_jumps: int = 0
    jumps_completed: int = 0
    jumps_remaining: int = 0
    progress_pct: int = 0
    distance_left: float = 0.0
    total_distance: float = 0.0
    current_system: str = ""
    next_system: str = ""
    is_neutron: bool = False
    boosted: bool = False
    upcoming: list[dict] = Field(default_factory=list)


class NeutronRouteProjection(Projection[NeutronRouteState]):
    """Projection that holds the current neutron highway route state."""

    StateModel = NeutronRouteState

    @override
    def process(self, event):
        # We don't process journal events directly — the sideeffect
        # pushes state into self.state after every route change or jump.
        return []



# -----------------------------------------------------------------------
# Pydantic models for action arguments
# -----------------------------------------------------------------------

class PlotNeutronRouteArgs(BaseModel):
    destination: str = Field(description="Destination system name.")
    source: str | None = Field(
        default=None,
        description="Source system name.  If omitted the current system is used.",
    )
    range: float | None = Field(
        default=None,
        description="Ship's unladen jump range in light-years.",
    )
    efficiency: int | None = Field(
        default=None,
        description="Supercruise efficiency (1-100, default 60).",
    )


class GetRouteStatusArgs(BaseModel):
    include_next: int | None = Field(
        default=3,
        description="How many upcoming systems to include (default 3).",
    )


class ClearRouteArgs(BaseModel):
    pass

class NeutronHighway(PluginBase):

    settings_config = PluginSettings(
        key="NeutronHighway",
        label="Neutron Highway",
        icon="route",
        grids=[
            SettingsGrid(
                key="general",
                label="General",
                fields=[
                    ToggleSetting(
                        key="enabled",
                        label="Enabled",
                        type="toggle",
                        readonly=False,
                        placeholder=None,
                        default_value=True,
                    ),
                ],
            ),
        ],
    )



    def __init__(self, plugin_manifest: PluginManifest):
        super().__init__(plugin_manifest)

        self._route: list[dict] = []
        self._current_index: int = 0
        self._source_system: str = ""
        self._destination_system: str = ""
        self._poll_task: asyncio.Task | None = None
        self._helper: PluginHelper | None = None

        # Projection for HUD + status
        self._projection = NeutronRouteProjection()

        # Cached state from COVAS built-in projections
        self._last_known_system: str = ""
        self._last_known_jump_range: float = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_chat_start(self, helper: PluginHelper):
        # Restore saved route
        if not self.settings.get("enabled", True):
            return

        self._load_route(helper)

        # Store helper for use in sideeffects
        self._helper = helper

        # Register the projection
        self._update_projection()
        helper.register_projection(self._projection)

        # Track journal events via a sideeffect
        helper.register_sideeffect(self._on_event)

        # Expose route state to the LLM via status generator
        helper.register_status_generator(self._status_generator)

        # Register actions
        helper.register_action(
            name="plot_neutron_route",
            description=(
                "Plot a neutron highway route from the current (or named) system "
                "to a destination system using the Spansh API.  The route is "
                "optimised for neutron-star supercharge boosts.  After plotting, "
                "the assistant automatically tracks the commander's FSDJump "
                "events against the route and can report progress at any time."
            ),
            parameters=PlotNeutronRouteArgs,
            method=self._action_plot_route,
        )

        helper.register_action(
            name="get_route_status",
            description=(
                "Return the current neutron highway route status: source, "
                "destination, total jumps, jumps completed, jumps remaining, "
                "next few systems, and total distance remaining."
            ),
            parameters=GetRouteStatusArgs,
            method=self._action_route_status,
        )

        helper.register_action(
            name="clear_route",
            description="Clear the active neutron highway route.",
            parameters=ClearRouteArgs,
            method=self._action_clear_route,
        )

        # Register plugin events (AI notification hooks)
        helper.register_event(
            name="neutron_route_plotted",
            should_reply_check=lambda e: True,
            prompt_generator=lambda e: (
                f"A neutron highway route from {e.plugin_event_content.get('source')} to "
                f"{e.plugin_event_content.get('destination')} has been plotted.  It has "
                f"{e.plugin_event_content.get('total_jumps')} jumps totalling "
                f"{e.plugin_event_content.get('total_distance'):.1f} Ly.  The first few "
                f"systems are: {e.plugin_event_content.get('first_systems')}.  "
                f"ACTION REQUIRED: Open the in-game Galaxy Map (press {chr(2)}), set the route target to the "
                f"first system on the route, and plot it so the commander can start following the neutron highway."
            ),
        )
        helper.register_event(
            name="neutron_route_progress",
            should_reply_check=lambda e: True,
            prompt_generator=lambda e: (
                f"Commander jumped to {e.plugin_event_content.get('system_name')} which is "
                f"jump {e.plugin_event_content.get('jump_number')}/{e.plugin_event_content.get('total_jumps')} "
                f"on the neutron highway route.  "
                f"{e.plugin_event_content.get('jumps_remaining')} jumps remain.  "
                f"{'⚡ Neutron star boost applied.' if e.plugin_event_content.get('is_neutron') and e.plugin_event_content.get('boosted') else ''}"
                f"{'⚠️ Neutron system — boost NOT applied, commander may have missed the scoop cone.' if e.plugin_event_content.get('is_neutron') and not e.plugin_event_content.get('boosted') else ''}"
                f"ACTION REQUIRED: Open the in-game Galaxy Map (press {chr(2)}), set the route target to "
                f"the NEXT system on the neutron highway route: {e.plugin_event_content.get('next_system')}, "
                f"and plot the route. This keeps the commander's in-game navigation aligned with the neutron highway."
            ),
        )
        helper.register_event(
            name="neutron_route_complete",
            should_reply_check=lambda e: True,
            prompt_generator=lambda e: (
                f"The commander has arrived at {e.plugin_event_content.get('destination')}, "
                f"completing the neutron highway route "
                f"({e.plugin_event_content.get('total_jumps')} jumps, "
                f"{e.plugin_event_content.get('total_distance'):.1f} Ly)."
            ),
        )
        helper.register_event(
            name="neutron_route_lost",
            should_reply_check=lambda e: True,
            prompt_generator=lambda e: (
                f"The commander jumped to {e.plugin_event_content.get('system_name')} which "
                f"is NOT on the active neutron highway route.  Expected "
                f"{e.plugin_event_content.get('expected_system')}.  The route tracker is "
                f"still active — ask the commander if they want to continue "
                f"tracking, re-plot, or clear the route."
            ),
        )

    def on_chat_stop(self, helper: PluginHelper):
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
        self._save_route(helper)
        self._helper = None

    # ------------------------------------------------------------------
    # Actions (called by the LLM)
    # ------------------------------------------------------------------

    def _action_plot_route(self, args: PlotNeutronRouteArgs, context: dict) -> str:
        """Submit a neutron route to Spansh and wait for results."""
        import urllib.request
        import urllib.parse

        source = args.source or self._current_system()
        if not source:
            return "Cannot determine source system.  Please specify it explicitly."

        jump_range = args.range or self._current_jump_range() or 50.0
        efficiency = args.efficiency or 60

        params = urllib.parse.urlencode({
            "from": source,
            "to": args.destination,
            "range": jump_range,
            "efficiency": efficiency,
            "neutron_star_boost": 1,
        })
        req = urllib.request.Request(
            SPANSH_ROUTE_URL,
            data=params.encode(),
            method="POST",
        )
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
        except Exception as exc:
            logger.error("Spansh route submit failed: %s", exc)
            return f"Failed to submit route to Spansh: {exc}"

        job_id = result.get("job")
        if not job_id:
            return f"Spansh returned no job ID: {result}"

        # Poll synchronously for simplicity — the action runs on the
        # LLM action thread so blocking is acceptable for now.
        route_data = self._poll_spansh_sync(job_id)
        if route_data is None:
            return f"Route calculation timed out (job {job_id})."

        jumps = route_data.get("system_jumps", [])
        if not jumps:
            return "Spansh returned an empty route."

        # Store route
        self._route = jumps
        self._current_index = 0
        self._source_system = route_data.get("source_system", source)
        self._destination_system = route_data.get("destination_system", args.destination)
        self._update_projection()
        self._save_route_cached()

        total_dist = route_data.get("distance", 0)
        neutron_count = sum(1 for j in jumps if j.get("neutron_star"))
        first_systems = [j["system"] for j in jumps[1:6]]
        first_str = ", ".join(first_systems) + (" ..." if len(jumps) > 6 else "")

        self._dispatch_event_safe(
            "neutron_route_plotted",
            {
                "source": self._source_system,
                "destination": self._destination_system,
                "total_jumps": len(jumps) - 1,
                "total_distance": total_dist,
                "neutron_stars": neutron_count,
                "first_systems": first_str,
            },
        )

        return (
            f"Neutron highway plotted: {self._source_system} → "
            f"{self._destination_system}\n"
            f"Jumps: {len(jumps) - 1}  |  Distance: {total_dist:.1f} Ly  |  "
            f"Neutron boosts: {neutron_count}\n"
            f"First stops: {first_str}\n"
            f"Route tracking is active — I'll follow your jumps automatically."
        )

    def _action_route_status(self, args: GetRouteStatusArgs, context: dict) -> str:
        if not self._route:
            return "No active route.  Use plot_neutron_route to plan one."

        total = len(self._route) - 1
        done = self._current_index
        remaining = total - done
        include_next = args.include_next or 3

        if done >= total:
            return (
                f"Route complete!  Arrived at {self._destination_system} "
                f"({total} jumps)."
            )

        current = self._route[self._current_index]
        upcoming = self._route[self._current_index + 1: self._current_index + include_next + 1]
        dist_left = current.get("distance_left", 0)

        lines = [
            f"Route: {self._source_system} → {self._destination_system}",
            f"Progress: {done}/{total} jumps ({remaining} remaining)",
            f"Distance remaining: {dist_left:.1f} Ly",
            f"Current system: {current['system']}"
            + (" (neutron star)" if current.get("neutron_star") else ""),
        ]
        if upcoming:
            up_strs = []
            for j in upcoming:
                marker = " ⚡" if j.get("neutron_star") else ""
                up_strs.append(f"  {j['system']}{marker}")
            lines.append(f"Next stops:\n" + "\n".join(up_strs))

        return "\n".join(lines)

    def _action_clear_route(self, args: ClearRouteArgs, context: dict) -> str:
        if not self._route:
            return "No active route to clear."

        name = f"{self._source_system} → {self._destination_system}"
        self._route = []
        self._current_index = 0
        self._source_system = ""
        self._destination_system = ""
        self._update_projection()
        self._delete_route_cached()
        return f"Cleared route {name}."

    # ------------------------------------------------------------------
    # Sideeffect: react to journal events
    # ------------------------------------------------------------------

    def _on_event(self, event: Any, states: dict[str, Any]):
        """React to incoming events.

        - Cache the current system and jump range from COVAS built-in
          projections (Location, Loadout) so actions can read them.
        - If the event is an FSDJump or CarrierJump, check it against
          the active route.
        """
        # --- cache latest projection state for action methods ----------
        self._cache_state(states)

        # --- route tracking (FSDJump + CarrierJump) ---------------------
        if not hasattr(event, "content"):
            return
        content = getattr(event, "content", None)
        if not isinstance(content, dict):
            return
        event_name = content.get("event")
        if event_name not in ("FSDJump", "CarrierJump"):
            return

        if not self._route or self._current_index >= len(self._route):
            return

        jumped_system = content.get("StarSystem")
        jumped_address = content.get("SystemAddress")
        if not jumped_system:
            return

        # Scan the ENTIRE remaining route for a match, not just the next
        # system. The in-game route planner may skip ahead through multiple
        # neutron route systems in a single jump.
        matched_index = None
        for i in range(self._current_index, len(self._route)):
            candidate = self._route[i]
            candidate_system = candidate["system"]
            candidate_id64 = candidate.get("id64")

            hit = False
            if jumped_address is not None and candidate_id64 is not None:
                hit = int(jumped_address) == int(candidate_id64)
            if not hit:
                hit = (
                    jumped_system.strip().lower()
                    == candidate_system.strip().lower()
                )
            if hit:
                matched_index = i
                break

        if matched_index is not None:
            # Fast-forward the route index to wherever we landed
            self._current_index = matched_index + 1
            boosted = content.get("BoostUsed") == 4
            self._projection.state.boosted = boosted
            self._update_projection()
            self._save_route_cached()

            total = len(self._route) - 1

            if self._current_index >= len(self._route):
                # Route complete
                total_distance = self._route[matched_index].get("distance_left", 0)
                self._dispatch_event_safe(
                    "neutron_route_complete",
                    {
                        "destination": self._destination_system,
                        "total_jumps": total,
                        "total_distance": total_distance,
                    },
                )
                self._route = []
                self._current_index = 0
                self._update_projection()
                self._delete_route_cached()
                return

            # Dispatch waypoint arrival — AI should open galaxy map and
            # plot the next system as the in-game route target.
            current_jump = self._route[self._current_index]
            next_sys = (
                self._route[self._current_index + 1]["system"]
                if self._current_index + 1 < len(self._route)
                else self._destination_system
            )
            self._dispatch_event_safe(
                "neutron_route_progress",
                {
                    "system_name": jumped_system,
                    "jump_number": self._current_index,
                    "total_jumps": total,
                    "jumps_remaining": total - self._current_index,
                    "is_neutron": current_jump.get("neutron_star", False),
                    "boosted": boosted,
                    "next_system": next_sys,
                    "distance_left": current_jump.get("distance_left", 0),
                },
            )
        else:
            # Only fire route_lost if the jumped system is NOWHERE in
            # the remaining route — it's a genuine off-course jump.
            expected_system = self._route[self._current_index]["system"]
            self._dispatch_event_safe(
                "neutron_route_lost",
                {
                    "system_name": jumped_system,
                    "expected_system": expected_system,
                },
            )

    # ------------------------------------------------------------------
    # Projection: maintain route state for HUD & status
    # ------------------------------------------------------------------

    def _update_projection(self):
        """Push current route state into the projection."""
        if not self._route:
            self._projection.state = NeutronRouteState()
            return

        total = len(self._route) - 1
        done = self._current_index
        remaining = total - done
        pct = round((done / total) * 100) if total > 0 else 0

        current = self._route[min(done, len(self._route) - 1)]
        next_sys = (
            self._route[done + 1]["system"]
            if done + 1 < len(self._route)
            else self._destination_system
        )
        dist_left = current.get("distance_left", 0)
        total_dist = self._route[-1].get("distance_left", 0)

        # Build upcoming list (next 5 systems with neutron flags)
        upcoming = []
        for j in self._route[done + 1: done + 6]:
            upcoming.append({
                "system": j["system"],
                "neutron": j.get("neutron_star", False),
            })

        self._projection.state = NeutronRouteState(
            active=True,
            source=self._source_system,
            destination=self._destination_system,
            total_jumps=total,
            jumps_completed=done,
            jumps_remaining=remaining,
            progress_pct=pct,
            distance_left=round(dist_left, 1),
            total_distance=round(total_dist, 1),
            current_system=current["system"],
            next_system=next_sys,
            is_neutron=current.get("neutron_star", False),
            upcoming=upcoming,
        )

    # ------------------------------------------------------------------
    # Status generator: feed route state into LLM context
    # ------------------------------------------------------------------

    def _status_generator(self, states: dict) -> list[tuple[str, Any]]:
        # Read from our projection's state model
        route_state = states.get("NeutronRouteProjection")
        if route_state is None:
            return []

        if hasattr(route_state, "active"):
            active = route_state.active
            source = route_state.source
            destination = route_state.destination
            jumps_completed = route_state.jumps_completed
            total_jumps = route_state.total_jumps
            jumps_remaining = route_state.jumps_remaining
            distance_left = route_state.distance_left
            current_system = route_state.current_system
            next_system = route_state.next_system
        elif isinstance(route_state, dict):
            active = route_state.get("active", False)
            source = route_state.get("source", "")
            destination = route_state.get("destination", "")
            jumps_completed = route_state.get("jumps_completed", 0)
            total_jumps = route_state.get("total_jumps", 0)
            jumps_remaining = route_state.get("jumps_remaining", 0)
            distance_left = route_state.get("distance_left", 0)
            current_system = route_state.get("current_system", "")
            next_system = route_state.get("next_system", "")
        else:
            return []

        if not active:
            return []

        if jumps_completed >= total_jumps:
            return [("Route", f"Complete — arrived at {destination}")]

        return [
            ("Active Route", f"{source} → {destination}"),
            ("Route Progress", f"{jumps_completed}/{total_jumps} jumps ({jumps_remaining} remaining)"),
            ("Distance Left", f"{distance_left:.0f} Ly"),
            ("Current System", current_system),
            ("Next System", next_system),
        ]

    # ------------------------------------------------------------------
    # Spansh API polling (synchronous for action thread)
    # ------------------------------------------------------------------

    def _poll_spansh_sync(self, job_id: str, timeout: int = 300) -> dict | None:
        """Poll Spansh for route results with exponential back-off (blocking)."""
        import urllib.request
        import time

        delay = POLL_INITIAL_S
        max_attempts = int(timeout / POLL_INITIAL_S)
        start = time.monotonic()

        for attempt in range(max_attempts):
            time.sleep(delay)
            if time.monotonic() - start > timeout:
                break
            try:
                url = f"{SPANSH_RESULTS_URL}/{job_id}"
                with urllib.request.urlopen(url, timeout=15) as resp:
                    data = json.loads(resp.read())
            except Exception as exc:
                logger.debug("Poll attempt %d failed: %s", attempt + 1, exc)
                delay = min(delay * 2, POLL_MAX_S)
                continue

            status = data.get("status")
            if status == "ok":
                return data.get("result", data)
            elif status == "error":
                logger.error("Spansh route job %s failed: %s", job_id, data.get("error"))
                return None
            # Still processing
            delay = min(delay * 2, POLL_MAX_S)

        logger.warning("Spansh route job %s timed out after %.0fs", job_id, time.monotonic() - start)
        return None

    # ------------------------------------------------------------------
    # State caching from COVAS built-in projections
    # ------------------------------------------------------------------

    def _cache_state(self, states: dict[str, Any]):
        """Cache current system and jump range from COVAS projections.

        COVAS:NEXT built-in projections are keyed by class name:
          - "Location" -> LocationState  (field: StarSystem)
          - "Loadout"  -> LoadoutState   (field: MaxJumpRange)
        """
        loc = states.get("Location")
        if loc is not None:
            if hasattr(loc, "StarSystem"):
                self._last_known_system = loc.StarSystem
            elif isinstance(loc, dict):
                self._last_known_system = loc.get("StarSystem", self._last_known_system)

        loadout = states.get("Loadout")
        if loadout is not None:
            if hasattr(loadout, "MaxJumpRange"):
                jr = loadout.MaxJumpRange
                if jr is not None:
                    self._last_known_jump_range = float(jr)
            elif isinstance(loadout, dict):
                jr = loadout.get("MaxJumpRange")
                if jr is not None:
                    self._last_known_jump_range = float(jr)

    def _current_system(self) -> str | None:
        return self._last_known_system or None

    def _current_jump_range(self) -> float | None:
        return self._last_known_jump_range or None

    # ------------------------------------------------------------------
    # Event dispatching
    # ------------------------------------------------------------------

    def _dispatch_event_safe(self, name: str, data: dict):
        """Dispatch a plugin event, catching any errors."""
        try:
            if self._helper is None:
                return
            self._helper.dispatch_event(PluginEvent(
                plugin_event_name=name,
                plugin_event_content=data,
            ))
        except Exception as exc:
            logger.warning("Failed to dispatch event %s: %s", name, exc)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _route_file(self, helper: PluginHelper) -> Path:
        data_path = helper.get_plugin_data_path(self.plugin_manifest)
        return Path(data_path) / "route.json"

    def _save_route_cached(self):
        if self._helper is None:
            return
        self._save_route(self._helper)

    def _delete_route_cached(self):
        if self._helper is None:
            return
        self._delete_route(self._helper)

    def _save_route(self, helper: PluginHelper):
        try:
            path = self._route_file(helper)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "route": self._route,
                "current_index": self._current_index,
                "source": self._source_system,
                "destination": self._destination_system,
            }
            path.write_text(json.dumps(payload, ensure_ascii=False))
        except Exception as exc:
            logger.warning("Failed to save route: %s", exc)

    def _load_route(self, helper: PluginHelper):
        try:
            path = self._route_file(helper)
            if path.exists():
                payload = json.loads(path.read_text())
                self._route = payload.get("route", [])
                self._current_index = payload.get("current_index", 0)
                self._source_system = payload.get("source", "")
                self._destination_system = payload.get("destination", "")
                logger.info(
                    "Restored route: %s → %s (%d jumps, at index %d)",
                    self._source_system,
                    self._destination_system,
                    len(self._route),
                    self._current_index,
                )
        except Exception as exc:
            logger.warning("Failed to load route: %s", exc)

    def _delete_route(self, helper: PluginHelper):
        try:
            path = self._route_file(helper)
            if path.exists():
                path.unlink()
        except Exception as exc:
            logger.warning("Failed to delete route file: %s", exc)


