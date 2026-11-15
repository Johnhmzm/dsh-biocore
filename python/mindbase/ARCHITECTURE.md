# mindbase architecture

One layer = one module. `engine.py` is the orchestrator, not a layer.

| Layer | Module | Responsibility |
|---|---|---|
| L0 substrate | `layers/l0_substrate.py` | noise, defaults, FlyWire/MaleCNS priors |
| L1 homeostasis | `layers/l1_homeostasis.py` | drives, clock, sleep pressure |
| L2 affect | `layers/l2_affect.py` | appraisal, V/A/stress/mood/neuromodulators |
| L3 attention | `layers/l3_attention.py` | salience, working memory |
| L4 learning | `layers/l4_learning.py` | episodic memory, mushroom body, learner |
| L5 self/social | `layers/l5_self_social.py` | bonds, relationship stages + decay/repair, persona numbers + construct registry, narrative identity, self-model, goals, values, will, lifecycle |
| L6 expression | `layers/l6_expression.py` | Action/expression gate, relationship budget, expression policy, adapter, OpenAI-compatible client |
| L7 evaluation | `layers/l7_evaluation.py` | metrics, ablation, OOD, calibration, story loop |

## Mushroom body package

`mb/` is split by component:

- `mb/kc.py`: KCField
- `mb/mbon.py`: MBON
- `mb/dan.py`: DAN
- `mb/apl.py`: APL
- `mb/habituation.py`: Habituation
- `mb/mushroom_body.py`: MushroomBody wiring

Import the public API from `mindbase.mb`.

## Offline training

`training/` is a pure-Python head layer, separate from online learning:

- `features.py`: state + hashed event features
- `linear.py`: softmax / linear regression by SGD
- `appraisal.py` / `policy.py` / `general.py` / `world_model.py`
- `loaders.py`: CPED / ESConv / EmoBank -> StorySample
- `MindBase(heads=...)`: appraisal blend + policy prior + world rollout
- `social_eval.py`: ToMBench / Social IQa evaluation
- `train.py`: CLI
- `base_only.py`: run the base with no LLM

## Rules

1. Dependencies point down only: L7 may read all; L0 imports no layer.
2. `engine.py` wires layers; layers do not import `engine`.
3. `types.py` is shared contracts only (Event, Appraisal, Outcome, LLMRequest).
4. Each layer must be importable on its own: `python -c "import mindbase.layers.l4_learning"`.
5. New cross-layer behavior goes into the orchestrator or a new layer module, not into an existing layer.
