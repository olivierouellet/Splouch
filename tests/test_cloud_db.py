"""The control plane's schema: migrations apply once and the tables hold.

Migrations run on every control-plane start (cloud/cloud_db.py), so applying them
a second time must be a no-op, and the regions the admin assigns organizers to
must exist from the first start.
"""

import pytest

pytestmark = pytest.mark.usefixtures("pg")


def test_migrating_again_changes_nothing(pg):
    pg.migrate()
    with pg.conn() as c:
        versions = [
            r["version"] for r in c.execute("SELECT version FROM schema_version")
        ]
    assert versions == [v for v, _ in pg.MIGRATIONS]


def test_the_three_regions_exist_from_the_first_start(pg):
    with pg.conn() as c:
        codes = {r["code"] for r in c.execute("SELECT code FROM regions")}
    assert codes == {"ca", "us", "eu"}


def test_tests_start_from_empty_tables(pg):
    with pg.conn() as c:
        for table in pg.DATA_TABLES:
            assert c.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == 0
