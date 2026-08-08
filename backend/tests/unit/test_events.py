"""Events dataset: offline build from the curated seed, pure dated loader,
provenance on every entry (source URL + fetch timestamp), zero network."""

import json

import pytest

from slackline.events import build_events
from slackline.events.dataset import EventsDataset, load_dataset


@pytest.fixture()
def artifact_path(tmp_path):
    path = tmp_path / "events.json"
    fetched_at = "2026-08-08T12:00:00+00:00"
    import datetime as dt

    events = build_events.expand_seed(dt.date(2026, 8, 10), 30, fetched_at)
    path.write_text(
        json.dumps({"build_date": "2026-08-08", "horizon_days": 30, "events": events}),
        encoding="utf-8",
    )
    return str(path)


def test_every_entry_has_provenance(artifact_path):
    raw = json.loads(open(artifact_path, encoding="utf-8").read())
    assert raw["events"], "seed expansion produced no events"
    for e in raw["events"]:
        assert e["source_url"].startswith("https://")
        assert e["fetched_at"]
        assert e["date"]


def test_candidates_for_date(artifact_path):
    ds = EventsDataset(artifact_path)
    assert ds.build_date == "2026-08-08"
    # 2026-08-15 is a Saturday: farmers markets + free museum day fire.
    saturday = ds.candidates_for("2026-08-15")
    assert saturday
    ids = {c.id for c in saturday}
    assert "ev-ferry-plaza-farmers-market-2026-08-15" in ids
    for cand in saturday:
        assert cand.source == "events"
        assert cand.source_url
        assert cand.fetched_at
        assert cand.open_windows is not None
    # No event leaks onto the wrong date.
    assert all("2026-08-15" in c.id for c in saturday)


def test_monthly_first_friday_only_fires_in_week_one(artifact_path):
    ds = EventsDataset(artifact_path)
    # 2026-08-14 and 2026-08-21 are Fridays but not the first of the month.
    for date in ("2026-08-14", "2026-08-21"):
        ids = {c.id for c in ds.candidates_for(date)}
        assert not any("first-friday" in i for i in ids)
    # 2026-09-04 is the first Friday of September.
    ids = {c.id for c in ds.candidates_for("2026-09-04")}
    assert any("oakland-first-fridays" in i for i in ids)


def test_missing_artifact_returns_none(tmp_path):
    assert load_dataset(str(tmp_path / "nope.json")) is None
