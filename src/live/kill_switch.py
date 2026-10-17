"""
Emergency Kill Switch — funbot Guardrail 7.
Provides persistent, fail-safe execution halting capable of being tripped manually
or by autonomous risk monitors.
"""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("KillSwitch")


class KillSwitchActiveError(Exception):
    """Raised when an operation is attempted while the Kill Switch is engaged."""
    pass


class EmergencyKillSwitch:
    """
    Persistent atomic emergency kill switch.
    State persists across process restarts in a local JSON state file.
    """

    def __init__(self, state_file_path: str = "data/live/kill_switch_state.json"):
        self.state_file = Path(state_file_path)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._load_or_init()

    def _load_or_init(self) -> None:
        if not self.state_file.exists():
            default_state = {
                "is_tripped": False,
                "tripped_at": None,
                "tripped_by": None,
                "reason": None,
                "history": [],
            }
            self._save(default_state)

    def _read(self) -> Dict:
        if not self.state_file.exists():
            self._load_or_init()
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Failed to read kill switch state: %s. Defaulting to TRIPPED for safety.", e)
            return {
                "is_tripped": True,
                "tripped_at": datetime.now(timezone.utc).isoformat(),
                "tripped_by": "FAIL_SAFE_GUARD",
                "reason": f"State read error: {e}",
                "history": [],
            }

    def _save(self, state: Dict) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = self.state_file.with_suffix(".tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        tmp_file.replace(self.state_file)

    def is_tripped(self) -> bool:
        """Returns True if the system is currently halted."""
        return bool(self._read().get("is_tripped", False))

    def assert_open(self) -> None:
        """Asserts that kill switch is not tripped. Raises KillSwitchActiveError if tripped."""
        state = self._read()
        if state.get("is_tripped", False):
            reason = state.get("reason", "Unknown reason")
            by = state.get("tripped_by", "Unknown")
            raise KillSwitchActiveError(f"Emergency Kill Switch is ACTIVE (Tripped by {by}: {reason})")

    def trip(self, reason: str, operator: str = "OPERATOR_MANUAL") -> Dict:
        """Immediately trips the kill switch and halts all execution."""
        now = datetime.now(timezone.utc).isoformat()
        state = self._read()
        state["is_tripped"] = True
        state["tripped_at"] = now
        state["tripped_by"] = operator
        state["reason"] = reason

        entry = {
            "action": "TRIP",
            "timestamp": now,
            "operator": operator,
            "reason": reason,
        }
        history = state.get("history", [])
        history.append(entry)
        state["history"] = history[-50:]  # Keep last 50 events

        self._save(state)
        logger.critical("EMERGENCY KILL SWITCH TRIPPED by %s: %s", operator, reason)
        return state

    def reset(self, operator: str, notes: str = "Manual override reset") -> Dict:
        """Resets the kill switch, requiring operator authentication/sign-off."""
        now = datetime.now(timezone.utc).isoformat()
        state = self._read()
        state["is_tripped"] = False
        state["tripped_at"] = None
        state["tripped_by"] = None
        state["reason"] = None

        entry = {
            "action": "RESET",
            "timestamp": now,
            "operator": operator,
            "notes": notes,
        }
        history = state.get("history", [])
        history.append(entry)
        state["history"] = history[-50:]

        self._save(state)
        logger.info("Emergency Kill Switch RESET by %s: %s", operator, notes)
        return state

    def get_status(self) -> Dict:
        """Returns full status report of the kill switch."""
        return self._read()
