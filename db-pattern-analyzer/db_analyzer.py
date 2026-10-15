#!/usr/bin/env python3
"""db_analyzer.py — Analizador/Identificador/Mejorador GENERICO SQLite.
FASES: INSPECT -> PROFILE -> MINE -> IMPROVE (dry-run por defecto).
Uso: py db_analyzer.py --db "ruta\\mi.db" --out resultados_db [--label complete] [--apply]
"""
import argparse, json, os, re, shutil, sqlite3

def qident(s): return '"' + str(s).replace('"','""') + '"'

def inspect_db(db_path):
    con = sqlite3.connect(db_path); cur = con.cursor()
    tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    info = {}
    for t in tables:
        cols = list(cur.execute(f'PRAGMA table_info({qident(t)})'))
        fks = list(cur.execute(f'PRAGMA foreign_key_list({qident(t)})'))
        idxs = list(cur.execute(f'PRAGMA index_list({qident(t)})'))
        try: n = cur.execute(f'SELECT COUNT(*) FROM {qident(t)}').fetchone()[0]
        except Exception: n = -1
        info[t] = {"columns":[{"name":c[1],"type":(c[2] or "").upper(),"pk":c[5]} for c in cols],
                   "fks":[{"from":f[2],"to_table":f[3],"to_col":f[4]} for f in fks],
                   "indexes":[{"name":i[1]} for i in idxs], "rows":n}
    try:
        pc = cur.execute('PRAGMA page_count').fetchone()[0]
        fl = cur.execute('PRAGMA freelist_count').fetchone()[0]
    except Exception: pc, fl = -1, -1
    con.close()
    return {"tables":info, "pragma":{"page_count":pc,"freelist_count":fl}}

def profile_db(db_path, schema):
    con = sqlite3.connect(db_path); cur = con.cursor()
    prof = {}
    for t, ti in schema["tables"].items():
        tp = {"rows": ti["rows"], "cols": {}}
        for c in ti["columns"]:
            name, typ = c["name"], c["type"]
            try:
                n = ti["rows"]
                nulls = cur.execute(f'SELECT COUNT(*) FROM {qident(t)} WHERE {qident(name)} IS NULL').fetchone()[0]
                distinct = cur.execute(f'SELECT COUNT(DISTINCT {qident(name)}) FROM {qident(t)}').fetchone()[0]
            except Exception as e:
                tp["cols"][name] = {"error": str(e)[:200]}; continue
            d = {"type": typ, "null_pct": round(nulls/max(n,1)*100,2), "distinct": distinct}
            if "INT" in typ or "REAL" in typ or "FLOA" in typ or "DOUB" in typ or "NUM" in typ:
                try:
                    r = cur.execute(f'SELECT MIN({qident(name)}), MAX({qident(name)}), AVG({qident(name)}) FROM {qident(t)} WHERE {qident(name)} IS NOT NULL').fetchone()
                    d.update({"min": r[0], "max": r[1], "avg": r[2]})
                except Exception: pass
            else:
                try:
                    top = list(cur.execute(f'SELECT {qident(name)}, COUNT(*) c FROM {qident(t)} WHERE {qident(name)} IS NOT NULL GROUP BY 1 ORDER BY c DESC LIMIT 5'))
                    d["top5"] = [(str(v)[:60], cnt) for v, cnt in top]
                except Exception: pass
            flags = []
            if d["null_pct"] > 30: flags.append(f'NULOS ALTOS {d["null_pct"]}%')
            if distinct == 1 and n > 10: flags.append("CONSTANTE")
            if 2 <= distinct <= 10 and "INT" not in typ: flags.append("ENUM IMPLICITO->lookup")
            if re.search(r'date|time', name, re.I) and typ == "TEXT": flags.append("FECHA COMO TEXTO->epoch")
            if re.search(r'price|pnl|amount|sol|usd', name, re.I) and typ == "REAL": flags.append("DINERO REAL->INTEGER")
            if re.match(r'.*_(id|mint)$', name, re.I) or name.lower() in ("mint","id","wallet","token_mint"): flags.append("CANDIDATA INDICE/FK")
            d["flags"] = flags
            tp["cols"][name] = d
        pks = [c["name"] for c in ti["columns"] if c["pk"]]
        tp["pk"] = pks
        salud = []
        if not pks: salud.append("SIN PK")
        if not ti["indexes"]: salud.append("SIN INDICES")
        if len(ti["columns"]) > 30: salud.append("GOD-TABLE")
        if not ti["fks"]: salud.append("SIN FK DECLARADA")
        tp["salud"] = salud
        prof[t] = tp
    orphans = []
    for t, ti in schema["tables"].items():
        for c in ti["columns"]:
            m = re.match(r'(.+?)_(id|mint)$', c["name"], re.I)
            if not m: continue
            base = m.group(1).lower()
            for t2 in schema["tables"]:
                if base in t2.lower() or t2.lower().rstrip("s") == base.rstrip("s"):
                    try:
                        pk2 = next((x["name"] for x in schema["tables"][t2]["columns"] if x["pk"]), schema["tables"][t2]["columns"][0]["name"])
                        oh = cur.execute(f'SELECT COUNT(*) FROM {qident(t)} a LEFT JOIN {qident(t2)} b ON a.{qident(c["name"])}=b.{qident(pk2)} WHERE a.{qident(c["name"])} IS NOT NULL AND b.{qident(pk2)} IS NULL').fetchone()[0]
                        if oh: orphans.append({"from": f"{t}.{c['name']}", "to": f"{t2}.{pk2}", "orphans": oh})
                    except Exception: pass
                    break
    con.close()
    return {"profile": prof, "orphans": orphans}


def mine_label(db_path, schema, label):
    try:
        import pandas as pd
    except ImportError:
        return {"ok": False, "reason": "falta pandas"}
    con = sqlite3.connect(db_path); out = []
    for t, ti in schema["tables"].items():
        cols = [c["name"] for c in ti["columns"]]
        lab = label if (label and label in cols) else next((c for c in ["complete","graduated","win","status"] if c in cols), None)
        if not lab: continue
        try: df = pd.read_sql(f'SELECT * FROM {qident(t)} LIMIT 20000', con)
        except Exception as e: out.append({"tabla": t, "error": str(e)[:150]}); continue
        u = df[lab].dropna().unique()
        if len(u) != 2: continue
        y = (df[lab] == u.max()).astype(int) if df[lab].dtype.kind in "ifb" else (df[lab] == u[0]).astype(int)
        base = float(y.mean())
        for c in df.columns:
            if c == lab or df[c].dtype.kind not in "ifb" or df[c].notna().sum() < 30: continue
            try: qq = pd.qcut(df[c].rank(method="first"), 5, labels=False, duplicates="drop")
            except Exception: continue
            for g in sorted(pd.Series(qq).dropna().unique()):
                sel = (qq == g); nn = int(sel.sum())
                if nn < 15: continue
                wr = float(y[sel].mean()); lift = wr/base if base > 0 else float("nan")
                if lift >= 1.25 or lift <= 0.8:
                    out.append({"tabla": t, "feature": c, "bin": f"Q{int(g)+1} [{float(df[c][sel].min()):.3g}-{float(df[c][sel].max()):.3g}]",
                                "n": nn, "winrate": round(wr*100,2), "base": round(base*100,2), "lift": round(float(lift),2)})
    con.close()
    out.sort(key=lambda r: -abs(r.get("lift", 1)-1))
    return {"ok": True, "hallazgos": out[:60]}

def build_improvements(schema):
    stmts = []
    for t, ti in schema["tables"].items():
        for c in ti["columns"]:
            cn = c["name"]
            if re.match(r'.*_(id|mint)$', cn, re.I) or cn.lower() in ("mint","id","wallet","token_mint","block_time","created_at","entry_time"):
                idx = f'idx_{re.sub(r"[^a-z0-9]+","_",t.lower())}_{re.sub(r"[^a-z0-9]+","_",cn.lower())}'[:60]
                stmts.append(f'CREATE INDEX IF NOT EXISTS {idx} ON {qident(t)}({qident(cn)});')
    stmts.append('ANALYZE;')
    notes = ["DRY-RUN: revisa mejoras.sql antes de --apply.",
             "WAL solo con aprobacion: PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
             "Si freelist alta: VACUUM INTO 'backup.db';",
             "ENUM implicito->lookup, FECHA TEXT->INTEGER epoch, REAL dinero->INTEGER.",
             "Todo SCAN en EXPLAIN QUERY PLAN sobre tabla grande -> indice (idea supabase/index_advisor)."]
    return stmts, notes

def main():
    ap = argparse.ArgumentParser(description="Analizador generico SQLite")
    ap.add_argument("--db", required=True); ap.add_argument("--out", default="resultados_db")
    ap.add_argument("--label", default=None); ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    schema = inspect_db(args.db); prof = profile_db(args.db, schema); mined = mine_label(args.db, schema, args.label)
    stmts, notes = build_improvements(schema)
    with open(os.path.join(args.out,"schema.json"),"w",encoding="utf-8") as f: json.dump(schema,f,indent=1,default=str)
    with open(os.path.join(args.out,"profile.json"),"w",encoding="utf-8") as f: json.dump(prof,f,indent=1,default=str)
    with open(os.path.join(args.out,"hallazgos.json"),"w",encoding="utf-8") as f: json.dump(mined,f,indent=1,default=str)
    with open(os.path.join(args.out,"mejoras.sql"),"w",encoding="utf-8") as f: f.write("\n".join(stmts)+"\n")
    L = [f"# Analisis DB: {args.db}", f"Tablas:{len(schema['tables'])} freelist={schema['pragma']['freelist_count']}"]
    for t,ti in schema["tables"].items():
        L.append(f"\n## {t} ({ti['rows']:,} filas)")
        p = prof["profile"][t]
        if p["salud"]: L.append("salud: "+" ; ".join(p["salud"]))
        for cn,cd in p["cols"].items():
            if cd.get("flags"): L.append(f"  - {cn}: nulos={cd['null_pct']}% distintos={cd['distinct']:,} | {' // '.join(cd['flags'])}")
    if prof["orphans"]:
        L.append("\n## Huerfanos"); [L.append(f"  - {o}") for o in prof["orphans"][:20]]
    if mined.get("ok") and mined.get("hallazgos"):
        L.append("\n## Patrones lift>=1.25 o <=0.8")
        [L.append(f"  - [{h['tabla']}] {h['feature']} {h['bin']} n={h['n']} WR={h['winrate']}% base={h['base']}% lift={h['lift']}x") for h in mined["hallazgos"][:30]]
    L.append("\n## Mejoras"); L += [f"  - {s}" for s in stmts]; L.append("\nNotas:"); L += [f"  * {n}" for n in notes]
    with open(os.path.join(args.out,"reporte.md"),"w",encoding="utf-8") as f: f.write("\n".join(L))
    print("\n".join(L[:90])); print(f"\nOK -> {args.out}/")
    if args.apply:
        bak = os.path.join(args.out, "backup.db"); shutil.copy2(args.db, bak)
        con = sqlite3.connect(bak); cur = con.cursor()
        for s in stmts:
            try: cur.execute(s)
            except Exception as e: print("skip:", s[:70], str(e)[:120])
        con.commit(); con.close(); print(f"Aplicado en copia: {bak}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

