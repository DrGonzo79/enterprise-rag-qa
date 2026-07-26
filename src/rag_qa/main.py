"""ASGI entrypoint.

Thin by design (SPEC-006 KD-11): `create_app()` performs no I/O, so importing
this module never requires a database or an API key. Everything that can fail
fails in `lifespan`, with a message naming the variable.
"""

from rag_qa.api import create_app
from rag_qa.env import load_env

load_env()  # .env fills gaps for local runs; real env vars win (SPEC-001 KD-6)

app = create_app()
