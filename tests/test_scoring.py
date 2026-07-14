import pytest

from stock_screener.scoring import (
    DimensionSpec,
    FactorSpec,
    load_momentum_config,
    score_bins,
    score_dimension,
)

BINS_GT = [{"gt": 30, "score": 100}, {"gt": 10, "score": 60}, {"gt": 0, "score": 30}, {"else": 0}]
BINS_RANGE = [{"range": [0, 8], "score": 100}, {"range": [8, 20], "score": 60},
              {"range": [20, 25], "score": 30}, {"else": 0}]
BINS_GTE = [{"gte": 10, "score": 100}, {"gte": 5, "score": 70}, {"gte": 3, "score": 40}, {"else": 0}]


def test_gt_bins_first_match_wins():
    assert score_bins(31, BINS_GT) == 100
    assert score_bins(30, BINS_GT) == 60      # gt is strict
    assert score_bins(10.5, BINS_GT) == 60
    assert score_bins(0.1, BINS_GT) == 30
    assert score_bins(0, BINS_GT) == 0
    assert score_bins(-5, BINS_GT) == 0


def test_gte_bins_boundaries():
    assert score_bins(10, BINS_GTE) == 100
    assert score_bins(9, BINS_GTE) == 70
    assert score_bins(5, BINS_GTE) == 70
    assert score_bins(3, BINS_GTE) == 40
    assert score_bins(2, BINS_GTE) == 0


def test_range_bins_inclusive_exclusive():
    assert score_bins(0, BINS_RANGE) == 100    # lower bound inclusive
    assert score_bins(7.99, BINS_RANGE) == 100
    assert score_bins(8, BINS_RANGE) == 60     # upper bound exclusive
    assert score_bins(20, BINS_RANGE) == 30
    assert score_bins(25, BINS_RANGE) == 0     # > 25 → else
    assert score_bins(-0.01, BINS_RANGE) == 0  # < 0 → else


def test_none_scores_zero():
    assert score_bins(None, BINS_GT) == 0


def test_dimension_capping():
    dim = DimensionSpec(name="chips", weight=0.35, factors=[
        FactorSpec(name="a", bins=[{"gt": 0, "score": 100}, {"else": 0}], score_if_true=None),
        FactorSpec(name="b", bins=[{"gt": 0, "score": 100}, {"else": 0}], score_if_true=None),
    ])
    capped, detail = score_dimension(dim, {"a": 1, "b": 1})
    assert capped == 100                       # 100 + 100 → capped at 100
    assert detail["a"]["score"] == 100 and detail["b"]["score"] == 100

    partial, _ = score_dimension(dim, {"a": 1, "b": -1})
    assert partial == 100                      # single factor already at cap

def test_boolean_factor():
    dim = DimensionSpec(name="fundamental", weight=0.3, factors=[
        FactorSpec(name="trend", bins=None, score_if_true=100),
    ])
    assert score_dimension(dim, {"trend": True})[0] == 100
    assert score_dimension(dim, {"trend": False})[0] == 0
    assert score_dimension(dim, {"trend": None})[0] == 0


def test_load_momentum_config_shape():
    mcfg = load_momentum_config()
    assert mcfg.top_n == 30
    assert mcfg.control_group_rank == (31, 50)
    assert {d.name for d in mcfg.dimensions} == {"fundamental", "chips", "technical"}
    assert abs(sum(d.weight for d in mcfg.dimensions) - 1.0) < 1e-9
    assert len(mcfg.version_hash) == 12
