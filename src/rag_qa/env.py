"""Load .env for local runs (SPEC-001 Key decision 6).

Real environment variables always win (override=False), so containers and CI
— where compose/Actions inject the variables — behave exactly as before; the
.env file only fills gaps on a bare local shell. The search starts from the
current working directory (usecwd) because the package itself is installed
under .venv, from which an upward search would never reach the repo root.
"""

from dotenv import find_dotenv, load_dotenv


def load_env() -> None:
    load_dotenv(find_dotenv(usecwd=True), override=False)
