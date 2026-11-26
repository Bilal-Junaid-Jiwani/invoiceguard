"""Agency-mode slice #37: CSV export for `client list` and `project list`.

The agency slices #32–#36 put CSV exports on every agency view
(freelancer list/show, payout list/suggest/schedule/due/overdue,
agency summary, invoice report, invoice list) — but the two core
rosters themselves were still text-only: the client list (with its
per-client dunning-policy overrides) and the project list (with its
contract-acknowledgment state) still had to be retyped from the text
lines. This slice adds `--csv PATH` (or `-` for stdout) to both,
writing the same lists as spreadsheets through two shared library
calls, `reports.client_list_csv()` and `reports.project_list_csv()`.
No new library rules: the rows are the same `db.list_clients()` /
`db.list_projects()` rows the text views print, and neither list
carries an `--as-of`, so — like the invoice list CSV — there are no
payout timing columns here. Read-only.
"""

from __future__ import annotations

import csv
import io

from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.contracts import render_contract
from invoiceguard.reports import (CLIENT_LIST_CSV_COLUMNS,
                                  PROJECT_LIST_CSV_COLUMNS,
                                  client_list_csv, project_list_csv)


def _client_csv_rows(*extra):
    res = CliRunner().invoke(cli, ["client", "list", *extra,
                                   "--csv", "-"])
    assert res.exit_code == 0, res.output
    return list(csv.reader(io.StringIO(res.output)))


def _project_csv_rows(*extra):
    res = CliRunner().invoke(cli, ["project", "list", *extra,
                                   "--csv", "-"])
    assert res.exit_code == 0, res.output
    return list(csv.reader(io.StringIO(res.output)))


def _add_project(db, client_id, title, cents, currency="USD"):
    contract = render_contract(
        "Acme Corp", "billing@acme.test", title, cents, currency,
        50.0, 1.5, 15,
    )
    pid = db.add_project(client_id, title, cents, currency,
                         50.0, 1.5, 15, contract)
    return db.get_project(pid)


# -- client list -------------------------------------------------------

def test_client_list_csv_header_and_columns(db, sample_client):
    rows = _client_csv_rows()
    assert rows[0] == CLIENT_LIST_CSV_COLUMNS
    assert len(rows) == 2  # header + one client row
    client = rows[1]
    assert client[0] == "1"  # client id
    assert client[1] == "Acme Corp"  # name
    assert client[2] == "billing@acme.test"  # email
    assert client[3] == ""  # no phone on file
    # No dunning overrides: all three cells empty, exactly like the
    # text lines omitting the `[dunning ...]` marker.
    assert client[4:] == ["", "", ""]


def test_client_list_csv_multiple_rows_in_id_order(db):
    db.add_client("Acme Corp", "billing@acme.test")
    db.add_client("Beta LLC", "ops@beta.test", "+15551234567")
    db.add_client("Gamma Inc")  # no email, no phone
    rows = _client_csv_rows()
    assert len(rows) == 4  # header + three client rows
    # Same id order the text lines print them.
    assert [r[0] for r in rows[1:]] == ["1", "2", "3"]
    assert rows[1][1] == "Acme Corp"
    assert rows[2][1] == "Beta LLC"
    # A `+`-leading phone is neutralized like any formula-looking
    # cell, exactly as the other CSV exports do.
    assert rows[2][3] == "'+15551234567"
    assert rows[3][1] == "Gamma Inc"
    assert rows[3][2] == "" and rows[3][3] == ""


def test_client_list_csv_dunning_overrides(db, sample_client):
    # Custom day1, disabled day7, day15 left at the global default.
    db.set_client_dunning_policy(sample_client["id"], day1=3, day7=0)
    db.add_client("Beta LLC", "ops@beta.test")
    rows = _client_csv_rows()
    assert len(rows) == 3
    overridden = rows[1]
    assert overridden[4] == "3"  # custom threshold
    assert overridden[5] == "off"  # disabled stage
    assert overridden[6] == ""  # global default: no override
    # The untouched client carries empty cells, like the text lines
    # omitting the marker.
    assert rows[2][4:] == ["", "", ""]


def test_client_list_csv_no_clients_header_only(db):
    rows = _client_csv_rows()
    assert rows == [CLIENT_LIST_CSV_COLUMNS]


def test_client_list_csv_file_path_writes_file_and_text_view(
        db, sample_client, tmp_path):
    db.set_client_dunning_policy(sample_client["id"], day1=3)
    path = tmp_path / "client-list.csv"
    res = CliRunner().invoke(cli, ["client", "list",
                                   "--csv", str(path)])
    assert res.exit_code == 0, res.output
    assert "#  1" in res.output  # text view still prints
    assert "[dunning day1=3]" in res.output  # marker still prints
    assert f"wrote client list CSV: {path} (1 rows)" in res.output
    rows = list(csv.reader(io.StringIO(path.read_text())))
    assert rows[0] == CLIENT_LIST_CSV_COLUMNS
    assert rows[1][1] == "Acme Corp" and rows[1][4] == "3"


def test_client_list_csv_stdout_replaces_text_view(db, sample_client):
    res = CliRunner().invoke(cli, ["client", "list", "--csv", "-"])
    assert res.exit_code == 0, res.output
    assert res.output.startswith(
        "client_id,client,email,phone,")
    assert "#  1" not in res.output  # no text view


def test_client_list_csv_bare_library_call(db):
    # A bare call needs no timing map — the list carries no `--as-of`
    # — and formula-looking client names are neutralized on the
    # free-text cells.
    db.add_client("=Evil", "evil@acme.test")
    rows = db.list_clients()
    bare = list(csv.reader(io.StringIO(client_list_csv(rows))))
    assert bare[0] == CLIENT_LIST_CSV_COLUMNS
    assert len(bare) == 2  # header + the one client row
    assert bare[1][0] == "1"
    assert bare[1][1] == "'=Evil"  # formula injection neutralized


# -- project list ------------------------------------------------------

def test_project_list_csv_header_and_money_columns(
        db, sample_client, sample_project):
    rows = _project_csv_rows()
    assert rows[0] == PROJECT_LIST_CSV_COLUMNS
    assert len(rows) == 2  # header + one project row
    proj = rows[1]
    assert proj[0] == "1"  # project id
    assert proj[1] == "Website redesign"  # title
    assert proj[2] == "Acme Corp"  # client
    assert proj[3] == "2000.00"  # amount
    assert proj[4] == "USD"  # currency
    assert proj[5] == "no-ack"  # contract not acknowledged


def test_project_list_csv_multiple_rows_in_id_order_and_ack(
        db, sample_client, sample_project):
    second = _add_project(db, sample_client["id"], "Logo refresh",
                          50_000)
    db.ack_contract(second["id"])
    rows = _project_csv_rows()
    assert len(rows) == 3  # header + two project rows
    # Same id order the text lines print them.
    assert [r[0] for r in rows[1:]] == ["1", "2"]
    assert rows[1][1] == "Website redesign"
    assert rows[1][3] == "2000.00" and rows[1][5] == "no-ack"
    assert rows[2][1] == "Logo refresh"
    assert rows[2][3] == "500.00" and rows[2][5] == "ack"


def test_project_list_csv_no_projects_header_only(db, sample_client):
    rows = _project_csv_rows()
    assert rows == [PROJECT_LIST_CSV_COLUMNS]


def test_project_list_csv_file_path_writes_file_and_text_view(
        db, sample_client, sample_project, tmp_path):
    path = tmp_path / "project-list.csv"
    res = CliRunner().invoke(cli, ["project", "list",
                                   "--csv", str(path)])
    assert res.exit_code == 0, res.output
    assert "#  1" in res.output  # text view still prints
    assert "[no-ack]" in res.output  # ack bracket still prints
    assert f"wrote project list CSV: {path} (1 rows)" in res.output
    rows = list(csv.reader(io.StringIO(path.read_text())))
    assert rows[0] == PROJECT_LIST_CSV_COLUMNS
    assert rows[1][1] == "Website redesign"
    assert rows[1][3] == "2000.00" and rows[1][5] == "no-ack"


def test_project_list_csv_stdout_replaces_text_view(
        db, sample_client, sample_project):
    res = CliRunner().invoke(cli, ["project", "list", "--csv", "-"])
    assert res.exit_code == 0, res.output
    assert res.output.startswith(
        "project_id,project,client,amount,currency,")
    assert "#  1" not in res.output  # no text view


def test_project_list_csv_bare_library_call(db, sample_client):
    # A bare call needs no timing map — the list carries no `--as-of`
    # — and formula-looking project titles and client names are
    # neutralized on the free-text cells.
    db.add_client("=Evil Corp", "evil@acme.test")
    evil_client = db.get_client(2)
    _add_project(db, evil_client["id"], "=Evil Project", 200_000)
    rows = db.list_projects()
    bare = list(csv.reader(io.StringIO(project_list_csv(rows))))
    assert bare[0] == PROJECT_LIST_CSV_COLUMNS
    assert len(bare) == 2
    assert bare[1][0] == "1"
    assert bare[1][1] == "'=Evil Project"  # formula injection neutralized
    assert bare[1][2] == "'=Evil Corp"
