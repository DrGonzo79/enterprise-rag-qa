"""Async DB fixtures per SPEC-002's test plan, plus SPEC-003 synthetic corpus
builders (small hand-built files reproducing each format's measured structure
so CI never needs the network or the real corpus).

Session-scoped NullPool engine, function-scoped connections, savepoint-based
rollback isolation. Migration tests use a dedicated scratch database instead.
"""

import os
import uuid
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from baseline_guard import snapshot, violations
from embedding_ledger import EmbeddingLedger, install, report, resolve_ceiling

REPO_ROOT = Path(__file__).resolve().parent.parent

# Tests default to a DEDICATED database, not the dev `rag` database, so suite
# runs and test-debugging can never touch locally ingested corpus data
# (SPEC-002 test plan, limitation note). CI still injects DATABASE_URL=…/rag —
# its service container holds no real data.
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://rag:rag@localhost:5432/rag_test"
)
ADMIN_URL = DATABASE_URL.replace("+asyncpg", "").rsplit("/", 1)[0] + "/rag"
SCRATCH_DB = "rag_migration_test"


async def ensure_database(url: str) -> None:
    """Create the database named by `url` if it doesn't exist yet."""
    import asyncpg

    name = url.rsplit("/", 1)[1]
    admin = await asyncpg.connect(ADMIN_URL)
    try:
        exists = await admin.fetchrow("SELECT 1 FROM pg_database WHERE datname = $1", name)
        if not exists:
            await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()


# --- baseline write gate + guard (SPEC-004 AC-13) ----------------------------
#
# Measuring is a test; producing a baseline artifact is not. The flag gates the
# write, and the session hooks fail the run if a guarded artifact changed
# anyway — belt and braces, because the failure this prevents is silent and
# the artifact it destroys is unreconstructable.

_baseline_snapshot: dict[str, str] = {}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--write-baseline",
        action="store_true",
        default=False,
        help=(
            "Write measured baseline artifacts (evals/baselines/, "
            "evals/retrieval_baseline.json). Off by default: a baseline records a "
            "corpus state that cannot be reconstructed once the corpus changes, so "
            "producing one is a deliberate act, never a side effect of `pytest`."
        ),
    )


@pytest.fixture
def write_baseline(request: pytest.FixtureRequest) -> bool:
    """True only when --write-baseline was passed on the command line."""
    return bool(request.config.getoption("--write-baseline"))


# --- embedding spend ledger (SPEC-003 AC-14) ---------------------------------
#
# Real embedding calls are real charges that reach no ledger and no ceiling
# (SPEC-006 KD-16's invoice clause). This prices them, attributes them to the
# test that made them, and stops the run at a ceiling — so a loop in a test is a
# red build rather than an invoice line nobody can explain.

_embedding_ledger = EmbeddingLedger(ceiling_usd=resolve_ceiling())


def pytest_configure(config: pytest.Config) -> None:
    install(_embedding_ledger)


def pytest_runtest_setup(item: pytest.Item) -> None:
    _embedding_ledger.current_test = item.nodeid


def pytest_sessionstart(session: pytest.Session) -> None:
    global _baseline_snapshot
    _baseline_snapshot = snapshot(REPO_ROOT)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")

    spend = report(_embedding_ledger)
    if spend and reporter is not None:
        reporter.section("EMBEDDING SPEND", bold=True)
        for line in spend:
            reporter.line(f"  {line}")

    problems = violations(
        _baseline_snapshot,
        snapshot(REPO_ROOT),
        writes_allowed=bool(session.config.getoption("--write-baseline")),
    )
    if not problems:
        return

    if reporter is not None:
        reporter.section("BASELINE GUARD FAILED", red=True, bold=True)
        for problem in problems:
            reporter.line(f"  {problem}")
        reporter.line("")
        reporter.line("  Baselines are measured records of a corpus state, not test output.")
        reporter.line("  Restore them (git checkout) and re-run with --write-baseline if the")
        reporter.line("  write was intended. See SPEC-004 AC-13 and SPEC-003 AC-13.")
    session.exitstatus = 1


CORPUS_DIR = Path(__file__).resolve().parent.parent / "corpus"
REAL_CORPUS_PRESENT = all(
    (CORPUS_DIR / name).exists() and (CORPUS_DIR / name).stat().st_size > 100_000
    for name in ("nist-ai-rmf-100-1.pdf", "eu-ai-act-2024-1689.html", "nvda-10k-2026.htm")
)

# --- Synthetic EUR-Lex: eli-subdivision ids, oj-* classes, layout tables ----

# The citation-dense layout table deliberately exceeds the EDGAR digit
# threshold to prove the drop rule never applies to this loader (decision 11).
SYNTH_EURLEX = """<!DOCTYPE html><html><head><title>OJ L</title></head><body>
<p class="oj-doc-ti">REGULATION (EU) 2099/9999 OF THE SYNTHETIC PARLIAMENT</p>
<div class="eli-subdivision" id="pbl_1">
 <div class="eli-subdivision" id="cit_1"><p class="oj-normal">Having regard to the Treaty,</p></div>
 <div class="eli-subdivision" id="rct_1"><p class="oj-normal">(1) Artificial intelligence should serve people. This recital has two sentences.</p></div>
 <div class="eli-subdivision" id="rct_2"><p class="oj-normal">(2) A second synthetic recital follows the first one closely.</p></div>
</div>
<div class="eli-subdivision" id="enc_1">
 <p class="oj-ti-section-1" id="cpt_I">CHAPTER I</p>
 <p class="oj-ti-section-2">GENERAL PROVISIONS</p>
 <div class="eli-subdivision" id="art_1">
  <p class="oj-ti-art">Article 1</p>
  <p class="oj-sti-art">Subject matter</p>
  <p class="oj-normal">This Regulation lays down synthetic harmonised rules.</p>
  <table><colgroup><col/><col/></colgroup><tbody>
   <tr><td><p class="oj-normal">(a)</p></td><td><p class="oj-normal">rules amending Regulations (EC) No 300/2008, (EU) No 167/2013 and (EU) 2018/858 under Article 6(2);</p></td></tr>
   <tr><td><p class="oj-normal">(b)</p></td><td><p class="oj-normal">prohibitions listed in Article 5(1) and Annex III point 8;</p></td></tr>
  </tbody></table>
 </div>
 <p class="oj-ti-section-1" id="sct_1">SECTION 1</p>
 <p class="oj-ti-section-2">Classification</p>
 <div class="eli-subdivision" id="art_2">
  <p class="oj-ti-art">Article 2</p>
  <p class="oj-sti-art">Scope</p>
  <p class="oj-normal">This Regulation applies to synthetic providers placing systems on the market. It also applies to synthetic deployers established in the Union.</p>
 </div>
</div>
<p class="oj-doc-ti">ANNEX I</p>
<p class="oj-doc-ti">List of synthetic harmonisation legislation</p>
<p class="oj-normal">Regulation (EC) No 300/2008 on common rules in civil aviation.</p>
<p class="oj-normal">Directive 2014/90/EU on marine equipment applies fully.</p>
<p class="oj-doc-ti">ANNEX II</p>
<p class="oj-doc-ti">Synthetic offence list</p>
<p class="oj-normal">Terrorism as defined in the relevant Directive is listed here.</p>
<div class="eli-subdivision" id="fnp_1"><p class="oj-note">OJ L 97, 9.4.2008, p. 72.</p></div>
</body></html>"""

# --- Synthetic EDGAR: ix tags, styled headings, duplicate Items, tables -----

_EDGAR_NARRATIVE = (
    "NVIDIA-Synthetic pioneered accelerated computing. " * 3
    + "Our platforms span data center, gaming and automotive markets. "
    + "We sell to cloud service providers, discussed further below. "
)

SYNTH_EDGAR_TEXT = f"""<!DOCTYPE html><html><head><title>10-K</title></head><body>
<div style="display:none"><ix:header><ix:hidden>HIDDEN-METADATA-SHOULD-NEVER-APPEAR</ix:hidden></ix:header></div>
<div>Table of Contents</div>
<table><tbody>
 <tr><td><a href="#i1">Item 1.</a></td><td><a href="#i1">Business</a></td><td>3</td></tr>
 <tr><td><a href="#i1a">Item 1A.</a></td><td><a href="#i1a">Risk Factors</a></td><td>14</td></tr>
 <tr><td><a href="#i2">Item 2.</a></td><td><a href="#i2">Properties</a></td><td>30</td></tr>
</tbody></table>
<div id="i1"><span style="color:#76b900;font-weight:700">Item 1. Business</span></div>
<p>{_EDGAR_NARRATIVE}Revenue reached <ix:nonfraction contextref="c1">130,497</ix:nonfraction> million this year.</p>
<div><span style="font-weight:700">Our Markets</span></div>
<p>The company’s markets keep growing across every segment we serve today.</p>
<table><tbody>
 <tr><td>Title of each class</td><td>Trading Symbol</td><td>Exchange</td></tr>
 <tr><td>Common Stock</td><td>NVDA-S</td><td>Synthetic Select Market</td></tr>
</tbody></table>
<div id="i1a"><span style="color:#76b900;font-weight:700">Item 1A. Risk Factors</span></div>
<p>Demand may fluctuate. Supply constraints could persist for several quarters ahead.</p>
<table><tbody>
 <tr><td>Revenue</td><td>130,497</td><td>60,922</td></tr>
 <tr><td>Gross margin</td><td>75.0%</td><td>72.7%</td></tr>
 <tr><td>Operating expenses</td><td>16,405</td><td>11,329</td></tr>
</tbody></table>
<div id="i2"><span style="color:#76b900;font-weight:700">Item 2. Properties</span></div>
<p>Our synthetic headquarters are located in Santa Clara, California today.</p>
</body></html>"""

SYNTH_EDGAR_BYTES = SYNTH_EDGAR_TEXT.encode("cp1252")


# --- Synthetic PDF: 3 pages, running header, hyphenation, outline -----------


def _pdf_page_stream(lines: list[str]) -> bytes:
    ops = ["BT", "/F1 12 Tf", "72 720 Td"]
    for i, line in enumerate(lines):
        if i:
            ops.append("0 -16 Td")
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        ops.append(f"({escaped}) Tj")
    ops.append("ET")
    return "\n".join(ops).encode("latin-1")


def build_synth_pdf() -> bytes:
    """Handcrafted 3-page PDF + pypdf-written outline, reproducing the NIST
    hazards: running header on every page, line-break hyphenation."""
    from pypdf import PdfReader, PdfWriter

    pages = [
        [
            "SYNTH DOC HEADER",
            "Alpha Section",
            "Alpha opens with a first sentence. It continues with clear bene-",
            "fits of synthetic fixtures. Alpha closes with a third sentence.",
        ],
        [
            "SYNTH DOC HEADER",
            "Beta Section",
            "Beta discusses Article 6(2) compliance and Regulation (EC) No 300/2008.",
            "Beta has a second sentence too.",
        ],
        [
            "SYNTH DOC HEADER",
            "Beta Child",
            "The child of beta carries one more sentence for depth testing.",
        ],
    ]
    streams = [_pdf_page_stream(p) for p in pages]

    objects: list[bytes] = []
    kids = " ".join(f"{3 + i} 0 R" for i in range(3))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count 3 >>".encode())
    for i in range(3):
        objects.append(
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 6 0 R >> >> /Contents {7 + i} 0 R >>"
            ).encode()
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    buf = BytesIO()
    buf.write(b"%PDF-1.4\n")
    offsets: list[int] = []
    for num, obj in enumerate(objects, start=1):
        offsets.append(buf.tell())
        buf.write(f"{num} 0 obj\n".encode() + obj + b"\nendobj\n")
    for num, stream in enumerate(streams, start=7):
        offsets.append(buf.tell())
        buf.write(
            f"{num} 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode()
            + stream
            + b"\nendstream\nendobj\n"
        )
    xref_at = buf.tell()
    count = len(offsets) + 1
    buf.write(f"xref\n0 {count}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        buf.write(f"{offset:010d} 00000 n \n".encode())
    buf.write(f"trailer\n<< /Size {count} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode())

    writer = PdfWriter()
    writer.append(PdfReader(BytesIO(buf.getvalue())))
    writer.add_outline_item("Alpha Section", 0)
    beta = writer.add_outline_item("Beta Section", 1)
    writer.add_outline_item("Beta Child", 2, parent=beta)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


@pytest.fixture
def synth_corpus(tmp_path: Path) -> Path:
    """A directory with all three synthetic documents, routable by the pipeline."""
    (tmp_path / "synth-eurlex.html").write_text(SYNTH_EURLEX, encoding="utf-8")
    (tmp_path / "synth-10k.htm").write_bytes(SYNTH_EDGAR_BYTES)
    (tmp_path / "synth-nist.pdf").write_bytes(build_synth_pdf())
    return tmp_path


@pytest.fixture(scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    await ensure_database(DATABASE_URL)
    engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
async def migrated_engine(engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    """The main test DB, migrated to head for the duration of the session."""
    from alembic.config import Config

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", DATABASE_URL)
    async with engine.connect() as conn:
        await conn.run_sync(lambda sync_conn: _upgrade(config, sync_conn))
        await conn.commit()
    yield engine


def _upgrade(config: object, sync_conn: object) -> None:
    from alembic import command
    from alembic.config import Config

    assert isinstance(config, Config)
    config.attributes["connection"] = sync_conn
    command.upgrade(config, "head")


@pytest.fixture
async def connection(migrated_engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """Function-scoped connection wrapping each test in a rolled-back transaction."""
    async with migrated_engine.connect() as conn:
        transaction = await conn.begin()
        yield conn
        await transaction.rollback()


@pytest.fixture
async def session(connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    """Savepoint-mode session bound to the rolled-back outer transaction."""
    async with AsyncSession(
        bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    ) as sess:
        yield sess


# --- SPEC-004 retrieval fixtures ---------------------------------------------
#
# Retrieval runs its two branch searches on SEPARATE connections, so the
# savepoint-rollback fixture above cannot host it (one connection, one
# transaction). These fixtures commit real rows through a pooled engine and
# clean up by deleting the exact document ids they inserted — never a
# TRUNCATE, so a misconfigured DATABASE_URL pointing at a corpus database
# still cannot lose data (SPEC-002 test-plan limitation note).

EMBED_DIM = 1536
STUB_IDENTITY = "fake:test-v1"


def unit_vector(theta: float) -> list[float]:
    """[cos θ, sin θ, 0…] — cosine distance to QUERY_VECTOR is 1 - cos θ, so
    dense rank order is exactly θ ascending."""
    import math

    vector = [0.0] * EMBED_DIM
    vector[0] = math.cos(theta)
    vector[1] = math.sin(theta)
    return vector


QUERY_VECTOR = unit_vector(0.0)


class StubQueryEmbedder:
    """Returns one fixed vector; identity is settable so AC-4 can mismatch it."""

    def __init__(self, vector: list[float] | None = None, identity: str = STUB_IDENTITY) -> None:
        self.identity = identity
        self._vector = vector if vector is not None else QUERY_VECTOR
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._vector for _ in texts]


@dataclass(frozen=True)
class SeededChunk:
    """One chunk to insert; list position sets its dense rank."""

    document_key: str
    text: str
    section_path: str


@dataclass
class SeededCorpus:
    document_ids: dict[str, uuid.UUID]
    chunk_ids: dict[str, uuid.UUID]  # keyed by chunk text
    total_chunks: int


async def seed_corpus(
    engine: AsyncEngine,
    documents: dict[str, tuple[str, str, str]],  # key -> (title, source_uri, doc_type)
    chunks: list[SeededChunk],
    *,
    identity: str = STUB_IDENTITY,
) -> SeededCorpus:
    """Insert documents + chunks whose dense rank order is `chunks` order."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from rag_qa.db.models import Chunk, Document

    factory = async_sessionmaker(engine, expire_on_commit=False)
    document_ids = {key: uuid.uuid4() for key in documents}
    chunk_ids: dict[str, uuid.UUID] = {}
    ordinals: dict[str, int] = dict.fromkeys(documents, 0)

    async with factory() as sess:
        for key, (title, source_uri, doc_type) in documents.items():
            sess.add(
                Document(
                    id=document_ids[key],
                    source_uri=source_uri,
                    title=title,
                    doc_type=doc_type,
                    content_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                    byte_size=len(title),
                )
            )
        await sess.flush()
        for rank, spec in enumerate(chunks):
            chunk_id = uuid.uuid4()
            chunk_ids[spec.text] = chunk_id
            ordinal = ordinals[spec.document_key]
            ordinals[spec.document_key] = ordinal + 1
            sess.add(
                Chunk(
                    id=chunk_id,
                    document_id=document_ids[spec.document_key],
                    ordinal=ordinal,
                    text=spec.text,
                    token_count=max(1, len(spec.text.split())),
                    section_path=spec.section_path,
                    # rank 0 nearest; strictly increasing angle => exact dense order
                    embedding=unit_vector((rank + 1) * 0.004),
                    embedding_model=identity,
                )
            )
        await sess.commit()

    return SeededCorpus(document_ids=document_ids, chunk_ids=chunk_ids, total_chunks=len(chunks))


async def drop_documents(engine: AsyncEngine, document_ids: Iterable[uuid.UUID]) -> None:
    """Delete exactly the seeded documents (chunks cascade). Never a TRUNCATE."""
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from rag_qa.db.models import Document

    factory = async_sessionmaker(engine)
    async with factory() as sess:
        await sess.execute(delete(Document).where(Document.id.in_(list(document_ids))))
        await sess.commit()


# The seeded corpus: 200 regulation chunks, 12 filing chunks, 3 standard
# chunks (215 total, satisfying AC-8's ">= 200 chunks"). Dense rank order is
# list order, so:
#   rank 1   = DENSE_ONLY_TEXT   (no lexical overlap with the probe query)
#   rank 215 = LEXICAL_ONLY_TEXT (the only chunk containing "quarklebit")
# which is exactly AC-3's setup: FTS-rank-1 but far outside the dense pool.
DENSE_ONLY_TEXT = "Zephyr alignment considerations govern oversight of automated decisions."
LEXICAL_ONLY_TEXT = "The quarklebit provision governs exceptional derogation cases."
PROBE_QUERY = "quarklebit"

SEED_DOCUMENTS = {
    "regulation": ("Synthetic Regulation", "synthetic://regulation", "regulation"),
    "filing": ("Synthetic Filing", "synthetic://filing", "filing"),
    "standard": ("Synthetic Standard", "synthetic://standard", "standard"),
}


def build_seed_chunks() -> list[SeededChunk]:
    chunks = [
        SeededChunk("regulation", DENSE_ONLY_TEXT, "Synthetic Regulation › CHAPTER I › Article 1")
    ]
    for i in range(198):
        chunks.append(
            SeededChunk(
                "regulation",
                f"Regulation filler passage {i} describing obligations of providers and deployers.",
                f"Synthetic Regulation › CHAPTER I › Article {2 + i % 7}",
            )
        )
    for i in range(12):
        chunks.append(
            SeededChunk(
                "filing",
                f"Filing narrative passage {i} discussing demand, supply and manufacturing risk.",
                f"Synthetic Filing › Item 1A. Risk Factors › Topic {i % 3}",
            )
        )
    for i, pillar in enumerate(("Govern", "Map", "Measure")):
        chunks.append(
            SeededChunk(
                "standard",
                f"Standard guidance passage {i} on {pillar} functions and outcomes.",
                f"Synthetic Standard › Core › {pillar}",
            )
        )
    chunks.append(
        SeededChunk(
            "regulation", LEXICAL_ONLY_TEXT, "Synthetic Regulation › CHAPTER IX › Article 99"
        )
    )
    return chunks


@pytest.fixture(scope="session")
async def pooled_engine(migrated_engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    """A genuinely pooled engine — the branch searches need two connections,
    and AC-11 needs pooled recycling to be real."""
    engine = create_async_engine(DATABASE_URL, pool_size=4, max_overflow=2)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
async def seeded_corpus(pooled_engine: AsyncEngine) -> AsyncIterator[SeededCorpus]:
    """Read-only 215-chunk corpus shared across retrieval tests."""
    corpus = await seed_corpus(pooled_engine, SEED_DOCUMENTS, build_seed_chunks())
    yield corpus
    await drop_documents(pooled_engine, corpus.document_ids.values())


@pytest.fixture
def session_factory(pooled_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    return async_sessionmaker(pooled_engine, expire_on_commit=False)


# --- query_log hygiene: one shared sweep, and an independent check on it -------
#
# Tests that build an app with a `session_factory` write a `query_log` row per
# `/query`, and cleaning up was left to whichever file remembered. Most did not:
# 184 rows had accumulated from 2026-07-26 onward, growing by every full run.
# Ambient rows no longer threaten correctness — the budget tests now measure
# their floor or use an empty window rather than assuming an empty table — but
# unbounded growth in a shared database is a thing someone inherits.
#
# Two fixtures rather than one, deliberately. The sweep is the mechanism; the
# count check is a *separate* verifier that does not share its implementation, so
# breaking the sweep fails the check rather than silently doing nothing. A
# cleanup that also reports its own success is the vacuous shape this repository
# keeps finding.


def _is_test_database(url: str) -> bool:
    """The dev `rag` database holds the real ingested corpus. CI injects
    `…/rag` for a throwaway service container, so the name alone cannot decide —
    which is why the sweep below is id-scoped rather than name-scoped, and this
    guard only gates the *bulk* path.

    **Matched at a component break, not by raw prefix** (the Annex I sweep,
    2026-08-04): `startswith("rag_test")` also accepted `rag_testing` and
    `rag_test_of_someone_elses`, which is the same over-match that made a gold
    label four times easier — a plausible pass where a visible failure belonged.
    A deliberate variant still works if it is separated at an underscore."""
    name = url.rsplit("/", 1)[1]
    return name == "rag_test" or name.startswith("rag_test_")


async def _query_log_ids(engine: AsyncEngine) -> set[str]:
    from sqlalchemy import text as sa_text

    async with engine.connect() as conn:
        rows = await conn.execute(sa_text("SELECT id::text FROM query_log"))
        return {row[0] for row in rows}


@pytest.fixture(scope="session", autouse=True)
async def query_log_sweep(
    migrated_engine: AsyncEngine, query_log_count_is_unchanged: None
) -> AsyncIterator[None]:
    """Delete exactly the `query_log` rows this session created.

    **Id-scoped, not predicate-scoped, and that is a stronger guarantee than the
    database-name guard it replaces.** A name check permits deleting rows it did
    not create as long as the database is called `rag_test`; this cannot delete
    any row it did not observe appear. It is therefore safe to run against the
    same `…/rag` URL CI injects, which a name guard would have skipped — leaving
    the one environment that runs the whole suite every time uncleaned.
    """
    from sqlalchemy import text as sa_text

    before = await _query_log_ids(migrated_engine)
    yield
    after = await _query_log_ids(migrated_engine)
    created = after - before
    if not created:
        return
    async with migrated_engine.begin() as conn:
        await conn.execute(
            sa_text("DELETE FROM query_log WHERE id::text = ANY(:ids)"), {"ids": sorted(created)}
        )


@pytest.fixture(scope="session", autouse=True)
async def query_log_count_is_unchanged(migrated_engine: AsyncEngine) -> AsyncIterator[None]:
    """A full run must leave `query_log` the size it found it.

    Independent of the sweep on purpose: it counts rather than tracking ids, so
    it also catches what the sweep structurally cannot — a row written with a
    `created_at` in the past that some test inserted and failed to remove, and
    any future sweep that quietly stops running. Deleting the sweep's body makes
    this fail; that is the check being real rather than ceremonial.

    **The sweep depends on this fixture rather than the reverse**, which is what
    puts this teardown *after* the sweep's. Fixture teardown is LIFO, so the
    dependency arrow and the check order are opposites — written the intuitive
    way round, this counted the rows before anything had been deleted and
    reported every swept row as a leak. Found by running it.
    """
    from sqlalchemy import text as sa_text

    async def count() -> int:
        async with migrated_engine.connect() as conn:
            return (await conn.execute(sa_text("SELECT count(*) FROM query_log"))).scalar_one()

    before = await count()
    yield
    after = await count()
    assert after == before, (
        f"the suite leaked {after - before} query_log row(s) "
        f"({before} -> {after}). Every test that writes rows must remove them; the "
        "session sweep in conftest.py handles rows it saw created, so a leak here is "
        "a row it could not attribute — check for explicit created_at timestamps."
    )
