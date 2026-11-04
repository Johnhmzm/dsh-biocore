"""Neuron models as explicit formulas.

LIF / Izhikevich / Hodgkin-Huxley / rate neuron.
These are mathematical models, not biological truth.
"""
from __future__ import annotations

import math
from typing import Dict


class LIFNeuron:
    def __init__(self, tau_m: float = 20.0, v_rest: float = -65.0,
                 v_reset: float = -70.0, v_th: float = -50.0,
                 r_m: float = 10.0, refractory_ms: float = 2.0):
        self.tau_m = float(tau_m)
        self.v_rest = float(v_rest)
        self.v_reset = float(v_reset)
        self.v_th = float(v_th)
        self.r_m = float(r_m)
        self.refractory_ms = float(refractory_ms)
        self.v = self.v_rest
        self.refractory = 0.0
        self.spikes = 0

    def step(self, dt: float, current: float = 0.0) -> bool:
        if self.refractory > 0.0:
            self.refractory = max(0.0, self.refractory - dt)
            self.v = self.v_reset
            return False
        dv = (-(self.v - self.v_rest) + self.r_m * float(current)) / self.tau_m
        self.v += dt * dv
        if self.v >= self.v_th:
            self.v = self.v_reset
            self.refractory = self.refractory_ms
            self.spikes += 1
            return True
        return False


class IzhikevichNeuron:
    def __init__(self, a: float = 0.02, b: float = 0.2, c: float = -65.0, d: float = 8.0):
        self.a, self.b, self.c, self.d = float(a), float(b), float(c), float(d)
        self.v = self.c
        self.u = self.b * self.c
        self.spikes = 0

    def step(self, dt: float, current: float = 0.0) -> bool:
        dv = 0.04 * self.v * self.v + 5.0 * self.v + 140.0 - self.u + float(current)
        du = self.a * (self.b * self.v - self.u)
        self.v += dt * dv
        self.u += dt * du
        if self.v >= 30.0:
            self.v = self.c
            self.u += self.d
            self.spikes += 1
            return True
        return False


class HHNeuron:
    """Hodgkin-Huxley with standard gating functions."""

    def __init__(self, c_m: float = 1.0, g_na: float = 120.0, g_k: float = 36.0,
                 g_l: float = 0.3, e_na: float = 50.0, e_k: float = -77.0,
                 e_l: float = -54.387, v0: float = -65.0):
        self.c_m = float(c_m)
        self.g_na, self.g_k, self.g_l = float(g_na), float(g_k), float(g_l)
        self.e_na, self.e_k, self.e_l = float(e_na), float(e_k), float(e_l)
        self.v = float(v0)
        self.m = self._am(self.v) / (self._am(self.v) + self._bm(self.v))
        self.h = self._ah(self.v) / (self._ah(self.v) + self._bh(self.v))
        self.n = self._an(self.v) / (self._an(self.v) + self._bn(self.v))
        self.spikes = 0

    @staticmethod
    def _am(v: float) -> float:
        x = (v + 40.0) / 10.0
        return 0.1 * (1.0 if abs(x) < 1e-9 else x / (1.0 - math.exp(-x)))

    @staticmethod
    def _bm(v: float) -> float:
        return 4.0 * math.exp(-(v + 65.0) / 18.0)

    @staticmethod
    def _ah(v: float) -> float:
        return 0.07 * math.exp(-(v + 65.0) / 20.0)

    @staticmethod
    def _bh(v: float) -> float:
        return 1.0 / (1.0 + math.exp(-(v + 35.0) / 10.0))

    @staticmethod
    def _an(v: float) -> float:
        x = (v + 55.0) / 10.0
        return 0.01 * (1.0 if abs(x) < 1e-9 else x / (1.0 - math.exp(-x)))

    @staticmethod
    def _bn(v: float) -> float:
        return 0.125 * math.exp(-(v + 65.0) / 80.0)

    def step(self, dt: float, current: float = 0.0) -> bool:
        v = self.v
        i_na = self.g_na * (self.m ** 3) * self.h * (v - self.e_na)
        i_k = self.g_k * (self.n ** 4) * (v - self.e_k)
        i_l = self.g_l * (v - self.e_l)
        self.v += dt * (float(current) - i_na - i_k - i_l) / self.c_m
        self.m += dt * (self._am(v) * (1.0 - self.m) - self._bm(v) * self.m)
        self.h += dt * (self._ah(v) * (1.0 - self.h) - self._bh(v) * self.h)
        self.n += dt * (self._an(v) * (1.0 - self.n) - self._bn(v) * self.n)
        self.m = max(0.0, min(1.0, self.m))
        self.h = max(0.0, min(1.0, self.h))
        self.n = max(0.0, min(1.0, self.n))
        if self.v >= 30.0:
            self.v = -65.0
            self.spikes += 1
            return True
        return False


class RateNeuron:
    def __init__(self, tau: float = 10.0, slope: float = 1.0, threshold: float = 0.0):
        self.tau = float(tau)
        self.slope = float(slope)
        self.threshold = float(threshold)
        self.x = 0.0

    def step(self, dt: float, current: float = 0.0) -> float:
        target = 1.0 / (1.0 + math.exp(-self.slope * (float(current) - self.threshold)))
        self.x += dt * (-(self.x - target) / self.tau)
        return self.x

    def snapshot(self) -> Dict[str, float]:
        return {"x": round(self.x, 5)}
