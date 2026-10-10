# The runner

A Fluxer bot has to stay connected to Fluxer all the time. Vercel runs the website in short-lived functions, which can't
do that. So the website hands the bot's code to a small service that does: **the runner**. This folder is that service,
ready to run in a container.

```
 person's browser ──► website on Vercel ──(signed, over https)──► runner ──► one process per bot ──► Fluxer
```

The website only ever asks the runner to start, stop and report on a bot. The bot's token travels to the runner when
the bot is started and lives only in that bot's process.

## What you need

- A host that can keep a Docker container running all the time and lets it reach Fluxer over the internet. A small
  VPS is enough to start with. Vercel, Netlify and other function hosts can't run it.
- A web address with https in front of it (a reverse proxy such as Caddy or nginx, or your host's own). The website
  refuses a plain `http://` runner address, because bot tokens travel to it.
- About 50 MB of memory for each simple bot that is running (measured: a bot made from the starter blocks uses about
  40 MB). `RUNNER_MEMORY_MB` is the ceiling for one bot that grows, so a runner that is full of such bots needs
  `RUNNER_MAX_CONCURRENT × RUNNER_MEMORY_MB`.

## Start it

From the project folder:

```bash
export RUNNER_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"   # keep this, you need it twice
docker compose -f deploy/runner/docker-compose.yml up -d --build
curl http://127.0.0.1:8080/healthz
# {"ok":true,"running":0,"room":8,"user_per_bot":true}
```

Then put https in front of port 8080. With Caddy that is one line in the Caddyfile:

```
runner.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

Last, give the website the same two values (on Vercel: Project Settings → Environment Variables, then redeploy):

| Name            | Value                                |
| --------------- | ------------------------------------ |
| `RUNNER_URL`    | `https://runner.example.com`         |
| `RUNNER_SECRET` | the secret from above (same on both) |

Open the editor on your site: the Run button works now. If it says running bots isn't switched on, the website
doesn't see `RUNNER_URL` and `RUNNER_SECRET` yet, or the secret is shorter than 32 characters.

## Settings of the runner

| Name                     | Default        | What it does                                                                     |
| ------------------------ | -------------- | -------------------------------------------------------------------------------- |
| `RUNNER_SECRET`          | _(required)_   | Signs every call from the website. At least 32 characters.                       |
| `RUNNER_MAX_CONCURRENT`  | `8`            | Bots running at the same time. More people than that are told to try again later. |
| `RUNNER_MAX_PER_USER`    | `2`            | Bots one person may run at the same time.                                        |
| `RUNNER_MEMORY_MB`       | `384`          | What one bot may hold in memory. A bot that goes over is stopped.                |
| `RUNNER_MAX_RUN_SECONDS` | `21600`        | A bot is stopped after this long (6 hours). People press Run to go on.           |
| `RUNNER_UID_BASE`        | `41000` (image) | See "How bots are kept apart". Leave it empty if the runner can't be root.       |
| `RUNNER_DATA_DIR`        | `/var/sff/runs` | Where each bot gets its folder. Mount a small tmpfs here (the compose file does). |
| `PORT`                   | `8080`         | The port inside the container.                                                   |
| `FLUXER_API_URL`         | Fluxer's own   | Only for a self-hosted Fluxer. Set it on the website as well.                    |
| `MAX_ASSETS_TOTAL_BYTES` | `8388608`      | If you raise this on the website, raise it here too, or big uploads are refused. |

## How bots are kept apart

Everything a person builds is code that runs on your machine, so the runner is careful with it. From the outside in:

1. **The code is checked twice**, once when the blocks become Python and again just before a bot starts. Only the
   imports and calls the blocks can produce are allowed. Anything else is refused.
2. **Every bot is its own process**, in its own folder, started from a clean environment that holds the bot's token and
   nothing else the runner knows (not the secret, not the other bots).
3. **Limits**: memory, processor time, file size, open files, how long it runs, how much it writes to its console.
4. **A user of its own.** When the runner starts as root and `RUNNER_UID_BASE` is set (the image does this), each bot
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

**If your host can't run the container as root** (some platforms insist on a normal user), start it with an empty
`RUNNER_UID_BASE`. It still works, but all bots then share the runner's user, so a bot that got past steps 1 to 3 could
read the runner's environment. That is the weaker setup; prefer a host that allows the first.

**Network.** A bot can reach whatever the container can reach. The blocks only talk to Fluxer, but if you want to be
sure, stop the container from reaching your private networks and the cloud metadata address (`169.254.169.254`), for
example with firewall rules on the host. Docker publishes a chain for this (`DOCKER-USER`). I haven't tested rules
for your network, so check them from inside the container with `curl`.

## Good to know

- **One runner, one worker.** The bots that are running belong to that one process, so don't start more than one copy
  or use `--workers` above 1. The image doesn't.
- **A restart stops every bot.** That includes updating the image. The website tells people ("The runner no longer has
  this run") and they press Run again. Bots don't start by themselves.
- **Nothing is stored.** A bot's folder and its console messages are kept only while it runs and a short while after.
  Code and tokens live on the website's database; the runner only gets them when a bot is started.
- **Logs.** The runner's own log (`docker compose logs`) records what the runner did, not what bots said, and is kept
  free of tokens and secrets.
- **Changing the secret.** Change `RUNNER_SECRET` on both sides, restart the runner, redeploy the website.
- **Update.** `git pull`, then `docker compose -f deploy/runner/docker-compose.yml up -d --build`.

## Without Docker

```bash
pip install -r requirements.txt
RUNNER_SECRET=... RUNNER_DATA_DIR=/var/sff/runs uvicorn scratch_fluxer.runner.app:create_runner_app --factory --port 8080
```

Run it as a normal user (bots share that user), or as root with `RUNNER_UID_BASE=41000` and the capabilities above.
On a laptop you don't need any of this: with no `RUNNER_URL` the website runs bots itself (`RUNNER_MODE=local`).
