import sqlite3
import os
conn = sqlite3.connect(os.environ.get("PUMPSCOPE_DB", "custom.db"))
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
print('Tables:')
for t in tables:
    name = t[0]
    try:
        count = cursor.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
        print(f'  {name}: {count:,} rows')
    except Exception as e:
        print(f'  {name}: error - {e}')
conn.close()