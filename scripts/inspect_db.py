import sqlite3

conn = sqlite3.connect(os.environ.get("PUMPSCOPE_DEMO_DB", "pumpfun_detector/demo_pumpfun.db"))
cursor = conn.cursor()
cursor.execute('SELECT name FROM sqlite_master WHERE type="table"')
tables = cursor.fetchall()
print('Tables:', tables)
for t in tables:
    name = t[0]
    try:
        count = cursor.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
        print(f'  {name}: {count} rows')
    except Exception as e:
        print(f'  {name}: error - {e}')

# Check schema of main tables
for t in tables:
    name = t[0]
    try:
        cursor.execute(f'PRAGMA table_info({name})')
        cols = cursor.fetchall()
        print(f'\n{name} columns:')
        for c in cols[:10]:
            print(f'  {c}')
    except:
        pass

conn.close()