# pumpfun-research

Python utilities for analysing a Solana wallet's pump.fun trades and Raydium Launchpad launches. The tool batches Helius-enhanced transaction data, classifies BUY/SELL flows, and aggregates per-mint performance metrics with optional metadata enrichment.

## Features
- Batch pulls pump.fun BUY/SELL activity for a wallet and computes PnL, delay-to-first-sell, and summary stats.
- Raydium Launchpad detection that combines instruction-program fingerprints with log keywords to flag launches while minimising RPC calls.
- Optional "pump.fun + Raydium" mode that unions both sets of mints in a single run.
- CSV export with human-readable timestamps plus optional full mint transaction logs.
- Mode-aware caching (`.cache/<wallet>_{pumpfun|pumpfun_plus_raydium|raydium_launchpad}`) with automatic resume, program-ID persistence, and parallelised log scans for fast re-runs.

## Requirements
- Python 3.10+
- A Solana RPC endpoint (`RPC_URL`)
- Strongly recommended: a Helius API key (`HELIUS_API_KEY`) for enhanced decoding and DAS metadata

Install dependencies with:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuration
Populate the supplied `.env` (or environment variables) with your connection details:

```bash
HELIUS_API_KEY=your_helius_key   # optional but speeds up decoding/metadata
RPC_URL=https://your.solana.rpc  # required fallback RPC
```

The script loads `.env` automatically on startup.

## Usage
### Basic pump.fun scan
```bash
python pumpfun_research.py --wallet <WALLET_ADDRESS> [--limit 25000] [--days 7] [--max-mints 50] [--fetch-metadata]
```
Key flags:
- `--wallet` *(required)*: Base58 wallet to analyse.
- `--limit`: Maximum signatures to fetch (default 25,000).
- `--days`: Restrict history to the last *N* days.
- `--max-mints`: Stop after analysing *N* token mints (fast tests).
- `--fetch-metadata`: Pull token `name` / `symbol` from Helius DAS (slower).
- `--helius-workers`: Concurrent Helius batch requests (defaults to up to 4 based on CPU count). Increase to speed up large runs; set to `1` to disable parallel decoding if you hit rate limits.

### Raydium Launchpad mode
```bash
python pumpfun_research.py --wallet <WALLET_ADDRESS> --raydium-launchpad
```
The Raydium filter looks for launch-related log keywords (`raydium launchpad`, `acceleraytor`, etc.) plus known launch/trigger program IDs. Instruction fingerprints are cached per signature, so subsequent runs can usually skip extra RPC calls. Default heuristics are defined inside `pumpfun_research.py` (`RAYDIUM_LAUNCHPAD_*` constants) and can be edited directly if you have better identifiers.

### Combined pump.fun + Raydium run
```bash
python pumpfun_research.py --wallet <WALLET_ADDRESS> --include-raydium-launchpad
```
This mode keeps pump.fun mints and any Raydium Launchpad coins detected for the wallet in one CSV. The summary footer reports how many of each were found.

### Export full mint transaction logs
Add `--export-mint-txs` to persist every decoded token transfer touching each mint. By default the exporter scans all signatures; constrain it with `--mint-tx-limit <N>` or redirect output with `--mint-tx-output-dir`.

```bash
python pumpfun_research.py \
  --wallet <WALLET_ADDRESS> \
  --include-raydium-launchpad \
  --export-mint-txs \
  --mint-tx-limit 0 \
  --helius-workers 4 \
  --days 420
```
The command above scans roughly 420 days of history, unions pump.fun and Raydium mints, and saves per-mint CSVs under `mint_txs/<wallet>/`. Use `--mint-tx-limit 0` for “no limit” (scan all signatures); omit the flag to export without limiting, or set a positive number to cap per-mint history. Passing a negative limit disables export even if `--export-mint-txs` is present.

### Caching behaviour
- Cache folders are mode-specific: `.cache/<wallet>_pumpfun`, `.cache/<wallet>_pumpfun_plus_raydium`, or `.cache/<wallet>_raydium_launchpad`.
- Each cache contains `signatures.json`, decoded Helius batches, the aggregated token events, plus `signature_programs.json` (instruction IDs) for fast launchpad filtering.
- If a matching cache already exists the script auto-resumes; you can point at a custom location via `--cache-dir` or `--resume-cache`.
- Legacy caches without the suffix are detected automatically—you can rename them yourself or let the tool reuse them on the next run.

## Output
The script prints summary statistics to stdout and writes a CSV per run:
- `wallet_pumpfun.csv` for pump.fun-only scans
- `wallet_pumpfun_plus_raydium.csv` when `--include-raydium-launchpad` is supplied
- `wallet_raydium_launchpad.csv` when `--raydium-launchpad` is supplied

CSV columns include mint address, optional name/symbol, first buy/sell timestamps, buy amounts, net profit in SOL, and more.

When `--export-mint-txs` is enabled, an additional CSV is produced per mint under `mint_txs/<wallet>/`, capturing from/to users, token amounts, and SOL movements for every transfer involving that mint.

## Customising Raydium detection
Edit the following constants in `pumpfun_research.py` to suit your heuristics:
- `RAYDIUM_LAUNCHPAD_KEYWORDS`
- `RAYDIUM_LAUNCHPAD_PROGRAM_IDS`
- `RAYDIUM_LAUNCHPAD_TRIGGER_PROGRAM_IDS`

Keywords are lowercase substrings matched against transaction logs. Program ID sets can be extended with authoritative IDs when available.

## Development notes
- Keep the virtual environment and `.env` out of version control (already listed in `.gitignore`).
- Preferred formatting is standard `black`/`ruff` tooling, though no formatter is enforced.
- Use `PYTHONPYCACHEPREFIX=.pycache python3 -m compileall pumpfun_research.py` to do a quick syntax check.

## License
MIT (unless stated otherwise in individual files).
