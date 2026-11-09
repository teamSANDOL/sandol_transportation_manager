from app.normalize import normalize, route_name
from tests.conftest import load_fixture

EXCLUDED = {"22", "23", "55", "99", "123", "125"}


def rows(station: str) -> list[dict]:
    return load_fixture(f"gbis_list_{station}.json")["response"]["msgBody"][
        "busArrivalList"
    ]


def allowed(station: str) -> dict[str, str]:
    return {
        str(r["routeId"]): route_name(r["routeName"])
        for r in rows(station)
        if route_name(r["routeName"]) not in EXCLUDED
    }


def test_route_name_is_always_text_without_suffix() -> None:
    assert route_name(3400) == "3400"
    assert route_name("P9540(출근)") == "P9540"
    assert route_name("11-A") == "11-A"


def test_drops_routes_outside_the_allow_list() -> None:
    arrivals = normalize(rows("25173"), allowed("25173"))

    assert not {a.route for a in arrivals} & EXCLUDED
    assert {"3400", "P9540", "P9541", "11-B"} <= {a.route for a in arrivals}


def test_empty_prediction_is_state_none_without_buses() -> None:
    arrivals = normalize(rows("25835"), allowed("25835"))

    empty = [a for a in arrivals if not a.buses]
    assert empty and all(a.state in ("none", "wait") for a in empty)


def test_running_bus_has_prediction_and_plate() -> None:
    arrivals = normalize(rows("25835"), allowed("25835"))

    running = [a for a in arrivals if a.state == "running"]
    assert running
    bus = running[0].buses[0]
    assert bus.plate_no and (bus.predict_sec or bus.predict_minutes) is not None


def test_same_route_twice_keeps_both_occurrences() -> None:
    arrivals = normalize(rows("25835"), allowed("25835"))

    orders = sorted(a.sta_order for a in arrivals if a.route == "28")
    assert len(orders) == 2


def test_row_count_may_exceed_route_count() -> None:
    arrivals = normalize(rows("25835"), allowed("25835"))

    assert len(arrivals) == 14
    assert len({a.route for a in arrivals}) == 9


def test_sadang_stop_keeps_only_our_routes_and_drops_other_buses() -> None:
    allowed = {"233000367": "P9541", "224000061": "6502", "224000050": "3400"}

    arrivals = normalize(rows("22851"), allowed)

    assert sorted(a.route for a in arrivals) == ["3400", "6502", "P9541"]


def test_city_hall_stop_keeps_only_our_routes() -> None:
    allowed = {"216000047": "5602", "224000071": "3401", "224000062": "33"}

    arrivals = normalize(rows("25484"), allowed)

    assert sorted(a.route for a in arrivals) == ["33", "3401", "5602"]
