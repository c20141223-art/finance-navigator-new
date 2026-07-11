from stock_screener.config import load_config


def test_load_config_defaults():
    config = load_config()
    assert config.http.max_retries >= 1
    assert config.data.min_backfill_trading_days == 90
    assert config.url("twse_daily_all").startswith("https://")


def test_url_missing_key_raises():
    config = load_config()
    try:
        config.url("does_not_exist")
        assert False, "expected KeyError"
    except KeyError:
        pass
