"""
Inspector de esquemas SQLite + mapeo semántico automático de columnas.

El detector no asume nombres fijos de tablas/columnas: inspecciona la base,
puntúa cada tabla contra "perfiles" (tokens, trades, my_trades, ohlcv) y asigna
roles semánticos a las columnas mediante aliases. El usuario puede corregir el
mapeo con un archivo JSON (--mapping).
"""
import json
import re
import sqlite3
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Perfiles de tablas: roles -> aliases (subcadenas, en orden de prioridad)
# ---------------------------------------------------------------------------
TOKEN_ID_ALIASES = [
    "mint_address", "token_address", "token_mint", "mint", "contract_address",
    "token_id", "contract", "token", "address", "coin",
]

TABLE_PROFILES = {
    "tokens": {
        "name_hints": ["token", "coin", "launch", "pump", "meta", "pool"],
        "key_roles": ["token_id", "token_name", "created_at"],
        "roles": {
            "token_id": TOKEN_ID_ALIASES,
            "token_name": ["token_name", "name", "title"],
            "token_symbol": ["symbol", "ticker", "token_symbol"],
            "created_at": ["created_at", "create_time", "creation", "launched_at",
                            "launch_time", "created", "create_ts", "timestamp", "time", "date"],
            "dev_wallet": ["dev_wallet", "creator", "deployer", "dev_address",
                            "owner", "dev", "creator_address"],
            "twitter": ["twitter", "x_url", "twitter_url", "x_handle"],
            "telegram": ["telegram", "tg", "telegram_url"],
            "website": ["website", "site", "web", "homepage", "url"],
            "graduated": ["graduated", "migrated", "complete", "is_complete",
                           "raydium", "graduation", "bonding_complete"],
            "market_cap": ["usd_market_cap", "market_cap", "mcap", "mc_usd", "fdv"],
            "supply": ["total_supply", "supply", "token_supply"],
            "king_of_hill": ["king_of_hill", "koh"],
            "nsfw": ["nsfw", "is_nsfw"],
        },
    },
    "trades": {
        "name_hints": ["trade", "swap", "tx", "transaction", "fill", "activity",
                        "buysell", "event", "history"],
        "key_roles": ["token_id", "wallet", "ts"],
        "roles": {
            "token_id": TOKEN_ID_ALIASES,
            "wallet": ["wallet", "owner", "user", "trader", "maker", "account",
                        "sender", "buyer", "address", "user_address"],
            "side": ["side", "type", "action", "buy_sell", "direction", "event", "is_buy"],
            "sol_amount": ["sol_amount", "amount_sol", "sol_value", "quote_amount",
                            "amount_quote", "sol", "value_sol", "amount_usd", "usd_amount", "value"],
            "token_amount": ["token_amount", "amount_token", "base_amount",
                              "amount_base", "tokens", "amount"],
            "price": ["price_sol", "price", "unit_price", "token_price", "avg_price"],
            "ts": ["block_time", "timestamp", "created_at", "time", "date", "ts", "created"],
            "tx_sig": ["tx_signature", "signature", "tx_id", "txid", "tx_hash", "hash", "tx", "sig"],
            "slot": ["slot", "block", "block_number"],
        },
    },
    "my_trades": {
        "name_hints": ["my", "position", "portfolio", "journal", "pnl", "trade",
                        "closed", "order"],
        "key_roles": ["token_id", "entry_time", "pnl_or_prices"],
        "roles": {
            "token_id": TOKEN_ID_ALIASES + ["tokenid"],
            "entry_time": ["entry_ts", "entryts", "entry_time", "entrytime", "buy_time", "open_time", "opened_at",
                            "entry_date", "buy_date", "created_at", "created"],
            "entry_price": ["entry_price", "entryprice", "buy_price", "buyprice", "avg_entry", "open_price",
                             "entry", "avg_buy_price", "buy_avg"],
            "exit_time": ["exit_ts", "exitts", "exit_time", "exittime", "sell_time", "close_time", "closed_at",
                           "exit_date", "sell_date"],
            "exit_price": ["exit_price", "exitprice", "sell_price", "sellprice", "avg_exit", "close_price",
                            "exit", "avg_sell_price", "sell_avg"],
            "size_sol": ["bet_usd", "betusd", "size_sol", "amount_sol", "invested", "cost_sol",
                          "principal", "sol_in", "buy_amount", "size", "cost", "amount"],
            "pnl_sol": ["pnl_usd", "pnlusd", "pnl_sol", "pnl", "profit", "realized_pnl", "net_pnl",
                         "gain", "profit_sol", "result"],
            "pnl_pct": ["pnl_pct", "pnlpct", "roi", "return_pct", "pnl_percent", "percent",
                         "roi_pct", "return"],
            "status": ["status", "state", "open", "is_open", "is_closed"],
            "win": ["win", "is_win", "won", "winner"],
            "fees": ["fees", "fee", "gas", "priority_fee", "total_fees"],
        },
    },
    "ohlcv": {
        "name_hints": ["ohlcv", "candle", "kline", "price", "bar", "snapshot"],
        "key_roles": ["token_id", "ts", "close"],
        "roles": {
            "token_id": TOKEN_ID_ALIASES + ["tokenid"],
            "ts": ["timestamp", "time", "ts", "date", "open_time", "bucket"],
            "open": ["open", "o"],
            "high": ["high", "h"],
            "low": ["low", "l"],
            "close": ["close", "price_usd", "priceusd", "price", "c"],
            "volume": ["volume", "vol", "v", "quote_volume"],
        },
    },
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")


def _match_score(col_norm: str, alias: str, role: str = "") -> int:
    """4 = exacto, 3 = exacto sin guiones bajos, 2 = prefijo, 1 = contiene."""
    # Desempate semántico: precio nunca debe matchear columnas temporales (ts, time, date)
    if "price" in role and (col_norm.endswith("ts") or col_norm.endswith("time") or col_norm.endswith("date")):
        return 0
    # Tiempo nunca debe matchear columnas de precio o pnl
    if ("time" in role or role == "ts") and ("price" in col_norm or "pnl" in col_norm or "usd" in col_norm):
        return 0
    # Market cap no debe matchear pumpAthMcap si hay marketcap exacto
    if role == "market_cap" and col_norm.startswith("pump_ath"):
        return 1

    if col_norm == alias:
        return 4
    if col_norm == alias.replace("_", ""):
        return 3
    if len(alias) <= 2:
        return 0
    if col_norm.startswith(alias + "_") or col_norm.startswith(alias.replace("_", "") + "_"):
        return 2
    if alias in col_norm or col_norm in alias:
        return 1
    return 0


@dataclass
class TableInfo:
    name: str
    columns: list
    dtypes: dict
    n_rows: int
    sample: object


@dataclass
class SchemaMap:
    tables: dict = field(default_factory=dict)            # name -> TableInfo
    mapping: dict = field(default_factory=dict)           # table_type -> {"table", "columns": {role: col}}
    notes: list = field(default_factory=list)

    def role(self, table_type: str, role: str):
        m = self.mapping.get(table_type)
        if not m:
            return None
        return m["columns"].get(role)

    def table_of(self, table_type: str):
        m = self.mapping.get(table_type)
        return m["table"] if m else None


# ---------------------------------------------------------------------------
# Inspección
# ---------------------------------------------------------------------------
def inspect_db(db_path: str) -> SchemaMap:
    import pandas as pd

    con = sqlite3.connect(db_path)
    sm = SchemaMap()
    tables = pd.read_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'",
        con,
    )["name"].tolist()
    if not tables:
        raise ValueError(f"La base de datos {db_path} no contiene tablas.")
    for t in tables:
        cols = pd.read_sql(f"PRAGMA table_info('{t}')", con)
        df_sample = pd.read_sql(f"SELECT * FROM '{t}' LIMIT 5", con)
        n_rows = int(pd.read_sql(f"SELECT COUNT(*) AS c FROM '{t}'", con)["c"][0])
        sm.tables[t] = TableInfo(
            name=t,
            columns=cols["name"].tolist(),
            dtypes=dict(zip(cols["name"], cols["type"])),
            n_rows=n_rows,
            sample=df_sample,
        )
    con.close()
    _auto_map(sm)
    return sm


def _map_table_columns(ti: TableInfo, profile: dict) -> dict:
    """Asigna el mejor alias a cada rol del perfil para una tabla dada."""
    out = {}
    used = set()
    cols_norm = {_norm(c): c for c in ti.columns}
    for role, aliases in profile["roles"].items():
        best_col, best_score = None, 0
        for cn, col in cols_norm.items():
            if col in used:
                continue
            for a in aliases:
                s = _match_score(cn, _norm(a), role)
                if s > best_score:
                    best_score, best_col = s, col
                    if s == 4:
                        break
            if best_score == 4:
                break
        if best_col and best_score >= 2 or (best_col and best_score == 1):
            out[role] = best_col
            used.add(best_col)
    return out


def _profile_score(ti: TableInfo, tname: str, ptype: str, profile: dict, colmap: dict) -> float:
    score = 0.0
    hints = profile["name_hints"]
    tn = _norm(tname)
    if any(h in tn for h in hints):
        score += 2
    key = profile["key_roles"]
    if ptype == "my_trades":
        # necesita pnl o (entry+exit) o precios de entrada
        has_pnl = any(r in colmap for r in ("pnl_sol", "pnl_pct", "win"))
        has_entry = "entry_time" in colmap or "entry_price" in colmap
        has_exit = any(r in colmap for r in ("exit_time", "exit_price"))
        if has_pnl and (has_entry or has_exit):
            score += 6
        elif has_entry and has_exit:
            score += 5
        else:
            return -1
        # si además tiene wallet+side como una tabla de flujo, penalizar
        if "side" in colmap and "wallet" in colmap and "sol_amount" in colmap and not has_pnl:
            score -= 3
    elif ptype == "ohlcv":
        if all(r in colmap for r in ("close", "ts")):
            score += 6
        else:
            return -1
    elif ptype == "trades":
        need = sum(1 for r in ("wallet", "side", "sol_amount", "token_amount", "ts") if r in colmap)
        if need >= 3 and ti.n_rows > 0:
            score += 4 + need
        else:
            return -1
        if ti.n_rows > 5000:
            score += 2  # las tablas de flujo suelen ser grandes
    elif ptype == "tokens":
        need = sum(1 for r in ("token_id", "token_name", "token_symbol", "created_at") if r in colmap)
        if "token_id" in colmap and need >= 2:
            score += 4 + need
        else:
            return -1
        if ti.n_rows < 5_000_000:
            score += 1
    # cobertura de roles como desempate
    score += 0.1 * len(colmap)
    return score


def _auto_map(sm: SchemaMap) -> None:
    # Puntuar TODAS las combinaciones (tabla, perfil) y asignar de forma voraz
    # por puntuación global, sin reutilizar tablas ni tipos.
    cands = []
    for tname, ti in sm.tables.items():
        for ptype, profile in TABLE_PROFILES.items():
            colmap = _map_table_columns(ti, profile)
            s = _profile_score(ti, tname, ptype, profile, colmap)
            if s > 0:
                cands.append((s, ptype, tname, colmap))
    cands.sort(key=lambda x: -x[0])
    used_tables, used_types = set(), set()
    for s, ptype, tname, colmap in cands:
        if ptype in used_types or tname in used_tables:
            continue
        used_types.add(ptype)
        used_tables.add(tname)
        sm.mapping[ptype] = {"table": tname, "columns": colmap}
    # notas de diagnóstico
    for ptype in TABLE_PROFILES:
        if ptype not in sm.mapping:
            sm.notes.append(f"No se detectó tabla de tipo '{ptype}' (opcional).")


def apply_mapping_override(sm: SchemaMap, mapping_path: str) -> None:
    """
    JSON de corrección manual. Formato admitido:
    {
      "my_trades": {"table": "posiciones", "columns": {"entry_price": "precio_entrada"}},
      "trades":    {"table": "swaps"}
    }
    """
    with open(mapping_path, "r", encoding="utf-8") as f:
        ov = json.load(f)
    for ptype, spec in ov.items():
        cur = sm.mapping.setdefault(ptype, {"table": None, "columns": {}})
        if "table" in spec and spec["table"]:
            if spec["table"] not in sm.tables:
                raise ValueError(f"mapping.json: la tabla '{spec['table']}' no existe en la base.")
            cur["table"] = spec["table"]
            # re-mapear columnas contra la nueva tabla
            profile = TABLE_PROFILES.get(ptype)
            if profile:
                auto = _map_table_columns(sm.tables[spec["table"]], profile)
                auto.update(spec.get("columns", {}))
                cur["columns"] = auto
        if "columns" in spec and not ("table" in spec and spec["table"]):
            cur["columns"].update(spec["columns"])
    sm.notes.append(f"Se aplicó mapping manual desde {mapping_path}")


def schema_report(sm: SchemaMap) -> str:
    lines = ["=" * 72, "INSPECCIÓN DE LA BASE DE DATOS", "=" * 72]
    for tname, ti in sm.tables.items():
        lines.append(f"\nTabla '{tname}'  ({ti.n_rows:,} filas, {len(ti.columns)} columnas)")
        lines.append("  columnas: " + ", ".join(f"{c} [{ti.dtypes.get(c,'?')}]" for c in ti.columns[:40]))
        if len(ti.columns) > 40:
            lines.append(f"  ... y {len(ti.columns)-40} más")
    lines.append("\n" + "-" * 72)
    lines.append("MAPEO SEMÁNTICO AUTOMÁTICO")
    lines.append("-" * 72)
    for ptype, m in sm.mapping.items():
        lines.append(f"\n[{ptype}] -> tabla '{m['table']}'")
        for role, col in sorted(m["columns"].items()):
            lines.append(f"    {role:<15} = {col}")
    if sm.notes:
        lines.append("\nNotas:")
        for n in sm.notes:
            lines.append(f"  - {n}")
    return "\n".join(lines)
