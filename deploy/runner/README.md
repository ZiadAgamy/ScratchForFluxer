# Running bots with the runner

A bot needs a process that can stay connected to Fluxer. Vercel functions are short-lived, so they can’t host that process. Instead, the website sends the bot’s code to a small service called **the runner**. The files in this folder are ready to run in a container.

```
 person's browser ──► website on Vercel ──(signed, over https)──► runner ──► one process per bot ──► Fluxer
```

The website uses the runner to start, stop, and check bots. A bot token is sent over when the bot starts and is kept in that bot’s process; it isn’t shared with the other running bots.

## Before you start

- A server that can keep a Docker container running and reach Fluxer over the internet. A small VPS is enough to get started. Vercel, Netlify, and other function hosts aren’t suitable for the runner.
- An HTTPS address for the runner, usually through a reverse proxy such as Caddy or nginx. The website rejects plain `http://` runner URLs because bot tokens are sent to the service.
- Plan for roughly 50 MB of memory per simple running bot. A bot made from the starter blocks used about 40 MB in testing. `RUNNER_MEMORY_MB` is the limit for each bot, so the maximum memory budget is roughly `RUNNER_MAX_CONCURRENT × RUNNER_MEMORY_MB`.

## Start the runner

From the project folder:

```bash
export RUNNER_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"   # keep this, you need it twice
docker compose -f deploy/runner/docker-compose.yml up -d --build
curl http://127.0.0.1:8080/healthz
# {"ok":true,"running":0,"room":8,"user_per_bot":true}
```

Next, put HTTPS in front of port 8080. With Caddy, the configuration can be as simple as:

```
runner.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

Finally, set these two values on the website (in Vercel, open Project Settings → Environment Variables, then redeploy):

| Name            | Value                                |
| --------------- | ------------------------------------ |
| `RUNNER_URL`    | `https://runner.example.com`         |
| `RUNNER_SECRET` | the secret from above (same on both) |

Open the editor and try **Run**. If the site says running bots isn’t enabled, check that it can read `RUNNER_URL` and `RUNNER_SECRET`, and that the secret has at least 32 characters.

## Runner settings

| Name                     | Default        | What it does                                                                     |
| ------------------------ | -------------- | -------------------------------------------------------------------------------- |
| `RUNNER_SECRET`          | _(required)_   | Signs every call from the website. At least 32 characters.                       |
| `RUNNER_MAX_CONCURRENT`  | `8`            | Bots running at the same time. More people than that are told to try again later. |
| `RUNNER_MAX_PER_USER`    | `2`            | Bots one person may run at the same time.                                        |
| `RUNNER_MEMORY_MB`       | `384`          | What one bot may hold in memory. A bot that goes over is stopped.                |
| `RUNNER_MAX_RUN_SECONDS` | `21600`        | A bot is stopped after this long (6 hours). People press Run to go on.           |
| `RUNNER_UID_BASE`        | `41000` (image) | See "How bot processes are isolated". Leave it empty if the runner can't be root.       |
| `RUNNER_DATA_DIR`        | `/var/sff/runs` | Where each bot gets its folder. Mount a small tmpfs here (the compose file does). |
| `PORT`                   | `8080`         | The port inside the container.                                                   |
| `FLUXER_API_URL`         | Fluxer's own   | Only for a self-hosted Fluxer. Set it on the website as well.                    |
| `MAX_ASSETS_TOTAL_BYTES` | `8388608`      | If you raise this on the website, raise it here too, or big uploads are refused. |

## How bot processes are isolated

Bots run code on your machine, so the runner puts several limits around each process:

1. **The code is checked twice**, once when the blocks become Python and again just before a bot starts. Only the
   imports and calls the blocks can produce are allowed. Anything else is refused.
2. **Every bot is its own process**, in its own folder, started from a clean environment that holds the bot's token and
   nothing else the runner knows (not the secret, not the other bots).
3. **Limits**: memory, processor time, file size, open files, how long it runs, how much it writes to its console.
4. **A dedicated user account.** When the runner starts as root and `RUNNER_UID_BASE` is set (the image does this), each bot
   runs as a different user id with no capabilities. It can't read another bot's folder, the runner's files or the runner's
   environment, and it can't start more than 128 processes. When a bot ends, everything it left running ends too.

Step 4 needs five Linux capabilities. Docker gives containers all five by default; the compose file removes
everything else:

| Capability     | Why                                                           |
| -------------- | ------------------------------------------------------------- |
| `SETUID`, `SETGID` | to start the bot as its own user                          |
| `CHOWN`        | to give the bot its folder                                    |
| `DAC_OVERRIDE` | to clear that folder afterwards, whatever the bot put in it   |
| `KILL`         | to stop the bot                                               |

The runner checks this when it starts. If one is missing it refuses to start and says which, instead of running bots
that can't be stopped. `"user_per_bot": true` in `/healthz` tells you the mode you are in.

**If your host can’t run the container as root**, set `RUNNER_UID_BASE` to an empty value. The runner will still work, but all bots share the same user. A bot that gets past the first three safeguards could then read the runner’s environment. This is a weaker setup, so use a host that supports the per-bot user IDs when possible.

**Network access.** A bot can reach any address that the container can reach. The built-in blocks talk to Fluxer, but
that does not stop other code from trying to contact different services. Consider blocking access to private networks and
the cloud metadata address (`169.254.169.254`) with host firewall rules. Docker provides the `DOCKER-USER` chain for
this. Test your rules from inside the container, for example with `curl`.


## A few things to keep in mind

- **Run one worker only.** Running bots belong to a single runner process. Don’t start multiple copies or set `--workers` above `1`; the included image already uses one worker.
- **Restarting stops all bots.** This also happens when you update the image. The website reports that the run is gone, and the user can press **Run** again. Bots don’t restart automatically.
- **Bot files are temporary.** A bot’s working folder and console output are kept only while it runs and briefly afterward. The website stores the saved code and token; the runner receives them when a bot starts.
- **Logs.** `docker compose logs` shows runner activity, not the bot’s console output. Runner logs should not contain tokens or secrets.
- **Rotating the secret.** Update `RUNNER_SECRET` on the website and runner, restart the runner, then redeploy the website.
- **Update the runner.** Run `git pull`, then `docker compose -f deploy/runner/docker-compose.yml up -d --build`.

## Run it without Docker

```bash
pip install -r requirements.txt
RUNNER_SECRET=... RUNNER_DATA_DIR=/var/sff/runs uvicorn scratch_fluxer.runner.app:create_runner_app --factory --port 8080
```

Run it as a normal user (bots share that user), or as root with `RUNNER_UID_BASE=41000` and the capabilities above.
On a laptop you don't need any of this: with no `RUNNER_URL` the website runs bots itself (`RUNNER_MODE=local`).
