import csv
import os
import unittest
from decimal import Decimal
from pathlib import Path

from core.data.queries import aggregate_transactions
from core.import_csv import import_files
from tests.support import PostgreSQLTest


@unittest.skipUnless(
    os.environ.get("ASTRA_FULL_DATASET_TEST") == "1", "Full dataset test is opt-in"
)
class DatasetTest(PostgreSQLTest):
    def test_full_dataset_totals_and_repeat_import(self):
        directory = Path(__file__).resolve().parents[1] / (
            "us-building-materials-sales-transactions-20192024"
        )
        macros = directory / "macro_drivers_weekly.csv"
        sales = directory / "building_materials_transactions.csv"
        expected_count = 0
        expected_units = 0
        expected_revenue = Decimal("0.00")
        with sales.open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                expected_count += 1
                expected_units += int(row["units"])
                expected_revenue += Decimal(row["revenue"])

        with self.session.begin():
            first = import_files(self.session, macro_path=macros, transactions_path=sales)
        self.assertEqual(first.macro_rows, 313)
        self.assertEqual(first.inserted_transactions, expected_count)
        self.assertEqual(first.skipped_transactions, 0)
        with self.session.begin():
            repeat = import_files(self.session, macro_path=macros, transactions_path=sales)
        self.assertEqual(repeat.inserted_transactions, 0)
        self.assertEqual(repeat.skipped_transactions, expected_count)
        totals = aggregate_transactions(self.session)
        self.assertEqual(totals.transaction_count, expected_count)
        self.assertEqual(totals.total_units, expected_units)
        self.assertEqual(totals.total_revenue, expected_revenue)
