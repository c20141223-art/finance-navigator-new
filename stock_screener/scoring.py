"""Config-driven scoring primitives for the momentum profile.

Everything numeric lives in config/momentum.yaml; this module only knows
how to read that structure and turn a raw factor value into a score. The
bin evaluator is deliberately first-match-wins over an ordered list so a
human can replay any score by reading the YAML top-to-bottom — auditability
over cleverness (spec principle 2).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_MOMENTUM_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "momentum.yaml"
)

DIMENSION_MAX_SCORE = 100.0  # 維度滿分封頂（規格書 3.2）


@dataclass(frozen=True)
class FactorSpec:
    name: str
    bins: list[dict] | None          # ordered; see score_bins
    score_if_true: float | None      # boolean factors


@dataclass(frozen=True)
class DimensionSpec:
    name: str
    weight: float
    factors: list[FactorSpec]


@dataclass(frozen=True)
class MomentumConfig:
    filters: dict
    dimensions: list[DimensionSpec]
    top_n: int
    control_group_rank: tuple[int, int]
    version_hash: str
    raw: dict


def load_momentum_config(path: Path | str = DEFAULT_MOMENTUM_CONFIG_PATH) -> MomentumConfig:
    path = Path(path)
    content = path.read_bytes()
    raw = yaml.safe_load(content)

    dimensions = []
    for dim_name, dim_cfg in raw["dimensions"].items():
        factors = []
        for factor_name, factor_cfg in dim_cfg["factors"].items():
            factors.append(FactorSpec(
                name=factor_name,
                bins=factor_cfg.get("bins"),
                score_if_true=factor_cfg.get("score_if_true"),
            ))
        dimensions.append(DimensionSpec(
            name=dim_name, weight=float(dim_cfg["weight"]), factors=factors,
        ))

    weight_sum = sum(d.weight for d in dimensions)
    if abs(weight_sum - 1.0) > 1e-9:
        raise ValueError(f"維度權重總和必須為 1.0，目前為 {weight_sum}")

    lo, hi = raw["output"]["control_group_rank"]
    return MomentumConfig(
        filters=dict(raw["filters"]),
        dimensions=dimensions,
        top_n=int(raw["output"]["top_n"]),
        control_group_rank=(int(lo), int(hi)),
        version_hash=hashlib.sha256(content).hexdigest()[:12],
        raw=raw,
    )


def score_bins(value: float | None, bins: list[dict]) -> float:
    """First-match-wins over the ordered bin list. None scores 0 (spec 1.4:
    missing-data factors score zero). Supported conditions per bin:
    gt / gte / range ([lower, upper), inclusive-exclusive) / else."""
    if value is None:
        return 0.0
    for b in bins:
        if "gt" in b and value > b["gt"]:
            return float(b["score"])
        if "gte" in b and value >= b["gte"]:
            return float(b["score"])
        if "range" in b:
            lower, upper = b["range"]
            if lower <= value < upper:
                return float(b["score"])
        if "else" in b:
            return float(b["else"])
    return 0.0


def score_factor(spec: FactorSpec, raw_value) -> float:
    if spec.bins is not None:
        return score_bins(raw_value, spec.bins)
    if spec.score_if_true is not None:
        return float(spec.score_if_true) if raw_value else 0.0
    raise ValueError(f"因子 {spec.name} 未定義 bins 或 score_if_true")


def score_dimension(spec: DimensionSpec, raw_values: dict) -> tuple[float, dict]:
    """Returns (capped dimension score, per-factor detail). Factor scores
    are summed then capped at DIMENSION_MAX_SCORE (維度內封頂, spec 3.2)."""
    detail = {}
    total = 0.0
    for factor in spec.factors:
        raw = raw_values.get(factor.name)
        score = score_factor(factor, raw)
        detail[factor.name] = {"raw": raw, "score": score}
        total += score
    return min(total, DIMENSION_MAX_SCORE), detail
