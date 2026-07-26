"""Async DB fixtures per SPEC-002's test plan, plus SPEC-003 synthetic corpus
builders (small hand-built files reproducing each format's measured structure
so CI never needs the network or the real corpus).

Session-scoped NullPool engine, function-scoped connections, savepoint-based
rollback isolation. Migration tests use a dedicated scratch database instead.
"""

import os
from collections.abc import AsyncIterator
from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+asyncpg://rag:rag@localhost:5432/rag")
SCRATCH_DB = "rag_migration_test"

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
