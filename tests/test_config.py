from datetime import time

import pytest

from app.config import load_api_keys, load_dev_frontend_mode, load_fetch_settings


def test_defaults_are_on_demand_only() -> None:
    settings = load_fetch_settings({})

    assert settings.on_demand_only is True
    assert (settings.peak_seconds, settings.offpeak_seconds) == (30, 0)
    assert settings.pause_windows == ((time(2, 30), time(5, 0)),)


def test_reads_values() -> None:
    settings = load_fetch_settings(
        {
            "BUS_FETCH_ON_DEMAND_ONLY": "0",
            "BUS_POLL_PEAK_SECONDS": "15",
            "BUS_POLL_OFFPEAK_SECONDS": "60",
            "BUS_POLL_PAUSE_WINDOWS": "1930-0450",
        }
    )

    assert settings.on_demand_only is False
    assert (settings.peak_seconds, settings.offpeak_seconds) == (15, 60)
    assert settings.pause_windows == ((time(19, 30), time(4, 50)),)


def test_peak_windows() -> None:
    settings = load_fetch_settings({"BUS_POLL_PEAK_WINDOWS": "0700-1000, 1600-2000"})

    assert settings.is_peak(time(8, 0))
    assert settings.is_peak(time(16, 0))
    assert not settings.is_peak(time(10, 0))
    assert not settings.is_peak(time(13, 0))


def test_pause_window_wraps_midnight() -> None:
    settings = load_fetch_settings({"BUS_POLL_PAUSE_WINDOWS": "1930-0450"})

    assert settings.is_paused(time(23, 0))
    assert settings.is_paused(time(4, 0))
    assert not settings.is_paused(time(12, 0))
    assert not settings.is_paused(time(4, 50))


@pytest.mark.parametrize(
    "key,value",
    [
        ("BUS_FETCH_ON_DEMAND_ONLY", "true"),
        ("BUS_POLL_PEAK_SECONDS", "-1"),
        ("BUS_POLL_OFFPEAK_SECONDS", "abc"),
        ("BUS_POLL_PEAK_WINDOWS", "0700"),
        ("BUS_POLL_PEAK_WINDOWS", "0700-2500"),
        ("BUS_POLL_PAUSE_WINDOWS", "930-1000"),
        ("BUS_POLL_PAUSE_WINDOWS", "0100-2460"),
    ],
)
def test_rejects_bad_values(key: str, value: str) -> None:
    with pytest.raises(ValueError):
        load_fetch_settings({key: value})


def test_api_keys_skip_blank_and_missing_in_order() -> None:
    keys = load_api_keys(
        {"DATA_GO_KR_KEY1": "a", "DATA_GO_KR_KEY2": "  ", "DATA_GO_KR_KEY4": "d"}
    )

    assert keys == ("a", "d")


def test_api_keys_empty_when_unset() -> None:
    assert load_api_keys({}) == ()


def test_dev_frontend_mode_defaults_to_off_and_rejects_other_values() -> None:
    assert load_dev_frontend_mode({}) is False
    assert load_dev_frontend_mode({"DEV_FRONTEND_MODE": "1"}) is True
    with pytest.raises(ValueError):
        load_dev_frontend_mode({"DEV_FRONTEND_MODE": "yes"})
