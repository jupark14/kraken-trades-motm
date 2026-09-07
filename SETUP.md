# Setting up Kraken Trades with Claude Code

This guide is for Money on the Move members who have never opened a terminal
before. Go slowly, do the steps in order, and you will end up with a folder on
your computer where Claude Code can prepare Kraken orders for you and you press
the button yourself. Plan on about thirty minutes.

## What you need before you start

- A Kraken account that is verified and funded. The Crypto Setup course on
  Patreon covers opening one.
- A Claude plan that includes Claude Code: Pro, Max, Team, or Enterprise. The
  free Claude plan does not include it.
- A Mac or a Windows PC. Every command below is written for Mac first, with the
  Windows difference noted where there is one.

## Step 1. Open a terminal

Mac: press Command and Space, type `Terminal`, press Return.

Windows: press the Windows key, type `PowerShell`, press Enter.

Everything that looks like this is something you type into that window:

```bash
echo hello
```

## Step 2. Check that Python is installed

```bash
python3 --version
```

You want to see `Python 3.9` or higher. If the Mac says the command is not
found, install the Apple developer tools with `xcode-select --install`, or
download Python from https://www.python.org/downloads/ and run the installer.

Windows: download Python from https://www.python.org/downloads/ and run the
installer. On the first screen tick the box that says **Add Python to PATH**
before clicking Install. On Windows the commands are `python` and `pip`
instead of `python3` and `pip3`. Everywhere this guide says `python3`, type
`python`.

## Step 3. Install Claude Code

Mac, paste this into the terminal:

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

Windows, paste this into PowerShell:

```powershell
irm https://claude.ai/install.ps1 | iex
```

Close the terminal window and open a new one so it picks up the new command.
Then type `claude` and press Return. The first run opens your browser to log in
with your Claude account. Once it says you are logged in, type `/exit` to leave
for now.

The official install page is https://code.claude.com/docs/en/setup.md if you
get stuck.

## Step 4. Put this folder on your computer

The easiest way is to download it. On the GitHub page for this project, click
the green **Code** button, then **Download ZIP**. Unzip it and move the folder
somewhere sensible, such as your Documents folder. Rename it to
`kraken-trades-motm` if it has a longer name.

If you already use git, this works too:

```bash
git clone https://github.com/moneyotm/kraken-trades-motm.git
```

Now move the terminal into that folder. Type `cd ` with a space after it, then
drag the folder from Finder (or File Explorer) into the terminal window, and
press Return. You should see something like this:

```bash
cd ~/Documents/kraken-trades-motm
```

Check you are in the right place:

```bash
ls
```

You should see `kraken.py`, `README.md`, `SETUP.md`, and a few other files.

## Step 5. Install the two Python libraries the tool uses

```bash
pip3 install -r requirements.txt
```

It installs `krakenex` (talks to Kraken) and `python-dotenv` (reads your `.env`
file). If the Mac prints a long warning mentioning `NotOpenSSLWarning` or
`LibreSSL`, ignore it. It is harmless.

## Step 6. Create your Kraken API key

An API key is a password that lets a program act on your Kraken account. We
make one with the smallest set of permissions that still lets it place orders.

1. Sign in to Kraken in your browser and go to https://www.kraken.com/u/security/api
2. Click **Create API key** (or **Add key**).
3. Give it a name you will recognize later, such as `kraken-trades`.
4. Under permissions, turn **on** only these:
   - Query funds (sometimes called "Query funds and balances")
   - Query open orders and trades
   - Query closed orders and trades
   - Create and modify orders
   - Cancel and close orders
5. Leave **off** everything to do with withdrawals, deposits, transfers,
   staking, earn, or exporting data. If the tool cannot withdraw, a stolen key
   cannot withdraw either.
6. Optional but recommended: set an expiry date a few months out. You can
   always make a new key.
7. Click **Generate key**.

Kraken now shows you two long strings: the **API Key** and the **Private Key**.
Copy both somewhere safe right now, such as a password manager. Kraken will
never show you the Private Key again. If you lose it, delete the key and make a
new one.

## Step 7. Put the keys in your .env file

The tool reads your keys from a file called `.env` in this folder. That file
does not exist yet. Create it by copying the example:

Mac:

```bash
cp .env.example .env
```

Windows:

```powershell
copy .env.example .env
```

Now open it in a text editor. Files that start with a dot are hidden in
Finder, so open it from the terminal:

Mac:

```bash
open -e .env
```

Windows:

```powershell
notepad .env
```

You will see lines like these:

```
KRAKEN_API_KEY=paste_your_api_key_here
KRAKEN_API_SECRET=paste_your_private_key_here
```

Replace `paste_your_api_key_here` with your API Key and
`paste_your_private_key_here` with your Private Key. No spaces around the
equals sign, no quotation marks. Leave the `_2` lines alone unless you are
setting up a second account (Step 10). Save and close the editor.

On a Mac, lock the file so only your user can read it:

```bash
chmod 600 .env
```

Three rules about this file:

- Never paste your keys into a Claude Code chat, a screenshot, a Discord
  message, or an email. Claude never needs to see them. It only needs them to
  be in the file.
- Never send anyone your `.env` file.
- If a key ever leaks, go back to Kraken, delete that key, and make a new one.
  Then update `.env`.

## Step 8. Test it

First a public command that needs no key at all:

```bash
python3 kraken.py ticker XBTUSD SOLUSD
```

You should see current prices. Now a private command that uses your key:

```bash
python3 kraken.py balance --account main
```

You should see a line that says `Account: main` followed by a table of your
balances. If you see an error instead, look at the troubleshooting section at
the bottom of this page.

## Step 9. Open Claude Code in this folder

Make sure the terminal is still inside the folder (run `ls` and check you see
`kraken.py`), then:

```bash
claude
```

Claude Code reads the `CLAUDE.md` file in this folder automatically, so it
already knows the rules: it prepares and checks orders, and you place them.

Claude will ask your permission before it runs each command. Read what it wants
to run, then say yes. That prompt is a feature, not a nuisance.

Things to try, in plain English:

> Show me my balance on main.

> What is the minimum order size for LUNAUSD?

> Set up a buy ladder for 10 SOL between $95 and $75, 5 levels, post-only, on
> main. Dry-run it and show me the output.

Claude will write the order file into `orders/`, run the dry-run, show you the
preflight and Kraken validation output, and then give you a command that ends
in `--live`. That command is yours. Type `/exit` to leave Claude, or open a
second terminal window in the same folder, paste the command, read the list of
orders one more time, and type `CONFIRM`.

After it runs, go back to Claude and say:

> I placed them. Check open orders on main.

## Step 10 (optional). A second Kraken account

The tool supports two accounts out of the box. Make a second API key on the
second account exactly as in Step 6, then fill in the two `_2` lines in `.env`:

```
KRAKEN_API_KEY_2=...
KRAKEN_API_SECRET_2=...
```

Use it with `--account second`. If you would rather call it something else,
open `accounts.json` and change the word `second` to whatever you like, such as
`roth` or `spouse`. Keep the name short, lowercase, and with no spaces.

## The rules, one more time

- Claude never runs `--live`. You do, and you type `CONFIRM` yourself.
- Every order file gets a dry-run first. If the dry-run fails, fix the file and
  dry-run it again before anything goes live.
- Your API key never has withdrawal permission.
- Your `.env` file never leaves your computer and never goes into a chat.
- `split` never touches your account. It only writes a file.
- A shared move from anyone in the community is information, not a decision.
  Run every idea through your own allocation, sizing, plan, and prayer before
  it becomes an order file.

## Troubleshooting

| What you see | What it means | What to do |
|--------------|---------------|------------|
| `command not found: python3` | Python is not installed, or on Windows it is called `python` | Do Step 2 |
| `No module named krakenex` | The libraries are not installed | Do Step 5 |
| `Account 'main' needs KRAKEN_API_KEY and KRAKEN_API_SECRET set in .env` | No `.env` file, or the placeholders are still in it, or you are in the wrong folder | Do Step 7, then `ls` to check the folder |
| `EAPI:Invalid key` | The key or secret was pasted wrong, or the two are swapped | Reopen `.env` and paste again. API Key on the KEY line, Private Key on the SECRET line |
| `EGeneral:Permission denied` | The key is missing a permission | Edit the key on Kraken and turn on the permissions listed in Step 6 |
| `EAPI:Invalid nonce` | Two programs used the key at the same instant | Wait a few seconds and run it again |
| `EOrder:Insufficient funds` | Not enough balance for the order | Check `balance` and shrink the order |
| `Account mismatch` | The order file says one account and `--account` says another | Regenerate the file for the right account |
| `NotOpenSSLWarning` on a Mac | A harmless Python warning | Ignore it |
| Claude says it will not run `--live` | It is doing its job | Run the command yourself |

## When you are done with the tool

If you stop using it, go to https://www.kraken.com/u/security/api and delete
the key. You can always make a new one later.

*This is not financial advice. Always take everything back to the Lord in prayer for personal confirmation.*
