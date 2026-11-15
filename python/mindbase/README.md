# mindbase

A layered, paper-grounded base for a human-like agent.
Pure Python, no external deps, no LLM, no AstrBot.

## Layers

| Layer | Module | What it does | Key papers |
|---|---|---|---|
| L0 substrate/noise | noise.py, digital_prior.py | OU / multi-scale 1/f-ish noise; FlyWire/MaleCNS structural priors | Ankri 2023; Gilden 1995; FlyWire 2024; MaleCNS 2026 |
| L1 drives/allostasis | drives.py, clock.py | energy/social/safety/curiosity; population clock; sleep | Theriault 2025; Candia-Rivera 2026; population clock 2026 |
| L2 appraisal/affect | appraisal.py, affect.py | RPE, novelty, threat -> V/A/stress/mood/DA/OA/5-HT | Shiu 2024; Huang 2024; Adolphs 2026 |
| L3 attention/WM | attention.py, working_memory.py | salience gate; 5 slots; decay | Chittka 2025; active sampling 2026 |
| L4 memory/plasticity | memory.py, mb/, learner.py | episodic sparse memory; KC sparse code -> MBON -> DAN-gated learning; habituation | Parnas 2024; Huang 2024; MB motifs 2025; habituation 2026 |
| L5 lifecycle | lifecycle.py | age, sleep, allostatic scar, aging DA reinforcement | aging operant learning 2026; PAM-DAN consolidation 2025; fast-slow allostasis 2026 |
| L5 social/persona/self/meta/autonomy/will | social.py, relationships.py, persona.py, self_model.py, meta.py, autonomy.py, intention.py, will.py | bonds, relationship stages, feedback/ostracism, parameter seed, agency, confidence, endogenous impulse, intentions, willpower | OT/VP 2026; de Bivort 2026; Butlin 2023 |
| L6 expression/gate | llm_gate.py | base decides when to call the LLM; LLM is the vocal organ | digital fly motor output; FC2 single goal |
| L7 evaluation | selftest.py, demo.py, evaluation.py | invariants + trace + digital-fly metrics | Turing 2024; EmotionQueen 2024; Representational geometry 2026 |

## Digital fly first

The base is a **compressed functional digital fly**, not a chatbot with fly flavor.

- `digital_prior.py`: FlyWire/MaleCNS priors (139,255 neurons, ~5e7 synapses, ~2000 KCs, 5% sparsity).
- `mb/`: 256 KC slots, ~5% active -> a compressed KC field.
- Full-brain LIF (Shiu 2024) is the reference architecture, not the runtime.
- Evaluation follows digital-fly practice: component metrics, ablation, not just video/demo.

## Architecture

One layer = one module, under `layers/`:

- `layers/l0_substrate.py` ... `layers/l7_evaluation.py`
- `engine.py` is the orchestrator, not a layer.
- `mb/` is split into `kc.py`, `mbon.py`, `dan.py`, `apl.py`, `habituation.py`, `mushroom_body.py`.
- Full rules: `ARCHITECTURE.md`.

## Usage

    from mindbase import MindBase, MindConfig, Event

    mb = MindBase(MindConfig(seed=42))
    out = mb.step(Event(text='你好', speaker='A'))
    print(out.action, out.reason, out.prompt_state)

## Run

    cd C:\Users\Administrator\Desktop\deepseek
    python -m mindbase.selftest
    python -m mindbase.demo

## Volition

The will is an arbitration, not a random coin flip:

- impulse = autonomy.h / threshold (what I want now)
- intention = top of IntentionStack (goal, action, strength, commitment, expiry)
- willpower = depletable budget (recovers with rest, drains with stress/conflict)
- high willpower -> follow intention; low willpower -> give in to impulse
- after acting: willpower cost; after speaking: autonomy.h *= 0.3

This is an operational model of volition, not a claim about free will or consciousness.

## LLM gate

The base owns the decision to call the LLM. It never calls the LLM itself.

    out = mb.step(event)
    if out.llm_request is not None:
        text = llm.generate(out.llm_request)
        mb.post_reply(text, feedback)
    else:
        pass  # stay silent

- `llm_request.mode`: reply / proactive / clarify / inner / tool
- `llm_request.context`: mood, drives, focus, confidence, willpower, intention, goal, salience, relationship, relationship_guidance, recent
- `llm_request.max_tokens` is relationship-sensitive: stranger 140 -> intimate 360
- proactive initiation is blocked for stranger / acquaintance; familiar+ can initiate
- `None`: silence / asleep / veto / low confidence

## Mushroom body

`mb/` is the Drosophila core of learning (one file per component):

- KCField: cue -> sparse Kenyon-cell code (k of n)
- MBON x2: reward channel and punishment channel read the KC code
- DAN x2: reward/punishment teaching signals; value = reward - punishment
- APL: feedback inhibition / sparsity gain
- Habituation: repeated cue -> weaker; negative -> sensitize

`learner.py`:

- on_event: habituation/sensitization + eligibility trace
- on_reply(text, feedback): admission first (`low_signal` / `redundant` rejected); admitted -> current cue full update + recent cues decayed credit
- consolidate: weight decay + homeostatic scaling + curriculum replay (surprise × rarity)

`MindBase.post_reply(text, feedback)` wires memory + agency + autonomy + learner + social feedback.

## Quality

- Every module is plain in -> out; it should return neutral defaults, not raise into the caller.
- `MindBase.assert_invariants()` checks ranges, capacity, KC sparsity, and weight bounds.
- Same seed + same events => same snapshot (deterministic).
- 172 tests cover invariants, no-throw, determinism, LLM gate, MB channels, specificity/cumulative/reversible, delayed credit, consolidation, admission, curriculum priority, lifecycle/aging/sleep, evaluation metrics, relationship stages, feedback weighting.
- Tests:

      cd C:\Users\Administrator\Desktop\deepseek
      python -m unittest discover -s tests -t . -v

## Lifecycle

- `lifecycle.py`: age/stage, aging DA reinforcement scale, sleep consolidation, allostatic scar.
- `MindBase.sleep(duration)` is offline rest, not a metaphor.

## Relationships

`relationships.py` derives a stage from a bond:

    stranger -> acquaintance -> familiar -> trusted -> intimate

- score = 0.50 * familiarity + 0.35 * trust + 0.15 * attachment
- thresholds 0.30 / 0.45 / 0.60 / 0.75, hysteresis 0.03
- `Social.stage(speaker)`, `Bond.stage`
- included in `LLMRequest.context["relationship"]`
- stage sets the token budget and style: stranger 140 / acquaintance 180 / familiar 240 / trusted 300 / intimate 360
- stranger / acquaintance do **not** initiate proactively; familiar / trusted / intimate can
- idle > 1 day decays: familiarity tau 30d, trust 60d, attachment 90d
- `MindBase.repair(speaker, effort)` raises trust/attachment (apology / care)

## Social feedback

`post_reply(text, feedback, speaker=None, responded=True)` now closes the social loop:

- responded=True: trust += 0.04 * feedback; attachment += 0.03 * max(0, feedback)
- responded=False: trust -= 0.03; attachment -= 0.02; social drive -= 0.02; signal = -0.4
- affect is updated through an Appraisal(social=signal)
- `post_reply` returns `out["social"] = {speaker, responded, signal}`

## Feedback weighting

`post_reply(..., feedback, feedback_source="user")` accepts a float or `Feedback(valence, source, confidence)`:

| source | weight |
|---|---|
| user | 1.00 |
| llm | 0.50 |
| self | 0.35 |

- negative feedback gets **1.25x** (negativity bias)
- LLM feedback is also scaled by relationship: stranger 1.0x, acquaintance 1.1x, familiar 1.2x, trusted 1.3x, intimate 1.4x
- effective feedback is clipped to [-1, 1]
- returned as `out["feedback"] = {raw, effective, source, confidence, relationship, source_weight}`

Examples:

    user  raw +0.5 -> effective +0.500 (stranger)
    llm   raw +0.5 -> effective +0.250 (stranger)
    user  raw -0.5 -> effective -0.625
    llm   raw +1.0 -> effective +0.700 (intimate)

## Goals

`goals.py` turns a long-horizon goal into an intention:

- `goals.add(name, action="speak", priority=0.6, commitment=0.6, ttl=3600)`
- top goal = max(priority * commitment * (0.5 + 0.5 * value_weight))
- `top(values)` and `conflict(values)` are values-aware; changing value weights changes the winner
- `sync_intentions` adds it to IntentionStack
- `on_action` +0.10 progress per matching action; completed at 1.0
- expired if `now >= deadline`
- `LLMRequest.context`: `goal`, `goal_progress`, `goal_priority`, `goal_action`, `goal_conflict`

Conflict handling:

- `goals.conflict()`: level high/medium/low, gap score, winner/rival
- high conflict **freezes proactive action** (`silence / goal_conflict`)
- directed requests still pass (external request overrides goal conflict)
- intention strength is penalized by 0.5 * conflict score

## Values

`values.py` drifts slowly from experience:

- positive feedback: care ↑, honesty ↑, safety ↓ slightly
- negative feedback: safety ↑, order ↑, care ↓
- goal completion: the goal's value weight ↑
- bounded [0, 1], lr = 0.02, returned as `out["values_delta"]`

## Persona

`persona.py` converts Big Five + physiology/biology into numbers and injects them into lower layers:

- N -> stress reactivity / recovery
- E -> social baseline / DA baseline / autonomy gain
- O -> curiosity baseline / learning rate / circadian phase
- A -> 5-HT baseline
- C -> energy / safety / willpower
- `Persona.from_profile(dict)`, `Persona.from_prompt(text)`, `Persona.numbers()`, `Persona.apply_to(mb)`
- numbers are exposed in `LLMRequest.context["persona"]`

See [人格-生理-生物-数值注入-20261019.md](../docs/人格-生理-生物-数值注入-20261019.md).

## Constructs

`constructs.py` + `constructs.json` is a declarative construct -> numeric registry:

- `expressive_suppression`: stress ↑, recovery ↓, expressivity ↓
- `self_silencing`: autonomy ↓, assertiveness ↓, help_seeking ↓
- `camouflaging_depression`: stress ↑, baseline_v ↓, masking ↑
- add a new construct with `registry.register(id, {"deltas": {...}})` or edit JSON
- `Persona.from_profile({"constructs": {...}})`
- `LLMRequest.context["constructs"]` / `["expression"]`

See [人格构念-数值化-通用注册表-20261019.md](../docs/人格构念-数值化-通用注册表-20261019.md).

## Narrative

`narrative.py` keeps 3-5 slow self-statements:

- "我重视好奇" / "我正在追求X" / "我的身份连续性约为0.71"
- coherence = mean entry weight
- exposed in `LLMRequest.context["narrative"]`
- template-based; an LLM may verbalize later, but the base owns the content

## Self-model

`self_model.py` has a slow identity variable:

- `identity`: 0..1, "am I still me?"
- contradictions (large value shift + goal conflict) drop `value_stability` and `identity`
- `tick(dt)` recovers `value_stability` and lets identity drift back
- `LLMRequest.context`: `identity`, `continuity`, `agency`
- this is operational, not a consciousness claim

## Expression

`expression.py` turns construct-derived numbers into a reply policy:

- expressivity -> max_tokens (0.70x–1.30x of stage budget)
- masking / low expressivity -> `guarded`
- high expressivity -> `expressive`
- assertiveness -> `directness`; self_disclosure -> `disclosure`
- context: `expression_style`, `expression_guidance`, `directness`, `disclosure`, `guard`

## LLM adapter

`llm_adapter.py` is a stateless bridge:

    adapter = LLMAdapter(mb, client)
    result = adapter.handle(Event(text="在吗", speaker="A", directed=True))

- no `llm_request` -> no client call
- `client.generate(request)` -> text
- `mb.post_reply(text, feedback)` -> learning + social + identity
- `ScriptedLLM` for tests, `TemplateLLM` as no-model fallback
- see [LLM适配器-20261019.md](../docs/LLM适配器-20261019.md)
- evaluation: `adapter_wording_invariance()` — same event/feedback, different replies -> same state (memory text may differ)

## Real LLM client

`llm_openai.py` is a minimal OpenAI-compatible client (stdlib only):

    export LLM_BASE_URL=http://127.0.0.1:11434/v1
    export LLM_API_KEY=...
    export LLM_MODEL=qwen2.5:7b

    from mindbase.llm_openai import OpenAICompatClient
    from mindbase.llm_adapter import LLMAdapter

    adapter = LLMAdapter(mb, OpenAICompatClient.from_env(
        chat_template_kwargs={"enable_thinking": False}
    ))

- no state, no memory, no decision; text in / text out
- works with Ollama / vLLM / LM Studio / cloud
- `.env.example` has the variable names
- tested with local MiniCPM-V 4.7 via llama.cpp (`127.0.0.1:8080`); disable thinking with `chat_template_kwargs`
- optional `character_prompt` / `LLM_CHARACTER_PROMPT_FILE`: roleplay is expression-layer only; base state still decides
- if no key/endpoint, keep using `TemplateLLM`

## Story loop

`story_loop.py` runs a 20-line story corpus + 5 held-out lines:

- line -> event -> adapter reply -> RuleJudge
- only score >= 0.55 gets feedback and enters replay
- `consolidate()` at the end
- `noise_sweep()` varies temperature; `compare_clients()` compares API vs local
- see [剧情回路-20261019.md](../docs/剧情回路-20261019.md)

## Base-only and training

No LLM required:

    python -m mindbase.base_only

Offline pure-Python training from annotated stories:

    python -m mindbase.training.train

- `AppraisalModel`: event+state -> valence/arousal/threat/social/relevance
- `PolicyModel`: event+state -> speak/silence/approach/avoid/repair
- `WorldModel`: event+state+action -> next stress/social/mood/energy/identity/reward
- data: `data/story_annotations.json`
- see [底座离线训练-20261019.md](../docs/底座离线训练-20261019.md)

## Dataset loaders

`training/loaders.py` converts dialogue/emotion data into `StorySample`:

- `load_cped`, `load_esconv`, `load_emobank`, `load_all`
- supports JSON / JSONL / CSV
- maps emotion -> appraisal, strategy -> action, reply -> outcome, improvement -> reward
- fixtures: `data/samples/*` (13 samples)
- see [数据集加载器-20261019.md](../docs/数据集加载器-20261019.md)

## Trained heads

`MindBase(heads=...)` accepts offline `Appraisal / Policy / World` models:

- Appraisal: blends 35% of the learned appraisal into `appraiser`
- Policy: action prior above 0.55, only in fallback paths
- World: one-step reward rollout, margin 0.10
- no heads => old behavior unchanged
- see [训练头接入-20261019.md](../docs/训练头接入-20261019.md)

## Real data held-out

- CPED real: `data/real/CPED/valid_split.csv` (11,139 rows)
- EmoBank real: `data/real/EmoBank/emobank.csv` (10,062 rows)
- ESConv: only fixture (repo has no data file)
- `training/evaluate.py` uses group split by `Dialogue_ID` / `split`
- result: CPED balanced policy acc 0.4683, balanced_acc 0.1209; unbalanced accuracy 0.9613 was a majority illusion
- see [真实数据-heldout-20261019.md](../docs/真实数据-heldout-20261019.md)

## Action and domain

Chat is one action, not the goal:

- `Outcome.act = Action(kind, modality, style)`
- `communicate/text` is chat; `withdraw/none` is silence
- `StorySample` has `domain`, `action_kind`, `modality`
- CPED/ESConv -> chat/communicate/text; EmoBank -> appraisal/appraise/none
- `evaluate_split` reports `domains`
- see [通用动作与域-20261019.md](../docs/通用动作与域-20261019.md)

## General action policy

`training/general.py` learns `action_kind`, not chat style:

- 11 kinds: communicate / approach / avoid / explore / rest / repair / protect / seek_help / give_help / withdraw / appraise
- `data/general_actions.json` has 11 cross-domain samples
- `GeneralPolicyModel` is parallel to `PolicyModel`
- `MindBase(heads={"general_policy": ...})` uses it as action prior
- see [通用动作策略-20261019.md](../docs/通用动作策略-20261019.md)

## Social / ToM evaluation

`training/social_eval.py`:

- ToMBench real: 600 False Belief samples
- Social IQa: fixture + local JSONL loader (official source unreachable)
- `HeuristicChooser` baseline; `LLMChoicePredictor(client)`
- heuristic: ToMBench 0.225 (full), MiniCPM 600-sample 0.5767
- multi-task: MiniCPM 0.30–0.62 across 7 ToM tasks
- see [social-ToM评测-20261019.md](../docs/social-ToM评测-20261019.md)

## Theory of mind

`theory_of_mind.py` is a non-LLM ToM module:

- per-speaker beliefs / knowledge / desires / intentions
- second-order "A thinks B knows"
- `predict_action` -> approach / avoid / communicate
- wired into `MindBase.step`, `prompt_state`, `snapshot`
- heuristic, not a claim of human-level ToM
- see [心智理论模块-20261019.md](../docs/心智理论模块-20261019.md)

## OCC appraisal

`Appraisal` now carries OCC-style dimensions:

- goal_relevance / goal_congruence
- agency (-1 other, 0 circumstance, +1 self)
- certainty / coping_potential / norm_compliance
- occ_emotion: pride / gratitude / fear / anger / guilt / shame / ...
- exposed in `prompt_state["occ"]` and `snapshot["appraisal"]`
- see [OCC评价维度-20261019.md](../docs/OCC评价维度-20261019.md)

## Memory activation

`SparseMemory` now ranks by four components:

- recency / importance / frequency / relevance
- default weights .35 / .25 / .20 / .20
- `retrieve_top_k(cue, now, k)`; legacy `retrieve` still works
- hits and last_access update on retrieval
- see [记忆激活检索-20261019.md](../docs/记忆激活检索-20261019.md)

## Global workspace

`workspace.py` implements LIDA-style competition:

- candidates: event / affect / goal / memory
- salience -> softmax -> winner + broadcast
- exposed in `prompt_state["workspace"]` and `snapshot["workspace"]`
- LLM reads it, does not control it
- see [全局工作空间-20261019.md](../docs/全局工作空间-20261019.md)

## Head persistence

- `training/persist.py`: `save_heads` / `load_heads`
- JSON, no pickle
- supports appraisal / policy / general_policy / world
- see [训练头持久化-20261019.md](../docs/训练头持久化-20261019.md)

## Calibration and embodiment

- `training/calibrate.py`: memory-weight grid search, best 0.8065
- `data/calibrated_memory_weights.json`
- `embodiment.py`: optional FlyGym adapter skeleton (not installed)
- see [校准与具身-20261019.md](../docs/校准与具身-20261019.md)

### Cognitive priors

OCC/ToM now influence `_decide`:

- low coping + fear/shame/guilt -> silence
- pride + congruent + salient -> speak
- ToM predicts avoid + threat -> silence

see [认知先验决策-20261019.md](../docs/认知先验决策-20261019.md).

## Real social actions

- `load_tombench_actions()` from ToMBench action tasks
- 40 real social-action samples mapped to action_kind
- see [真实社会动作-20261019.md](../docs/真实社会动作-20261019.md)

## choices13k

- 2,000 real human risky choices
- loader: `load_choices13k`
- approach = higher variance choice, avoid = lower
- A/B choice model: 0.4811, below majority 0.5472
- not suitable as base action data
- see [choices13k决策-20261019.md](../docs/choices13k决策-20261019.md)

## LongMemEval

- oracle data (500 items) + retrieval harness
- metrics: Recall@1 / Recall@k / MRR, recency baseline
- default retrieval is BM25; LongMemEval +20 distractors: recall@1 .7333
- see [LongMemEval-20261019.md](../docs/LongMemEval-20261019.md)

## Embedding retrieval

- `embedding.py`: OpenAI-compatible client; `from_astrbot()` reads AstrBot
- Qwen3-Embedding-0.6B, 1024-dim
- LongMemEval 30 + 20 distractors: embedding recall@1 .8667 vs BM25 .6667
- `SparseMemory.retrieve_embedding(cue, now, k, client)`
- `retrieve_hybrid(cue, now, k, client, alpha=.6)`: 12 samples, recall@1 .9167
- cache + chunking + rerank client; rerank no gain on 8 samples
- see [Embedding检索-20261019.md](../docs/Embedding检索-20261019.md)

## Long-run benchmark

- `longrun.py`: 30-day simulated run
- identity .757 -> .914, 92 memories, stress mean .071, health ok
- stress floor is unrealistic; smoke benchmark only
- see [长程演化-20261019.md](../docs/长程演化-20261019.md)

## ALFWorld

- `training/alfworld.py`: trajectory adapter + action-kind mapping
- real SFT: 3,000 steps; baseline: `python -m mindbase.training.alfworld_baseline`; test balanced .6361
- see [ALFWorld适配-20261019.md](../docs/ALFWorld适配-20261019.md)

## Embodiment interface

- `NoBodyAdapter` (usable), `HumanoidAdapter` (skeleton), `FlyGymAdapter` (skeleton)
- base defaults to no body
- ALFWorld HF download rate-limited; real trajectories pending
- see [Embodiment接口-20261019.md](../docs/Embodiment接口-20261019.md)

## Structured trajectories

- `training/structured.py`: JSONL/HDF5 -> StorySample
- supports observations/actions/rewards/dones
- 7 BEHAVIOR episodes; delta MLP 1.318 vs persistence 1.278; GRU 1.914 (worse)
- see [结构化轨迹导入-20261019.md](../docs/结构化轨迹导入-20261019.md)

## BEHAVIOR

- real human skill annotations: 145 episodes / 3,380 skill steps
- `training/behavior.py` -> StorySample
- raw skill labels: acc .3183 / balanced .6267 (no manual skill_kind mapping)
- see [BEHAVIOR技能轨迹-20261019.md](../docs/BEHAVIOR技能轨迹-20261019.md)

## Try demo

```bash
python -m mindbase.demo          # 直接输入
python -m mindbase.demo --script  # 脚本演示
```

or double-click `run_demo.bat`.
See [试用Demo-20261019.md](../docs/试用Demo-20261019.md).

## Learned decide

- `DecisionModel` replaces `_decide` heuristics via `heads={"decide": ...}`
- self-distilled on 1,000 BEHAVIOR events; holdout 1.0 (teacher labels, not human)
- see [学习决策头-20261019.md](../docs/学习决策头-20261019.md)

## Action advice

- `MindBase(heads={"action_advice": ...})`
- advice -> `prompt_state` / `snapshot`
- high-confidence advice (>= .30) shapes `Outcome.act.kind`
- `BehaviorAdvice` loads `behavior_policy.pt` and local Qwen3 embeddings
- policy shapes `Action.kind` only; no advice->speak/silence rule
- see [动作建议接入-20261019.md](../docs/动作建议接入-20261019.md)

## Policy stream

- 100 real BEHAVIOR events through `MindBase`
- advice accuracy .73; speak 79 / silence 21
- acts: approach 78 / withdraw 21 / explore 1
- see [policy事件流-20261019.md](../docs/policy事件流-20261019.md)

## Evaluation

    python -m mindbase.evaluation

- kc_sparsity ~5%
- weight bounds / homeostatic
- kc_separation (RSA proxy)
- channel_correlation (differential-readout trap; digital fly ~ -0.75)
- ood / ood_gap / auroc
- ablation (reward / punishment / mb / apl / cross)
- learning_ablation (no_learning / no_cross / no_apl / no_trace / no_admission)
- apl_calibration (strength sweep; default 0.5 near target reward 0.02)
- cross_seed_apl (seed/feedback stability; recommended band 0.25–0.5)
- speaker_ood (familiarity / trust gap)
- RSA / linear CKA
- behavior_entropy / ece

## Notes

- This is the base, not the character. No role language, no TTS, no persona prompt.
- Digital fly (connectome/whole-brain/embodiment) is the primary foundation; general Drosophila neuroscience is mechanism detail.
- Text -> V/A currently uses a tiny demo lexicon; replace with ANEW/NRC-VAD/ Japanese norms for calibration.
- The LLM layer should only verbalize prompt_state; it must not own emotion/memory/attention/will.
- `intention.py` + `will.py` model impulse-vs-intention arbitration; they do not claim free will or consciousness.
- `mb/` + `learner.py` are the Drosophila-first learning core; continual-learning papers only constrain stability/admission.
- `llm_gate.py` makes the base the controller: LLM is only called when `Outcome.llm_request` is not None.
- All modules are plain in -> out; failures should return neutral defaults, not raise into the caller.
