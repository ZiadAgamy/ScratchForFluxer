<div align="center">

# Scratch For Fluxer

**Build [Fluxer](https://fluxer.app) bots by snapping blocks together, then see the Python behind every block.**
Build in your browser, then run your bot or download the Python file.

[**Open the site**](https://scratch.fluxer.my) · [**Read the guide**](https://scratch.fluxer.my/docs) · [**Join the community**](https://fluxer.gg/oHy1OzcO) · [**Source code**](https://github.com/ZiadAgamy/ScratchForFluxer)

</div>

![The editor: blocks on the left, the Python they write on the right, and the console of a running bot](docs/screenshot-editor.png)

## What you can do

- Build bots in a **block editor** powered by [Blockly](https://developers.google.com/blockly). There are 110+ blocks across 11 groups, from messages and roles to variables, lists, and JSON files.
- Every block maps to real [fluxer.py](https://github.com/akarealemil/fluxer.py) code. Read it in the editor, copy it, or download `main.py` and run it yourself.
- Sign in with Fluxer, add your bot token, press **Run**, and follow the bot’s output in the console.
- Use the guide at `/docs` for screenshots and a reference for every block.

You can start using the hosted version right away at **<https://scratch.fluxer.my>**. No installation needed. The rest of this guide is for anyone who wants to run their own copy, whether locally or alongside a self-hosted Fluxer.

## How it fits together

```
 browser ──► the website (FastAPI) ──signed, over https──► the runner (small service) ──► one process per bot ──► Fluxer
                  │
                  └──► database (SQLite on your computer, Postgres online): accounts, encrypted tokens, blocks, files
```

| Piece | What it does | Where it can run |
| ----- | ------------ | ---------------- |
| **Website** | The editor, the login, the database, turning blocks into Python | Your computer, a VPS, or Vercel |
| **Runner** | Keeps bots connected to Fluxer, one process per bot | Any machine that can keep a Docker container running. Not Vercel |

When you run the site on your computer, it can run bots itself. You only need a separate runner for a hosted setup.

---

## 1. Run it locally

You’ll need **Python 3.10 or newer** and **Git**.

```bash
git clone https://github.com/ZiadAgamy/ScratchForFluxer.git
cd ScratchForFluxer
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
```

Open `.env` and add `FLUXER_CLIENT_ID` and `FLUXER_CLIENT_SECRET` as described in the next section. Then run:

```bash
python -m scratch_fluxer check     # checks your settings and prints the address to give Fluxer
python -m scratch_fluxer           # starts the site
```

Open **<http://localhost:8000>**. Use `localhost`, not `127.0.0.1`: it must match `APP_BASE_URL` exactly or the login can't find its cookie.

## 2. Set up Fluxer login

To sign in with Fluxer, create an application and add your site’s callback address to its redirect list.

1. In Fluxer, open the developer area and **create an application**. Copy its **ID** and its **client secret**.
2. Put them in your settings: the ID is `FLUXER_CLIENT_ID`, the secret is `FLUXER_CLIENT_SECRET`. Keep the client secret private. Don’t commit it or paste it into chat or source code.
3. Add the **redirect URI** to the application. It is your site's address plus `/auth/callback`, written exactly (same `http`/`https`, no slash at the end):

   | Where you run it | Redirect URI |
   | ---------------- | ------------ |
   | Your computer | `http://localhost:8000/auth/callback` |
   | Online | `https://your-domain.example/auth/callback` |

The site only requests the `identify` scope, which provides your ID, name, and avatar. It never gets your password, email, or messages.

## 3. Connect a self-hosted Fluxer

If you host Fluxer yourself, the site needs your instance’s service URLs. By default, it points to the public Fluxer. See [Fluxer’s self-hosting guide](https://docs.fluxer.app/self-hosting) for the server setup.

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

   These values show the expected format. Use the actual addresses listed on your `/.well-known/fluxer` page.
3. Set `FLUXER_API_URL` on the **runner** too, so your bots connect to your instance and not the public one.
4. Create the application (step 2) **on your own Fluxer**, not on the public one.

> This configuration follows Fluxer’s documentation, but it hasn’t been tested against a live self-hosted instance yet. Please open an issue if a step doesn’t match your setup.

## 4. Deploy it

For a hosted setup, you’ll need a **domain**, a **Postgres database**, and a **runner** if you want to run bots from the editor.

### The website

Pick one:

**A) Vercel (easiest).**
1. Fork or push this repository to your GitHub.
2. On [vercel.com](https://vercel.com), **Add New → Project** and import it. Vercel finds the FastAPI app on its own.
3. In the project's **Storage**, add a **Neon** (Postgres) database and connect it. This sets `DATABASE_URL` for you.
4. Add the environment variables below, then **Redeploy**.

**B) Your own server.** Install Python 3.12, run `pip install -r requirements.txt`, set `APP_ENV=production`, and start it with `uvicorn index:app --host 0.0.0.0 --port 8000` behind a reverse proxy with https (Caddy or nginx). Set `TRUST_PROXY=1` when you do.

**Website environment variables**

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

If a required setting is missing, the site shows an **“Almost ready”** page and tells you what needs fixing. `/healthz` remains available for health checks.

### The runner

Bots need a process that can stay online, so hosted bots run on a small separate service instead of Vercel.

```bash
export RUNNER_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"   # keep it, you need it twice
docker compose -f deploy/runner/docker-compose.yml up -d --build
curl http://127.0.0.1:8080/healthz        # {"ok":true,"running":0,...}
```

Put https in front of port 8080 (a reverse proxy such as Caddy, or a tunnel), then give the website the same two values: `RUNNER_URL` and `RUNNER_SECRET`. The full guide is in [deploy/runner/README.md](deploy/runner/README.md).

Without a runner, you can still edit, save, and download bots; only the **Run** button is unavailable.

## Settings you can tune

The settings are environment variables. Use [`.env.example`](.env.example) as a reference, then run `python -m scratch_fluxer check` to see whether your setup is ready.

| Name | Default | What it does |
| ---- | ------- | ------------ |
| `RUNNER_MAX_RUN_SECONDS` | 21600 (6 h) | A bot is stopped after this long. Raise it for longer runs |
| `RUNNER_MAX_CONCURRENT` | 8 | Bots running at once |
| `RUNNER_MAX_PER_USER` | 2 | Bots one person may run at once |
| `RUNNER_MEMORY_MB` | 384 | Memory one bot may use |
| `MAX_BOTS_PER_USER` | 10 | Bots one person can add |
| `CONTACT_URL` | none | Shown on the privacy page (`mailto:` or a link) |
| `COMMUNITY_URL` | this project's server | The community link in the footer. Empty hides it |

## Troubleshooting

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

## Security basics

- Bot tokens are **encrypted** before they are saved, and are never sent back to the browser.
- Blocks are **not code**. The generated Python is checked against an allow-list twice, before it is shown and before a bot starts.
- Every bot runs in **its own process** with limits on memory, time, files and output.
- **Delete my data** on the My bots page removes the account, bots, tokens and files.

The [runner guide](deploy/runner/README.md) covers the details, including network rules that you must configure yourself. Read it before allowing other people to run bots on your machine.

## Developing locally

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

Each block is described once in `catalogue.py`. The editor, compiler, landing page, `/docs` reference, and tests all use that definition. Adding a block usually means adding its entry there and teaching `compiler.py` which Python to generate.

## Useful links

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

Fluxer and its name belong to their respective owners. Scratch For Fluxer is an independent tool for building Fluxer bots and is not affiliated with Fluxer Platform AB.
