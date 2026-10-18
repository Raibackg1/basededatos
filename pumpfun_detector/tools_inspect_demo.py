import sqlite3, pathlib
db = pathlib.Path(os.environ.get("PUMPSCOPE_DEMO_DB", "pumpfun_detector/demo_pumpfun.db"))
con = sqlite3.connect(str(db))
cur = con.cursor()
tabs = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print("TABLAS:", tabs)
for t in tabs:
    print("="*70)
    print("TABLA:", t)
    cols = list(cur.execute(f'PRAGMA table_info("{t}")'))
    for c in cols[:30]:
        print(f"  col {c[1]} [{c[2]}] pk={c[5]}")
    cnt = cur.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
    print(f"  filas={cnt:,}")
    try:
        rows = list(cur.execute(f'SELECT * FROM "{t}" LIMIT 3'))
        for r in rows:
            print("  ej:", str(r)[:400])
    except Exception as e:
        print("  err sample:", e)
con.close()
