from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from core.api.dependencies import get_session
from core.main import app
from tests.support import PostgreSQLTest

MACRO = {
    "housing_starts_index": 96.4,
    "lumber_price_index": 388.0,
    "mortgage_rate": 3.95,
    "season_factor": 0.581,
}
SALE = {
    "date": "2019-01-07",
    "region": "Pacific",
    "product_category": "lumber",
    "sku": "LUM-001",
    "channel": "pro_dealer",
    "customer_type": "contractor",
    "units": 3,
    "unit_price": "0.10",
}


class APITest(PostgreSQLTest):
    def setUp(self):
        super().setUp()

        def test_session():
            with Session(bind=self.connection, join_transaction_mode="create_savepoint") as session:
                yield session

        app.dependency_overrides[get_session] = test_session
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def save_week(self, week="2019-01-07", **changes):
        response = self.client.put(f"/macro-weeks/{week}", json={**MACRO, **changes})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def save_sale(self, **changes):
        response = self.client.post("/transactions", json={**SALE, **changes})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_missing_week_rolls_back_and_can_be_fixed(self):
        response = self.client.post("/transactions", json=SALE)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["missing_week"], SALE["date"])
        self.assertEqual(self.client.get("/transactions").json()["items"], [])
        self.save_week()
        sale = self.save_sale()
        self.assertEqual(sale["revenue"], "0.30")
        self.assertEqual(sale["unit_price"], "0.10")
        self.assertEqual((sale["year"], sale["month"], sale["week_of_year"]), (2019, 1, 2))
        self.assertEqual(sale["macro_week"]["mortgage_rate"], MACRO["mortgage_rate"])
        self.assertEqual(self.client.get(f"/transactions/{sale['id']}").json(), sale)

    def test_macro_upsert_is_visible_to_existing_sales(self):
        self.save_week()
        sale = self.save_sale()
        updated = self.save_week(mortgage_rate=4.5)
        self.assertEqual(self.client.get("/macro-weeks").json(), [updated])
        self.assertEqual(self.client.get("/macro-weeks/2019-01-07").json(), updated)
        result = self.client.get(f"/transactions/{sale['id']}").json()
        self.assertEqual(result["macro_week"]["mortgage_rate"], 4.5)

    def test_filters_pagination_and_grouped_aggregate(self):
        self.save_week()
        self.save_week("2019-01-14")
        first = self.save_sale()
        second = self.save_sale(product_category="roofing", units=2, unit_price="10.00")
        self.save_sale(date="2019-01-14", region="South_Atlantic", units=4, unit_price="2.25")
        page = self.client.get("/transactions", params={"limit": 1, "offset": 1}).json()
        self.assertEqual(page["items"], [second])
        filtered = self.client.get(
            "/transactions", params={"region": "Pacific", "product_category": "lumber"}
        ).json()
        self.assertEqual(filtered["items"], [first])
        totals = self.client.get(
            "/transactions/aggregate", params={"region": "Pacific", "group_by": "product_category"}
        ).json()
        self.assertEqual(totals["transaction_count"], 2)
        self.assertEqual(totals["total_units"], 5)
        self.assertEqual(Decimal(totals["total_revenue"]), Decimal("20.30"))
        self.assertEqual([row["value"] for row in totals["groups"]], ["lumber", "roofing"])
        self.assertEqual([row["transaction_count"] for row in totals["groups"]], [1, 1])
        by_date = self.client.get("/transactions/aggregate", params={"group_by": "date"}).json()
        self.assertEqual([row["value"] for row in by_date["groups"]], ["2019-01-07", "2019-01-14"])
        period = self.client.get(
            "/transactions", params={"date_from": "2019-01-07", "date_to": "2019-01-07"}
        ).json()
        self.assertEqual(len(period["items"]), 2)

    def test_no_matches_and_unknown_records(self):
        self.assertEqual(self.client.get("/transactions/999").status_code, 404)
        self.assertEqual(self.client.get("/macro-weeks/2019-01-07").status_code, 404)
        self.save_week()
        self.save_sale()
        result = self.client.get(
            "/transactions/aggregate", params={"region": "' OR 1=1 --", "group_by": "region"}
        ).json()
        self.assertEqual(result["transaction_count"], 0)
        self.assertEqual(result["total_units"], 0)
        self.assertEqual(Decimal(result["total_revenue"]), Decimal("0"))
        self.assertEqual(result["groups"], [])

    def test_invalid_sales_never_write(self):
        self.save_week()
        cases = [
            {"date": "2019-01-08"},
            {"units": 0},
            {"units": True},
            {"units": 2.5},
            {"unit_price": "0.001"},
            {"unit_price": "NaN"},
            {"unit_price": "-1.00"},
            {"units": 2147483647, "unit_price": "9999999999.99"},
            {"region": "  "},
            {"channel": "unknown"},
            {"revenue": "0.30"},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                response = self.client.post("/transactions", json={**SALE, **changes})
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.get("/transactions").json()["items"], [])

    def test_invalid_macros_and_query_parameters(self):
        incomplete = {key: value for key, value in MACRO.items() if key != "mortgage_rate"}
        self.assertEqual(
            self.client.put("/macro-weeks/2019-01-07", json=incomplete).status_code, 422
        )
        self.assertEqual(self.client.put("/macro-weeks/2019-01-08", json=MACRO).status_code, 422)
        cases = [
            ("/transactions", {"limit": 1001}),
            ("/transactions", {"offset": -1}),
            ("/transactions", {"date_from": "2019-02-01", "date_to": "2019-01-01"}),
            ("/transactions/aggregate", {"group_by": "revenue"}),
            ("/macro-weeks", {"limit": 0}),
        ]
        for path, params in cases:
            with self.subTest(path=path, params=params):
                self.assertEqual(self.client.get(path, params=params).status_code, 422)

    def test_health_and_openapi(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
        schema = self.client.get("/openapi.json").json()
        self.assertIn("post", schema["paths"]["/transactions"])
        self.assertIn("get", schema["paths"]["/transactions/aggregate"])
