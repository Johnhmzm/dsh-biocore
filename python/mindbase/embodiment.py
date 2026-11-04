from __future__ import annotations

from typing import Any, Dict, Protocol

from .types import Action, Event


class Embodiment(Protocol):
    def sensor_event(self) -> Event: ...
    def apply_action(self, action: Action) -> None: ...
    def step(self) -> Dict[str, Any]: ...


class NoBodyAdapter:
    """Software-only body: no sensors, no motors, chat is just one action."""

    def available(self) -> bool:
        return True

    def sensor_event(self) -> Event:
        return Event(kind="tick", speaker="self")

    def apply_action(self, action: Action) -> None:
        return None

    def step(self) -> Dict[str, Any]:
        return {}


class HumanoidAdapter:
    """Optional humanoid body. No dependency by default."""

    def __init__(self, *args: Any, **kwargs: Any):
        if not self.available():
            raise RuntimeError("no humanoid backend installed (mujoco/isaacsim)")
        self.args = args
        self.kwargs = kwargs

    @staticmethod
    def available() -> bool:
        import importlib.util

        return any(importlib.util.find_spec(x) is not None for x in ("mujoco", "isaacsim"))

    def sensor_event(self) -> Event:
        raise NotImplementedError("wire humanoid sensors to MindBase.Event")

    def apply_action(self, action: Action) -> None:
        raise NotImplementedError("wire MindBase.Action to humanoid motors")

    def step(self) -> Dict[str, Any]:
        raise NotImplementedError("advance the humanoid simulation")


class FlyGymAdapter:
    """Optional body adapter. The base does not depend on FlyGym/MuJoCo."""

    def __init__(self, *args: Any, **kwargs: Any):
        if not self.available():
            raise RuntimeError("flygym is not installed; install it before using the embodiment adapter")
        import flygym  # type: ignore

        self.flygym = flygym
        self.args = args
        self.kwargs = kwargs

    @staticmethod
    def available() -> bool:
        try:
            import importlib.util

            return importlib.util.find_spec("flygym") is not None
        except Exception:
            return False

    def sensor_event(self) -> Dict[str, Any]:
        raise NotImplementedError("wire FlyGym observations to MindBase.Event here")

    def apply_action(self, action: Any) -> None:
        raise NotImplementedError("wire MindBase.Action to FlyGym motor commands here")

    def step(self) -> Dict[str, Any]:
        raise NotImplementedError("advance the FlyGym simulation here")
