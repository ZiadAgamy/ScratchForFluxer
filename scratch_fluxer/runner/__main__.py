"""Start the runner service:  python -m scratch_fluxer.runner      (it listens on $HOST:$PORT, 0.0.0.0:8080 by default)"""

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "scratch_fluxer.runner.app:create_runner_app",
        factory=True,
        host=os.environ.get("HOST", "0.0.0.0"),  # noqa: S104 - a runner is meant to be reached from the site
        port=int(os.environ.get("PORT", "8080")),
        log_level="info",
        # Behind a platform's proxy the address of the caller doesn't matter: every call is signed.
        server_header=False,
    )


if __name__ == "__main__":
    main()
