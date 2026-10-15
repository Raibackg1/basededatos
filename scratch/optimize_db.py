import sqlite3, shutil, os, datetime

src = r"D:\hjh\pumpscope\db\custom.db"
dst = r"D:\hjh\pumpscope\db\custom.optimized.db"
ok = r"D:\hjh\pumpscope\db\custom.optimized.ok"
log = r"D:\hjh\pumpscope\db\custom.optimized.log"
if os.path.exists(dst):
    os.remove(dst)
if os.path.exists(ok):
    os.remove(ok)
lines = []
def L(msg):
    lines.append(msg)
    with open(log, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
L(f"start {datetime.datetime.now()}")
shutil.copy2(src, dst)
L("copied")
con = sqlite3.connect(dst)
cur = con.cursor()
stmts = [
    'CREATE INDEX IF NOT EXISTS idx_token_mint ON Token(mint)',
    'CREATE INDEX IF NOT EXISTS idx_token_creator ON Token(creator)',
    'CREATE INDEX IF NOT EXISTS idx_pp_tokenId ON PaperPosition(tokenId)',
    'CREATE INDEX IF NOT EXISTS idx_pp_status_created ON PaperPosition(status,createdAt)',
    'CREATE INDEX IF NOT EXISTS idx_snapshot_token_ts ON Snapshot(tokenId,ts)',
    'CREATE INDEX IF NOT EXISTS idx_livetrade_mint ON LiveTrade(mint)',
    'CREATE INDEX IF NOT EXISTS idx_whalesignal_mint ON WhaleSignal(mint)',
    'ANALYZE',
]
for s in stmts:
    L(f"applying: {s}")
    cur.execute(s)
    con.commit()
    L(f"done: {s}")
con.close()
L("finished")
open(ok, "w", encoding="utf-8").write("ok")
