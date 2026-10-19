import sqlite3

import pytest

from corki.storage.admin import DatabaseAdmin


@pytest.fixture
def admin(tmp_path):
    with sqlite3.connect(tmp_path / "fixture.db") as db:
        db.executescript("""
        CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                           amount INTEGER, payload BLOB);
        CREATE TABLE composite(a TEXT,b INTEGER,value TEXT,PRIMARY KEY(a,b)) WITHOUT ROWID;
        CREATE TABLE child(id INTEGER PRIMARY KEY, parent INTEGER REFERENCES items(id));
        CREATE VIEW item_view AS SELECT * FROM items;
        CREATE TABLE generated(a INTEGER,b INTEGER GENERATED ALWAYS AS (a*2));
        INSERT INTO items VALUES(1,'示例',9223372036854775807,x'00ff');
        INSERT INTO composite VALUES('key',2,'original');
        INSERT INTO child VALUES(1,1);
        """)
    return DatabaseAdmin(tmp_path)


def mutate(admin, action, table="items", row=None, **values):
    return admin.mutate(
        dict(
            database="fixture.db",
            table=table,
            action=action,
            values=values,
            key=row and row["key"],
            revision=row and row["revision"],
        )
    )


def test_roundtrip_and_conflict(admin):
    assert admin.catalog()["databases"][0]["name"] == "fixture.db"
    row = admin.read("fixture.db", "items")["rows"][0]
    assert row["values"]["amount"] == {"$integer": "9223372036854775807"}
    assert row["values"]["payload"] == {"$blob": "AP8="}
    mutate(admin, "update", row=row, **{**row["values"], "name": "修改"})
    with pytest.raises(ValueError, match="已变更"):
        mutate(admin, "update", row=row, name="stale")
    assert admin.read("fixture.db", "items", search="修改")["total"] == 1
    assert admin.read("fixture.db", "items", search="' OR 1=1 --")["total"] == 0


def test_constraints_and_scope(admin, tmp_path):
    row = admin.read("fixture.db", "items")["rows"][0]
    with pytest.raises(ValueError, match="FOREIGN KEY"):
        mutate(admin, "delete", row=row)
    with pytest.raises(ValueError, match="NOT NULL"):
        mutate(admin, "insert", name=None)
    with pytest.raises(ValueError):
        admin.read("../fixture.db", "items")
    with pytest.raises(ValueError):
        admin.read("fixture.db", "items; DROP TABLE items")
    with pytest.raises(ValueError):
        mutate(admin, "delete", row={**row, "key": {}})
    with pytest.raises(ValueError):
        mutate(admin, "insert", unknown="x")
    with pytest.raises(ValueError):
        mutate(admin, "insert", table="item_view", name="x")
    assert admin.read("fixture.db", "item_view")["readonly"]
    (tmp_path / "linked.db").symlink_to(tmp_path / "fixture.db")
    assert len(admin.catalog()["databases"]) == 1


def test_composite_generated_and_pagination(admin):
    row = admin.read("fixture.db", "composite")["rows"][0]
    mutate(admin, "update", table="composite", row=row, value="updated")
    row = admin.read("fixture.db", "composite")["rows"][0]
    mutate(admin, "delete", table="composite", row=row)
    assert admin.read("fixture.db", "composite")["total"] == 0
    mutate(admin, "insert", table="generated", a=4)
    assert admin.read("fixture.db", "generated")["rows"][0]["values"]["b"] == 8
    with pytest.raises(ValueError):
        mutate(admin, "insert", table="generated", b=1)
    for i in range(55):
        mutate(admin, "insert", name=f"row{i}")
    result = admin.read("fixture.db", "items", 50)
    assert result["total"] == 56 and len(result["rows"]) == 6


def test_sql_query_and_confirmation(admin):
    def sql(text, confirmed=False):
        return admin.query({"database": "fixture.db", "sql": text, "confirmed": confirmed})

    result = sql("SELECT name, amount FROM items")
    assert result["rows"][0] == ["示例", {"$integer": "9223372036854775807"}]
    assert not result["writes"]
    assert sql("UPDATE items SET name='updated'") == {"requires_confirmation": True}
    assert admin.read("fixture.db", "items")["rows"][0]["values"]["name"] == "示例"
    assert sql("UPDATE items SET name='updated'", True)["affected"] == 1
    assert sql("CREATE TABLE created(id INTEGER PRIMARY KEY)")["requires_confirmation"]
    assert sql("CREATE TABLE created(id INTEGER PRIMARY KEY)", True)["writes"]
    assert sql("INSERT INTO created VALUES(1) RETURNING id", True)["rows"] == [[1]]
    assert sql("DROP TABLE created", True)["writes"]
    result = sql(
        "WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<250) SELECT x FROM n"
    )
    assert len(result["rows"]) == 200 and result["truncated"]
    with pytest.raises(ValueError):
        sql("UPDATE items SET name='broken'; DELETE FROM items", True)
    assert sql("SELECT name FROM items")["rows"] == [["updated"]]
    for text in (
        "COMMIT",
        "PRAGMA writable_schema=ON",
        "ATTACH ':memory:' AS external",
        "SELECT load_extension('x')",
    ):
        with pytest.raises(ValueError):
            sql(text, True)
    with pytest.raises(ValueError):
        sql("DELETE FROM items", True)
    assert sql("SELECT count(*) FROM items")["rows"] == [[1]]
