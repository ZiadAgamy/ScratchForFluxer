<div align="center">

# Scratch For Fluxer

**Build [Fluxer](https://fluxer.app) bots by snapping blocks together.**
Drag blocks like in Scratch, read the real Python they write, then run your bot or download it.

[**Open the site**](https://scratch.fluxer.my) · [**Read the guide**](https://scratch.fluxer.my/docs) · [**Join the community**](https://fluxer.gg/oHy1OzcO) · [**Source code**](https://github.com/ZiadAgamy/ScratchForFluxer)

</div>

![The editor: blocks on the left, the Python they write on the right, and the console of a running bot](docs/screenshot-editor.png)

## What it is

- A **block editor** ([Blockly](https://developers.google.com/blockly)) with 110+ blocks in 11 families: Events, Bot, Messages, Message info, Channels and roles, Threads and forums, Control, Operators, Variables, Lists, and JSON and files.
- Every block writes real [fluxer.py](https://github.com/akarealemil/fluxer.py) code. You can read it, copy it, or download `main.py` and run it anywhere.
- **Log in with Fluxer**, add your bot token, press **Run**, and watch the console.
- A beginner guide at `/docs`, with screenshots and a reference of every block.

Just want to use it? You don't need to install anything: go to **<https://scratch.fluxer.my>**.
The rest of this page is for people who want to **run their own copy** (for example on a self-hosted Fluxer).

## How the pieces fit

```
 browser ──► the website (FastAPI) ──signed, over https──► the runner (small service) ──► one process per bot ──► Fluxer
                  │
                  └──► database (SQLite on your computer, Postgres online): accounts, encrypted tokens, blocks, files
```

| Piece | What it does | Where it can run |
| ----- | ------------ | ---------------- |
| **Website** | The editor, the login, the database, turning blocks into Python | Your computer, a VPS, or Vercel |
| **Runner** | Keeps bots connected to Fluxer, one process per bot | Any machine that can keep a Docker container running. Not Vercel |

On your own computer the website runs the bots itself, so you don't need a separate runner to try it out.

---

## 1. Try it on your computer (5 minutes)

You need **Python 3.10 or newer** and **Git**.

```bash
git clone https://github.com/ZiadAgamy/ScratchForFluxer.git
cd ScratchForFluxer
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
```

Open `.env` and fill in `FLUXER_CLIENT_ID` and `FLUXER_CLIENT_SECRET` (next step), then:

```bash
python -m scratch_fluxer check     # checks your settings and prints the address to give Fluxer
python -m scratch_fluxer           # starts the site
```

Open **<http://localhost:8000>**. Use `localhost`, not `127.0.0.1`: it must match `APP_BASE_URL` exactly or the login can't find its cookie.

## 2. Create the Fluxer application (for the login)

"Log in with Fluxer" needs an application in your Fluxer, with your site's address on its redirect list.

1. In Fluxer, open the developer area and **create an application**. Copy its **ID** and its **client secret**.
2. Put them in your settings: the ID is `FLUXER_CLIENT_ID`, the secret is `FLUXER_CLIENT_SECRET`. The secret is private: never put it in Git, chat or code.
3. Add the **redirect URI** to the application. It is your site's address plus `/auth/callback`, written exactly (same `http`/`https`, no slash at the end):

   | Where you run it | Redirect URI |
   | ---------------- | ------------ |
   | Your computer | `http://localhost:8000/auth/callback` |
   | Online | `https://your-domain.example/auth/callback` |

The site only asks Fluxer for the `identify` scope (your ID, name and avatar). It never sees your password, email or messages.

## 3. Use your own (self-hosted) Fluxer

If you run your own Fluxer instance ([Fluxer's self-hosting guide](https://docs.fluxer.app/self-hosting)), tell the site where it is. By default everything points to the public Fluxer.

1. Open `https://YOUR-FLUXER-HOST/.well-known/fluxer`. It lists the addresses of your instance (API, web app, media, OAuth2).
2. Put them in your settings (`.env` or your host's environment variables):

   ```env
   FLUXER_API_URL=https://chat.example.com/api/v1
   FLUXER_WEB_URL=https://chat.example.com
   FLUXER_MEDIA_URL=https://chat.example.com/media
   FLUXER_STATIC_URL=https://chat.example.com/static

   FLUXER_OAUTH_AUTHORIZE_URL=https://chat.example.com/api/v1/oauth2/authorize
   FLUXER_OAUTH_TOKEN_URL=https://chat.example.com/api/v1/oauth2/token
   FLUXER_OAUTH_USERINFO_URL=https://chat.example.com/api/v1/oauth2/userinfo
   FLUXER_OAUTH_REVOKE_URL=https://chat.example.com/api/v1/oauth2/token/revoke
   ```

   The values above are an **example shape only**. Copy the real addresses from your `/.well-known/fluxer` page.
3. Set `FLUXER_API_URL` on the **runner** too, so your bots connect to your instance and not the public one.
4. Create the application (step 2) **on your own Fluxer**, not on the public one.

> The self-hosted setup follows Fluxer's documentation, but hasn't been tested against a real self-hosted instance yet. If a step doesn't match what you see, open an issue.

## 4. Put it online

You need a **domain**, a **Postgres database**, and (to run bots) a **runner**.

### The website

Pick one:

**A) Vercel (easiest).**
1. Fork or push this repository to your GitHub.
2. On [vercel.com](https://vercel.com), **Add New → Project** and import it. Vercel finds the FastAPI app on its own.
3. In the project's **Storage**, add a **Neon** (Postgres) database and connect it. This sets `DATABASE_URL` for you.
4. Add the environment variables below, then **Redeploy**.

**B) Your own server.** Install Python 3.12, run `pip install -r requirements.txt`, set `APP_ENV=production`, and start it with `uvicorn index:app --host 0.0.0.0 --port 8000` behind a reverse proxy with https (Caddy or nginx). Set `TRUST_PROXY=1` when you do.

**The settings the site needs**

| Name | Value |
| ---- | ----- |
| `SECRET_KEY` | A long random string: `python -c "import secrets; print(secrets.token_urlsafe(48))"`. It encrypts bot tokens, so keep it safe |
| `APP_BASE_URL` | Your site's address, no slash at the end, e.g. `https://scratch.example.com` |
| `FLUXER_CLIENT_ID` | From your Fluxer application |
| `FLUXER_CLIENT_SECRET` | From your Fluxer application |
| `DATABASE_URL` | Your Postgres address (set for you by Neon on Vercel) |
| `RUNNER_URL` | The https address of your runner (next section) |
| `RUNNER_SECRET` | A random string of at least 32 characters, the same on the runner |

Then add `https://your-domain/auth/callback` to your Fluxer application's redirect URIs. Open the site at that exact address when you log in.

Until something is missing, the site shows an **"Almost ready"** page that lists exactly what to fix. `/healthz` always answers.

### The runner

Bots must stay connected all the time, so they run on a separate small service, not on Vercel.

```bash
export RUNNER_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"   # keep it, you need it twice
docker compose -f deploy/runner/docker-compose.yml up -d --build
curl http://127.0.0.1:8080/healthz        # {"ok":true,"running":0,...}
```

Put https in front of port 8080 (a reverse proxy such as Caddy, or a tunnel), then give the website the same two values: `RUNNER_URL` and `RUNNER_SECRET`. The full guide is in [deploy/runner/README.md](deploy/runner/README.md).

Without a runner everything works except the **Run** button. Downloading `main.py` always works.

## Settings you may want to change

Everything is an environment variable. Copy [`.env.example`](.env.example), which explains each one, and run `python -m scratch_fluxer check` to look yours over.

| Name | Default | What it does |
| ---- | ------- | ------------ |
| `RUNNER_MAX_RUN_SECONDS` | 21600 (6 h) | A bot is stopped after this long. Raise it for longer runs |
| `RUNNER_MAX_CONCURRENT` | 8 | Bots running at once |
| `RUNNER_MAX_PER_USER` | 2 | Bots one person may run at once |
| `RUNNER_MEMORY_MB` | 384 | Memory one bot may use |
| `MAX_BOTS_PER_USER` | 10 | Bots one person can add |
| `CONTACT_URL` | none | Shown on the privacy page (`mailto:` or a link) |
| `COMMUNITY_URL` | this project's server | The community link in the footer. Empty hides it |

## Something wrong?

| You see | What to do |
| ------- | ---------- |
| An error from **Fluxer** when you log in | The redirect URI isn't on the application's list, or isn't exactly the same. Run `python -m scratch_fluxer check` and copy the address it prints |
| "Your login session expired or didn't match" | Open the site at exactly the address in `APP_BASE_URL` (`localhost`, not `127.0.0.1`) and allow cookies |
| "Fluxer didn't accept the login" | The client ID or secret is wrong. If they are right, try `FLUXER_OAUTH_PKCE=false` |
| **"Almost ready"** page | A setting is missing. The page names it |
| "Running bots isn't switched on" | Set `RUNNER_URL` and `RUNNER_SECRET` (32+ characters), or run locally |
| "The runner no longer has this run" | The runner restarted, which stops every bot. Press Run again |
| "The saved token can't be read any more" | `SECRET_KEY` was changed. Put the old key in `SECRET_KEY_PREVIOUS`, or paste the token again |
| Everyone looks like one visitor / "Too many requests" | Behind a reverse proxy, set `TRUST_PROXY=1` |

## Security in short

- Bot tokens are **encrypted** before they are saved, and are never sent back to the browser.
- Blocks are **not code**. The generated Python is checked against an allow-list twice, before it is shown and before a bot starts.
- Every bot runs in **its own process** with limits on memory, time, files and output.
- **Delete my data** on the My bots page removes the account, bots, tokens and files.

The long version, and what you must do yourself (like network rules for the runner), is in [deploy/runner/README.md](deploy/runner/README.md). Please read it before you let other people run bots on your machine.

## Working on the project

```bash
pip install -r requirements-dev.txt
playwright install chromium        # only for the browser tests
pytest -m "not browser and not e2e"   # the quick tests
ruff check . && ruff format --check .
```

- `scratch_fluxer/` the site. `catalogue.py` and `compiler.py` turn blocks into Python, `codecheck.py` is the allow-list, `runner/` runs bots
- `public/static/` the front end (the editor is in `js/editor/`)
- `deploy/runner/` the runner's Dockerfile, compose file and guide
- `tests/` the tests, plus a pretend Fluxer to test against

A block is defined once in `catalogue.py`, and the compiler, the editor, the landing page, the `/docs` reference and the tests all read it. A new block is mostly one entry there plus the code it writes in `compiler.py`.

## Links

| | |
| --- | --- |
| Live site | <https://scratch.fluxer.my> |
| Guide for beginners | <https://scratch.fluxer.my/docs> |
| Source code | <https://github.com/ZiadAgamy/ScratchForFluxer> |
| Community server | <https://fluxer.gg/oHy1OzcO> |
| Fluxer | <https://fluxer.app> |
| Fluxer docs | <https://docs.fluxer.app> |
| Self-hosting Fluxer | <https://docs.fluxer.app/self-hosting> |
| fluxer.py (the bot library) | <https://github.com/akarealemil/fluxer.py> |
| Blockly | <https://github.com/google/blockly> |
| Phosphor icons | <https://phosphoricons.com> |

## License

[MIT](LICENSE): you can use, copy, change and share it, as long as the license text stays with it. Third-party licenses are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Fluxer and its name belong to their owners.
