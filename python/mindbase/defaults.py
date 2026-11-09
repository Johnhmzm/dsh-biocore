"""Reference defaults from digital-fly literature.

These are documented starting points, not laws. Runtime knobs live in
MindConfig; changing a default should be a deliberate design change.
"""

# Digital fly (FlyWire / MaleCNS)
NEURONS = 139_255
SYNAPSES = 50_000_000
CELL_TYPES = 11_710
MB_COMPARTMENTS = 15

# Mushroom body compression
KC_COUNT = 2_000
KC_SPARSITY = 0.05
KC_SLOTS = 256
KC_ACTIVE = max(1, round(KC_SLOTS * KC_SPARSITY))  # 13

# Approximate scale only (used for comments / coarse priors)
MBON_COUNT = 34
DAN_COUNT = 130

# Runtime contract
MAX_WM_SLOTS = 5
MEMORY_CAPACITY = 256
MAX_GOALS = 3
MAX_LLM_CALLS_PER_STEP = 1
