import unittest
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema

from core.config import DATABASE_URL
from core.data import Base


class PostgreSQLTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            DATABASE_URL, hide_parameters=True, connect_args={"connect_timeout": 5}
        )
        cls.addClassCleanup(cls.engine.dispose)

    def setUp(self):
        self.connection = self.engine.connect()
        self.addCleanup(self.connection.close)
        outer_transaction = self.connection.begin()
        self.addCleanup(outer_transaction.rollback)
        schema = "astra_test_" + uuid4().hex
        self.connection.execute(CreateSchema(schema))
        self.connection.execute(
            text("SELECT set_config('search_path', :schema, true)"), {"schema": schema}
        )
        Base.metadata.create_all(self.connection, checkfirst=False)
        self.session = Session(bind=self.connection, join_transaction_mode="create_savepoint")
        self.addCleanup(self.session.close)
