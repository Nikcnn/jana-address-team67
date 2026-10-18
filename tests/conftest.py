"""Suite-wide test isolation (integrator-owned).

app.parser.PARSE_ERROR_COUNT is a process-global counter surfaced by /health.
Several tests intentionally inject parser failures (C7/F4 checks). Without
isolation their increments leak into unrelated tests that assert a clean
/health (error_count == 0). Snapshot/restore around every test keeps each test
independent of execution order.
"""
import pytest


@pytest.fixture(autouse=True)
def _restore_parser_error_counter():
    import app.parser as parser_module
    before = getattr(parser_module, "PARSE_ERROR_COUNT", 0)
    yield
    parser_module.PARSE_ERROR_COUNT = before
