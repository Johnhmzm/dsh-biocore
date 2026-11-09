"""mindbase: a layered, paper-grounded base for a human-like agent.

Layers:
  L0 substrate/noise  -> noise.py
  L1 drives/allostasis -> drives.py, clock.py
  L2 affect/appraisal  -> appraisal.py, affect.py
  L3 attention/WM      -> attention.py, working_memory.py
  L4 memory            -> memory.py
  L5 social/persona/self/meta/autonomy -> social.py, persona.py, self_model.py, meta.py, autonomy.py
  L6 expression        -> (not implemented; LLM adapter later)
"""

from .config import MindConfig
from .actions import Action
from .types import Event, Appraisal, Outcome, Feedback, LLMRequest
from .engine import MindBase

__all__ = ["MindConfig", "Event", "Appraisal", "Outcome", "LLMRequest", "Feedback", "Action", "MindBase"]
