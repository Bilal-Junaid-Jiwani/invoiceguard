"""Agency-mode slice #9: per-freelancer payout schedules.

Slices #4-#8 recorded what the agency paid and what it should pay
next; this slice answers *when* it pays: each roster freelancer gets
a recurring schedule — weekly on an ISO weekday, or monthly on a day
of month — and `reports.payout_schedule()` / `payout schedule` derive
the next payout date from it. Bookkeeping only: a schedule never
records or transfers a payout.
"""

from __future__ import annotations

import os
import sqlite3

import pytest
from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.db import DB, migrate_freelancers_payout_schedule
from invoiceguard.reports import next_payout_date, payout_schedule


# -- DB-level ----------------------------------------------------------

def test_add_freelancer_with_schedule(db):
    fid = db.add_freelancer("Ali R.", payout_cadence="monthly",
                            payout_day=15)
    row = db.get_freelancer(fid)
    assert row["payout_cadence"] == "monthly"
    assert row["payout_day"] == 15


def test_add_freelancer_without_schedule_stays_null(db):
    fid = db.add_freelancer("Ali R.")
    row = db.get_freelancer(fid)
    assert row["payout_cadence"] is None
    assert row["payout_day"] is None


@pytest.mark.parametrize("cadence,day", [
    ("daily", 1), ("fortnightly", 15), ("Weekly", 1), ("", 1),
    ("weekly", 0), ("weekly", 8), ("weekly", None),
    ("monthly", 0), ("monthly", 32), ("monthly", None),
    (None, 15),
])
def test_add_freelancer_rejects_bad_schedule(db, cadence, day):
    with pytest.raises(ValueError):
        db.add_freelancer("Ali R.", payout_cadence=cadence,
                          payout_day=day)


def test_set_and_clear_schedule(db):
    fid = db.add_freelancer("Ali R.")
    db.set_freelancer_schedule(fid, "weekly", 5)
    row = db.get_freelancer(fid)
    assert (row["payout_cadence"], row["payout_day"]) == ("weekly", 5)
    db.set_freelancer_schedule(fid, None, None)
    row = db.get_freelancer(fid)
    assert row["payout_cadence"] is None
    assert row["payout_day"] is None


def test_set_schedule_unknown_and_bad(db):
    with pytest.raises(ValueError, match="no freelancer #999"):
        db.set_freelancer_schedule(999, "weekly", 1)
    fid = db.add_freelancer("Ali R.")
    with pytest.raises(ValueError):
        db.set_freelancer_schedule(fid, "weekly", 8)
    # failed write leaves the old value in place
    assert db.get_freelancer(fid)["payout_cadence"] is None


def test_migration_adds_schedule_columns_to_old_db(home):
    """A pre-v0.15.0 database gets the schedule columns on connect."""
    path = os.environ["INVOICEGUARD_DB"]
    conn = sqlite3.connect(path)
    # A faithful pre-v0.15.0 freelancers table: every column EXCEPT
    # the schedule columns.
    conn.execute(
        "CREATE TABLE freelancers(id INTEGER PRIMARY KEY, "
        "name TEXT NOT NULL, email TEXT, phone TEXT, split_pct REAL, "
        "created_at TEXT NOT NULL)")
    conn.commit()
    migrate_freelancers_payout_schedule(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(freelancers)")}
    conn.close()
    assert "payout_cadence" in cols
    assert "payout_day" in cols
    # idempotent: reopening via DB does not lose data
    d = DB(str(path))
    fid = d.add_freelancer("Ali R.", payout_cadence="monthly",
                           payout_day=1)
    assert d.get_freelancer(fid)["payout_day"] == 1
    d.close()


# -- next-payout-date derivation --------------------------------------

def test_next_weekly_same_day_is_today():
    # 2026-10-28 is a Wednesday (ISO weekday 3).
    assert (next_payout_date("weekly", 3, as_of="2026-10-28")
            .isoformat()) == "2026-10-28"


def test_next_weekly_rolls_forward():
    # Friday after Wed 2026-10-28 is 2026-10-30; Monday is 2026-11-02.
    assert (next_payout_date("weekly", 5, as_of="2026-10-28")
            .isoformat()) == "2026-10-30"
    assert (next_payout_date("weekly", 1, as_of="2026-10-28")
            .isoformat()) == "2026-11-02"


def test_next_monthly_same_month_and_rollover():
    assert (next_payout_date("monthly", 15, as_of="2026-10-28")
            .isoformat()) == "2026-11-15"
    assert (next_payout_date("monthly", 28, as_of="2026-10-28")
            .isoformat()) == "2026-10-28"
    assert (next_payout_date("monthly", 1, as_of="2026-12-15")
            .isoformat()) == "2027-01-01"


def test_next_monthly_clamps_to_month_end():
    # Day 31 in February clamps to Feb 28 (2026 is not a leap year)
    # and to Feb 29 in a leap year.
    assert (next_payout_date("monthly", 31, as_of="2026-02-01")
            .isoformat()) == "2026-02-28"
    assert (next_payout_date("monthly", 31, as_of="2028-02-01")
            .isoformat()) == "2028-02-29"


def test_next_payout_date_none_without_schedule():
    assert next_payout_date(None, None, as_of="2026-10-28") is None


def test_next_payout_date_rejects_bad_schedule():
    with pytest.raises(ValueError):
        next_payout_date("weekly", 8, as_of="2026-10-28")


# -- reports rows ------------------------------------------------------

def test_schedule_rows_sorted_earliest_first_unscheduled_last(db):
    unscheduled = db.add_freelancer("Zed U.")
    early = db.add_freelancer("Ali R.", payout_cadence="weekly",
                              payout_day=5)  # Fri 2026-10-30
    late = db.add_freelancer("Sana K.", payout_cadence="monthly",
                             payout_day=15)  # 2026-11-15
    rows = payout_schedule(db, as_of="2026-10-28")
    assert [r["freelancer_id"] for r in rows] == [early, late, unscheduled]
    assert rows[0]["next_payout_date"] == "2026-10-30"
    assert rows[1]["next_payout_date"] == "2026-11-15"
    assert rows[2]["next_payout_date"] is None
    assert rows[2]["payout_cadence"] is None


def test_schedule_rows_single_freelancer_and_unknown(db):
    fid = db.add_freelancer("Ali R.", payout_cadence="monthly",
                            payout_day=1)
    (row,) = payout_schedule(db, fid, as_of="2026-10-28")
    assert row["next_payout_date"] == "2026-11-01"
    with pytest.raises(ValueError, match="no freelancer #999"):
        payout_schedule(db, 999, as_of="2026-10-28")


# -- CLI ---------------------------------------------------------------

def test_cli_set_and_clear_schedule(db):
    r = CliRunner().invoke(cli, ["freelancer", "add", "--name", "Ali R."])
    assert r.exit_code == 0, r.output
    r = CliRunner().invoke(cli, ["freelancer", "set-schedule", "1",
                                 "--cadence", "monthly", "--day", "15"])
    assert r.exit_code == 0, r.output
    assert "monthly on day 15" in r.output
    r = CliRunner().invoke(cli, ["freelancer", "show", "1"])
    assert r.exit_code == 0, r.output
    assert "schedule: monthly on day 15" in r.output
    r = CliRunner().invoke(cli, ["freelancer", "clear-schedule", "1"])
    assert r.exit_code == 0, r.output
    assert "(cleared)" in r.output


def test_cli_set_schedule_rejects_bad_day_and_unknown(db):
    CliRunner().invoke(cli, ["freelancer", "add", "--name", "Ali R."])
    r = CliRunner().invoke(cli, ["freelancer", "set-schedule", "1",
                                 "--cadence", "weekly", "--day", "8"])
    assert r.exit_code != 0
    assert "between 1 and 7" in r.output
    r = CliRunner().invoke(cli, ["freelancer", "set-schedule", "999",
                                 "--cadence", "weekly", "--day", "1"])
    assert r.exit_code != 0
    assert "no freelancer #999" in r.output


def test_cli_payout_schedule(db):
    CliRunner().invoke(cli, ["freelancer", "add", "--name", "Ali R."])
    CliRunner().invoke(cli, ["freelancer", "add", "--name", "Sana K."])
    CliRunner().invoke(cli, ["freelancer", "set-schedule", "1",
                             "--cadence", "weekly", "--day", "5"])
    r = CliRunner().invoke(cli, ["payout", "schedule",
                                 "--as-of", "2026-10-28"])
    assert r.exit_code == 0, r.output
    assert "weekly on Friday" in r.output
    assert "next payout 2026-10-30" in r.output
    assert "no schedule set" in r.output


def test_cli_payout_schedule_empty_roster(db):
    r = CliRunner().invoke(cli, ["payout", "schedule"])
    assert r.exit_code == 0, r.output
    assert "no freelancers on the roster" in r.output


def test_cli_payout_schedule_unknown_freelancer(db):
    r = CliRunner().invoke(cli, ["payout", "schedule",
                                 "--freelancer", "999"])
    assert r.exit_code != 0
    assert "no freelancer #999" in r.output
