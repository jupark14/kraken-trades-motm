# Kraken Trades

A Money on the Move community tool.

A small command-line toolkit for placing orders on Kraken from a plain text
order file, with Claude Code doing the preparation and you doing the pressing
of the button. It handles one or two Kraken accounts.

Everything is **dry-run by default**. `--live` shows the full order list and
requires typing `CONFIRM`. Your API keys need order permissions only. No
withdrawal permissions, ever.

New here? Start with [SETUP.md](SETUP.md). Open this folder in the Claude
desktop app's Code tab, paste one prompt, and Claude does most of the setup.
The only part you do by hand is creating the Kraken API key.

## How it fits together

| Piece | What it does |
|-------|--------------|
| `kraken.py` | The whole tool. One file. |
| `accounts.json` | Names your accounts (`main`, and optionally `second`) and says which `.env` variables hold their keys. |
| `.env` | Your API keys. Created by you from `.env.example`. Never committed, never shared. |
| `orders/` | Order files you generate. Each one is a JSON list of orders for one account. |
| `orders/examples/` | Three sample order files so you can see the format. |
| `logs/trade_log.jsonl` | Append-only history of every live order and cancel this tool has made. Created automatically. |
| `CLAUDE.md` | The rules Claude Code follows in this folder. Read it once. |

## Setup in short

```bash
pip3 install -r requirements.txt
cp .env.example .env     # then paste in your keys with a text editor
chmod 600 .env
```

Full instructions, including how to create the Kraken key, are in
[SETUP.md](SETUP.md).

## Everyday commands

The account name comes from `accounts.json`. The default file names it `main`.

```bash
python3 kraken.py balance --account main
python3 kraken.py ticker XBTUSD SOLUSD LUNAUSD
python3 kraken.py pair-info SOLUSD
python3 kraken.py open-orders --account main
python3 kraken.py closed-orders --account main --count 10
python3 kraken.py trades --account main
```

`ticker` and `pair-info` are public and work without any key. Kraken names
LUNC as `LUNAUSD`, Bitcoin as `XBTUSD`.

## Placing orders

Orders live in JSON files under `orders/` (all numbers are **strings**). Generate
one with `split`, or copy an example from `orders/examples/` and edit it.

```bash
# 1. Generate: buy 10 SOL across 5 resting limit orders from $95 down to $75
python3 kraken.py split ladder --pair SOLUSD --side buy --total-volume 10 \
    --levels 5 --price-start 95 --price-end 75 --oflags post \
    --account main -o orders/2026-09-07_main_SOLUSD_buy-ladder.json

# 2. Dry-run: local preflight plus Kraken's own validate=true check. Places nothing.
python3 kraken.py place orders/2026-09-07_main_SOLUSD_buy-ladder.json --account main

# 3. Live: only after the dry-run passes. Asks you to type CONFIRM.
python3 kraken.py place orders/2026-09-07_main_SOLUSD_buy-ladder.json --account main --live
```

Other generators:

```bash
# Take-profit ladder: sell 1,000,000 LUNC at three trigger prices,
# each with a limit 3% below its trigger
python3 kraken.py split ladder --pair LUNAUSD --side sell \
    --ordertype take-profit-limit --total-volume 1000000 \
    --prices 0.0001,0.00015,0.0002 --limit-offset=-3% --trigger last \
    --account main -o orders/2026-09-07_main_LUNAUSD_take-profit-ladder.json

# Iceberg: one limit order that only shows part of its size on the book
python3 kraken.py split iceberg --pair XBTUSD --side buy --price 75000 \
    --total-volume 0.5 --display 0.05 --oflags post \
    --account main -o orders/2026-09-07_main_XBTUSD_iceberg.json

# Chunks: split one big limit order into N equal orders
python3 kraken.py split chunk --pair XRPUSD --side buy --price 1.20 \
    --total-volume 1000 --chunks 4 \
    --account main -o orders/2026-09-07_main_XRPUSD_chunks.json
```

`split` never touches your account. It only reads public pair specs and writes
a file. The table it prints must end in `exact`. If it ever says `MISMATCH`,
stop and ask.

## Cancelling

```bash
python3 kraken.py cancel OABC12-XXXXX-YYYYYY --account main   # shows the order, asks y/N
python3 kraken.py cancel-all --account main                   # asks for CONFIRM
```

## Safety design

- `--account` is required on every private command. There is no default account,
  so a command can never quietly hit the wrong one.
- Order files embed their target account. `place` refuses if it does not match
  `--account`.
- Dry-run means local checks (balance, minimums, decimal precision) **plus**
  Kraken's server-side `validate=true` for every single order.
- `--live` prints every order again and waits for you to type `CONFIRM`.
- Every live order and cancel is appended to `logs/trade_log.jsonl`.
- Unknown keys in an order file abort the run. A typo like `pricee` is never
  silently dropped on the way to Kraken.
- Your `.env`, your `logs/`, and your own `orders/*.json` are all in
  `.gitignore`, so they stay on your machine even if you push this folder
  somewhere.

## A note on trailing stops

Kraken's `trailing-stop-limit` has no activation price. It starts trailing
from the current market price the moment it is placed. For an exit set far
above the market that is functionally the same as "activate up there, then
trail", because the high-water mark will be near your target by the time
price gets there. The tradeoff is that a spike-and-reverse before your target
can trigger it early. Claude will point this out whenever it is relevant.

## Working with Claude Code

`CLAUDE.md` defines the workflow. Claude prepares order files and runs the
dry-run. **You** run every `--live` command yourself. Open Claude Code inside
this folder and talk to it in plain English:

> Check my balance on main, then set up a buy ladder for 10 SOL between $95
> and $75 in 5 levels, post-only.

Claude will generate the file, dry-run it, show you the output, and hand you
the `--live` command to run.

## License

MIT. See [LICENSE](LICENSE). The software is provided as is, with no warranty.
You are responsible for every order you place.

*This is not financial advice. Always take everything back to the Lord in prayer for personal confirmation.*
