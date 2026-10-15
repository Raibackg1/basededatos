import sqlite3
import pandas as pd
c = sqlite3.connect("live_demo.db")
tabs = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print("TABLAS:", tabs)
for t in tabs:
    df = pd.read_sql(f"SELECT * FROM {t}", c)
    print("====", t, df.shape)
    cols = [cc for cc in df.columns if cc not in ("features_json",)]
    print(df[cols].to_string())
