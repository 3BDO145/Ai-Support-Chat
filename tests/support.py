import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

ORDERS_SOURCE = ROOT / "data" / "orders.db"


class OrdersCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not ORDERS_SOURCE.exists():
            raise unittest.SkipTest("data/orders.db is missing, run app/prepare_orders.py first")

    def setUp(self):
        import orders
        self.orders = orders
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "orders.db")
        shutil.copy(ORDERS_SOURCE, self.db_path)
        self.original_path = orders.DB_PATH
        orders.DB_PATH = self.db_path
        orders.init()

    def tearDown(self):
        self.orders.DB_PATH = self.original_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def query(self, sql, *args):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        try:
            return con.execute(sql, args).fetchone()
        finally:
            con.close()

    def pick(self, where):
        row = self.query(f"SELECT order_no, customer_email FROM orders WHERE {where} LIMIT 1")
        if row is None:
            self.skipTest(f"no order matches: {where}")
        return row["order_no"], row["customer_email"]

    def field(self, order_no, column):
        return self.query(f"SELECT {column} AS v FROM orders WHERE order_no=?", order_no)["v"]