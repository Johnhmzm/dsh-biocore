"""Layer boundaries for mindbase.

Each layer is a separate module; the orchestrator is mindbase.engine.
Import concrete implementations from the layer module or from the package.
"""

LAYER_MAP = {
    "L0_substrate": "mindbase.layers.l0_substrate",
    "L1_homeostasis": "mindbase.layers.l1_homeostasis",
    "L2_affect": "mindbase.layers.l2_affect",
    "L3_attention": "mindbase.layers.l3_attention",
    "L4_learning": "mindbase.layers.l4_learning",
    "L5_self_social": "mindbase.layers.l5_self_social",
    "L6_expression": "mindbase.layers.l6_expression",
    "L7_evaluation": "mindbase.layers.l7_evaluation",
}

__all__ = ["LAYER_MAP"]
