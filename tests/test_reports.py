"""Receivables aging report + CSV export (v0.6.0).

All dates are pinned with --as-of / as_of so the tests never depend on
the wall clock.
"""

import csv
import io

from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.late_fees import late_fee_summary
from invoiceguard.reports import (aging_bucket, invoices_csv, payments_csv,
                                  receivables_report)

AS_OF = "2026-10-08"


def _invoice(db, project_id, amount_cents, due_date, status="sent",
             currency=None):
    proj = db.get_project(project_id)
    return db.add_invoice(project_id, "milestone", amount_cents,
                          currency or proj["currency"], status=status,
                          due_date=due_date)


def test_aging_bucket_boundaries():
    assert aging_bucket(None) == "current"
    assert aging_bucket(0) == "current"
    assert aging_bucket(1) == "1-30"
    assert aging_bucket(30) == "1-30"
    assert aging_bucket(31) == "31-60"
    assert aging_bucket(60) == "31-60"
    assert aging_bucket(61) == "61-90"
    assert aging_bucket(90) == "61-90"
    assert aging_bucket(91) == "90+"


def test_report_buckets_outstanding_and_fees(db, sample_project):
    pid = sample_project["id"]
    # not yet due → Current; due 2026-10-20, as of 2026-10-08
    _invoice(db, pid, 50_000, "2026-10-20")
    # 18 days overdue → 1-30, with a partial payment of $250
    mid = _invoice(db, pid, 100_000, "2026-09-20")
    db.record_payment(mid, 25_000, method="bank")
    # 99 days overdue → 90+, old enough to accrue late fees
    old = _invoice(db, pid, 200_000, "2026-07-01")

    rep = receivables_report(db, as_of=AS_OF)
    assert rep["as_of"] == AS_OF
    assert [r["id"] for r in rep["rows"]] == [old, mid, 1]  # oldest first

    usd = {b["key"]: b for b in rep["buckets"]["USD"]}
    assert usd["current"]["count"] == 1
    assert usd["current"]["outstanding_cents"] == 50_000
    assert usd["1-30"]["count"] == 1
    assert usd["1-30"]["outstanding_cents"] == 75_000  # 100k - 25k paid
    assert usd["31-60"]["count"] == 0
    assert usd["90+"]["outstanding_cents"] == 200_000

    totals = rep["totals"]["USD"]
    assert totals["count"] == 3
    assert totals["outstanding_cents"] == 325_000

    old_row = next(r for r in rep["rows"] if r["id"] == old)
    assert old_row["days_overdue"] == 99
    assert old_row["bucket"] == "90+"
    expected = late_fee_summary(db.invoice_full(old), as_of=AS_OF)
    assert expected["fees_cents"] > 0
    assert old_row["late_fees_cents"] == expected["fees_cents"]
    assert old_row["total_due_cents"] == 200_000 + expected["fees_cents"]
    # the 18-day invoice is also past its 15-day grace: 1 month billed
    mid_row = next(r for r in rep["rows"] if r["id"] == mid)
    mid_expected = late_fee_summary(db.invoice_full(mid), as_of=AS_OF)
    assert mid_row["late_fees_cents"] == mid_expected["fees_cents"] > 0
    assert totals["fees_cents"] == (expected["fees_cents"]
                                    + mid_expected["fees_cents"])
    assert totals["total_cents"] == (totals["outstanding_cents"]
                                     + totals["fees_cents"])


def test_report_excludes_paid_void_and_draft(db, sample_project):
    pid = sample_project["id"]
    open_id = _invoice(db, pid, 10_000, "2026-09-01")
    paid_id = _invoice(db, pid, 10_000, "2026-09-01")
    db.record_payment(paid_id, 10_000)
    void_id = _invoice(db, pid, 10_000, "2026-09-01")
    db.update_invoice(void_id, status="void")
    _invoice(db, pid, 10_000, "2026-09-01", status="draft")

    rep = receivables_report(db, as_of=AS_OF)
    assert [r["id"] for r in rep["rows"]] == [open_id]


def test_report_no_due_date_is_current(db, sample_project):
    iid = _invoice(db, sample_project["id"], 10_000, None)
    rep = receivables_report(db, as_of=AS_OF)
    row = rep["rows"][0]
    assert row["id"] == iid
    assert row["days_overdue"] is None
    assert row["bucket"] == "current"


def test_report_aggregates_never_mix_currencies(db, sample_client):
    from invoiceguard.contracts import render_contract

    contract = render_contract("Acme Corp", "billing@acme.test", "EU work",
                               100_000, "EUR", 50.0, 1.5, 15)
    eur_pid = db.add_project(sample_client["id"], "EU work", 100_000,
                             "EUR", 50.0, 1.5, 15, contract)
    _invoice(db, eur_pid, 100_000, "2026-09-01")
    usd_pid = db.add_project(sample_client["id"], "US work", 300_000,
                             "USD", 50.0, 1.5, 15, "contract")
    _invoice(db, usd_pid, 300_000, "2026-09-01")

    rep = receivables_report(db, as_of=AS_OF)
    assert rep["totals"]["USD"]["outstanding_cents"] == 300_000
    assert rep["totals"]["EUR"]["outstanding_cents"] == 100_000
    assert {c["currency"] for c in rep["by_client"]} == {"USD", "EUR"}


def _read_csv(text):
    return list(csv.reader(io.StringIO(text)))


def test_invoices_csv_rows(db, sample_project):
    mid = _invoice(db, sample_project["id"], 100_000, "2026-09-20")
    db.record_payment(mid, 25_000, method="bank", note="check #1")
    rep = receivables_report(db, as_of=AS_OF)
    rows = _read_csv(invoices_csv(rep["rows"]))
    assert rows[0] == ["invoice_id", "client", "project", "kind",
                       "status", "currency", "amount", "paid",
                       "outstanding", "late_fees", "total_due",
                       "due_date", "days_overdue"]
    assert len(rows) == 2
    r = rows[1]
    assert r[0] == str(mid) and r[1] == "Acme Corp"
    assert r[6] == "1000.00" and r[7] == "250.00" and r[8] == "750.00"
    assert r[11] == "2026-09-20" and r[12] == "18"


def test_payments_csv_covers_paid_invoices_too(db, sample_project):
    iid = _invoice(db, sample_project["id"], 100_000, "2026-09-20")
    db.record_payment(iid, 40_000, method="bank", note="check #1")
    db.record_payment(iid, 60_000, method="stripe")
    # invoice is fully paid now — its payments must still export
    assert receivables_report(db, as_of=AS_OF)["rows"] == []
    rows = _read_csv(payments_csv(db))
    assert rows[0][0] == "payment_id"
    assert len(rows) == 3
    assert rows[1][5] == "bank" and rows[1][6] == "400.00"
    assert rows[1][8] == "check #1"
    assert rows[2][5] == "stripe" and rows[2][6] == "600.00"


def test_cli_report_text(home, db, sample_project):
    _invoice(db, sample_project["id"], 100_000, "2026-09-20")
    res = CliRunner().invoke(cli, ["invoice", "report",
                                   "--as-of", AS_OF])
    assert res.exit_code == 0, res.output
    assert "receivables report (as of 2026-10-08)" in res.output
    assert "1-30 days overdue" in res.output
    assert "Acme Corp" in res.output
    assert "by client:" in res.output


def test_cli_report_empty(home):
    res = CliRunner().invoke(cli, ["invoice", "report",
                                   "--as-of", AS_OF])
    assert res.exit_code == 0, res.output
    assert "no open invoices" in res.output


def test_cli_report_csv_file_and_stdout(home, db, sample_project,
                                        tmp_path):
    _invoice(db, sample_project["id"], 100_000, "2026-09-20")
    r = CliRunner()
    out_file = tmp_path / "inv.csv"
    res = r.invoke(cli, ["invoice", "report", "--as-of", AS_OF,
                         "--csv", str(out_file)])
    assert res.exit_code == 0, res.output
    assert "wrote invoice CSV" in res.output
    rows = _read_csv(out_file.read_text(encoding="utf-8"))
    assert rows[0][0] == "invoice_id" and len(rows) == 2

    res = r.invoke(cli, ["invoice", "report", "--as-of", AS_OF,
                         "--csv", "-"])
    assert res.exit_code == 0, res.output
    assert res.output.startswith("invoice_id,client,project")
    assert "receivables report" not in res.output


def test_cli_report_payments_csv(home, db, sample_project, tmp_path):
    iid = _invoice(db, sample_project["id"], 100_000, "2026-09-20")
    db.record_payment(iid, 40_000, method="bank")
    out_file = tmp_path / "pay.csv"
    res = CliRunner().invoke(cli, ["invoice", "report", "--as-of", AS_OF,
                                   "--payments-csv", str(out_file)])
    assert res.exit_code == 0, res.output
    assert "wrote payments CSV" in res.output
    rows = _read_csv(out_file.read_text(encoding="utf-8"))
    assert rows[0][0] == "payment_id" and len(rows) == 2


def test_cli_report_bad_date_and_double_stdout(home):
    r = CliRunner()
    res = r.invoke(cli, ["invoice", "report", "--as-of", "08/10/2026"])
    assert res.exit_code != 0
    assert "bad --as-of date" in res.output
    res = r.invoke(cli, ["invoice", "report", "--csv", "-",
                         "--payments-csv", "-"])
    assert res.exit_code != 0
    assert "only one CSV" in res.output
