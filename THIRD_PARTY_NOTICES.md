# Third-party notices

Scratch For Fluxer includes, or depends on, the following. Each one keeps its own licence. This file lists them so you
can check them; it is not legal advice, and the licence texts of the projects themselves are the ones that count.

## Included in this repository (served to browsers)

| What | Where | Licence |
| ---- | ----- | ------- |
| [Blockly](https://github.com/google/blockly) 13.3.0 (the block editor) | `public/static/vendor/blockly/` (`LICENSE` is Blockly's) | Apache-2.0 |
| [Phosphor Icons](https://phosphoricons.com), Fill weight (the icon font) | `public/static/vendor/phosphor/` (`LICENSE` is Phosphor's) | MIT |
| [IBM Plex](https://github.com/IBM/plex) Sans, Sans Arabic and Mono (the fonts, as WOFF2 files) | `public/static/fonts/` (`LICENSE-IBM-Plex-OFL.txt`) | SIL Open Font License 1.1 |

The logo (`public/static/img/logo.png`) and the images made from it are the project's own artwork. Fluxer and its name
belong to their owners.

## Python packages (installed with `pip`, not copied into this repository)

These are the packages in `requirements.txt` and the ones they bring along that matter most, with the licence each
declares in the versions this project was tested with.

| Package | Licence |
| ------- | ------- |
| [FastAPI](https://github.com/fastapi/fastapi), [Pydantic](https://github.com/pydantic/pydantic), pydantic-settings, [SQLAlchemy](https://www.sqlalchemy.org), [fluxer.py](https://github.com/akarealemil/fluxer.py), anyio, h11, httptools | MIT |
| [Starlette](https://www.starlette.io), [Uvicorn](https://www.uvicorn.org), [Jinja2](https://jinja.palletsprojects.com), MarkupSafe, [HTTPX](https://www.python-httpx.org), httpcore, itsdangerous, idna, websockets, click, python-dotenv | BSD-3-Clause |
| [cryptography](https://github.com/pyca/cryptography) | Apache-2.0 or BSD-3-Clause |
| python-multipart, aiohttp (a dependency of fluxer.py) | Apache-2.0 (aiohttp: Apache-2.0 and MIT) |
| [psycopg](https://www.psycopg.org/psycopg3/) 3 (the Postgres driver, with its binary build) | LGPL-3.0 |
| certifi | MPL-2.0 |
| uvloop (installed with `uvicorn[standard]`) | MIT or Apache-2.0 |
| typing_extensions | PSF-2.0 |

`psycopg` is used as a library and is not modified or copied into this project.

For the tests and the helper scripts (`requirements-dev.txt`, not needed to run the site): pytest (MIT), pytest-asyncio
(Apache-2.0), respx (BSD-3-Clause), httpx2 (BSD-3-Clause), Playwright (Apache-2.0) and Pillow (MIT-CMU).

## What you can check yourself

```bash
pip install pip-licenses
pip-licenses --from=mixed --format=markdown
```
