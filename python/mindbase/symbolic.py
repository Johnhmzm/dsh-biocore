"""Symbolic expression evolution.

If mathematics itself must not be restricted to hand-picked equations, the
formula structure has to be searchable too.  This is a small, safe symbolic
regression layer: expressions are trees over +, -, *, /, sin, cos, exp, tanh,
square and constants; mutation/crossover search minimizes MSE + complexity.
"""
from __future__ import annotations

import math
import random
from typing import Callable, Dict, List, Sequence, Tuple

BINARY = ("+", "-", "*", "/")
UNARY = ("sin", "cos", "exp", "tanh", "square")


def _rand_const(rng: random.Random) -> float:
    return round(rng.uniform(-2.0, 2.0), 3)


def random_expr(rng: random.Random, variables: Sequence[str], depth: int = 3):
    if depth <= 0 or rng.random() < 0.30:
        if rng.random() < 0.55:
            return rng.choice(list(variables))
        return _rand_const(rng)
    if rng.random() < 0.30:
        return (rng.choice(UNARY), random_expr(rng, variables, depth - 1))
    return (rng.choice(BINARY), random_expr(rng, variables, depth - 1),
            random_expr(rng, variables, depth - 1))


def size(expr) -> int:
    if isinstance(expr, (tuple, list)):
        return 1 + sum(size(a) for a in expr[1:])
    return 1


def eval_expr(expr, env: Dict[str, float]) -> float:
    if isinstance(expr, str):
        return float(env.get(expr, 0.0))
    if isinstance(expr, (int, float)):
        return float(expr)
    if isinstance(expr, list):
        expr = tuple(expr)
    op, args = expr[0], expr[1:]
    vals = [eval_expr(a, env) for a in args]
    if op == "+":
        return vals[0] + vals[1]
    if op == "-":
        return vals[0] - vals[1]
    if op == "*":
        return vals[0] * vals[1]
    if op == "/":
        return vals[0] / (vals[1] if abs(vals[1]) > 1e-9 else 1e-9)
    if op == "sin":
        return math.sin(vals[0])
    if op == "cos":
        return math.cos(vals[0])
    if op == "exp":
        return math.exp(max(-20.0, min(20.0, vals[0])))
    if op == "tanh":
        return math.tanh(vals[0])
    if op == "square":
        return vals[0] * vals[0]
    return 0.0


def _replace_subtree(expr, path: List[int], new):
    if not path:
        return new
    op = expr[0]
    args = list(expr[1:])
    idx = path[0]
    args[idx] = _replace_subtree(args[idx], path[1:], new)
    return (op,) + tuple(args)


def _paths(expr, prefix=()):
    out = [prefix]
    if isinstance(expr, tuple):
        for i, a in enumerate(expr[1:]):
            out.extend(_paths(a, prefix + (i,)))
    return out


def mutate(expr, rng: random.Random, variables: Sequence[str]):
    paths = _paths(expr)
    path = rng.choice(paths[1:]) if len(paths) > 1 else ()
    new = random_expr(rng, variables, depth=rng.randint(1, 3))
    return _replace_subtree(expr, list(path), new)


def crossover(a, b, rng: random.Random):
    pa = _paths(a)
    path = rng.choice(pa[1:]) if len(pa) > 1 else ()
    return _replace_subtree(a, list(path), b)


def fit(data: Sequence[Tuple[Dict[str, float], float]], variables: Sequence[str],
        generations: int = 25, population: int = 40, seed: int = 1) -> Dict[str, object]:
    rng = random.Random(int(seed))
    if not data:
        return {"expr": None, "mse": 0.0, "size": 0}
    pool = [random_expr(rng, variables, depth=3) for _ in range(max(4, population))]

    def score(e):
        err = 0.0
        for env, y in data:
            try:
                err += (eval_expr(e, env) - float(y)) ** 2
            except Exception:
                err += 1e6
        return err / len(data) + 0.002 * size(e)

    best, best_score = pool[0], score(pool[0])
    for _ in range(max(1, generations)):
        scored = sorted(((score(e), e) for e in pool), key=lambda x: x[0])
        if scored[0][0] < best_score:
            best_score, best = scored[0]
        elite = [e for _, e in scored[:max(2, population // 5)]]
        pool = list(elite)
        while len(pool) < population:
            if rng.random() < 0.7:
                pool.append(mutate(rng.choice(elite), rng, variables))
            else:
                pool.append(crossover(rng.choice(elite), rng.choice(elite), rng))
    return {"expr": best, "mse": round(best_score, 6), "size": size(best)}


def expression_text(expr) -> str:
    if isinstance(expr, str):
        return expr
    if isinstance(expr, (int, float)):
        return str(expr)
    op, args = expr[0], expr[1:]
    if op in UNARY:
        return f"{op}({expression_text(args[0])})"
    return f"({expression_text(args[0])} {op} {expression_text(args[1])})"


def formula_stability(expr, states: Sequence[Dict[str, float]] | None = None) -> float:
    """Fraction of representative states where the expression is finite and bounded."""
    if expr is None:
        return 1.0
    if not states:
        states = [
            {"energy": 0.2, "social": 0.2, "stress": 0.8, "hunger": 0.8, "thirst": 0.7,
             "pain": 0.5, "inflammation": 0.5, "sleep_pressure": 0.8,
             "known_food": 0.0, "food_uncertainty": 1.0},
            {"energy": 0.9, "social": 0.8, "stress": 0.0, "hunger": 0.1, "thirst": 0.1,
             "pain": 0.0, "inflammation": 0.0, "sleep_pressure": 0.1,
             "known_food": 0.8, "food_uncertainty": 0.2},
            {"energy": 0.5, "social": 0.5, "stress": 0.3, "hunger": 0.5, "thirst": 0.4,
             "pain": 0.2, "inflammation": 0.2, "sleep_pressure": 0.4,
             "known_food": 0.3, "food_uncertainty": 0.7},
        ]
    good = 0
    for env in states:
        try:
            v = eval_expr(expr, env)
            if math.isfinite(v) and abs(v) <= 10.0:
                good += 1
        except Exception:
            pass
    return good / max(1, len(states))


def formula_complexity(expr) -> int:
    return size(expr)
