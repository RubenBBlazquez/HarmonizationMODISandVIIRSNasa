"""La API funciona aunque la base aún no tenga la columna fire_events.land_cover."""

from backend.api import queries, routes


class Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self, has_column):
        self.has_column = has_column
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append(query)
        if query == routes.LAND_COVER_COLUMN_SQL:
            return Result((1,) if self.has_column else None)
        return Result(None)


def run_detail(has_column):
    routes._schema_cache.clear()
    connection = Connection(has_column)
    routes.SchemaCompatConnection(connection).execute(queries.EVENT_DETAIL_SQL, ("evt_1",))
    return connection.queries[-1]


def test_events_are_served_without_land_type_when_the_column_is_missing():
    sql = run_detail(has_column=False)

    assert routes.MISSING_LAND_COVER_SQL in sql
    assert queries.EVENT_LAND_COVER_SQL not in sql


def test_the_column_is_used_once_the_database_is_migrated():
    sql = run_detail(has_column=True)

    assert queries.EVENT_LAND_COVER_SQL in sql


def test_queries_without_land_cover_are_not_checked():
    routes._schema_cache.clear()
    connection = Connection(has_column=False)

    routes.SchemaCompatConnection(connection).execute("SELECT 1")

    assert connection.queries == ["SELECT 1"]
