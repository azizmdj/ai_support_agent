import shutil
import socket
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from triage_agent import DEFAULT_PACK, DEFAULT_TICKETS
from triage_agent.audit import AuditLog
from triage_agent.config import load_config
from triage_agent.gate import Gate
from triage_agent.llm import FakeLLM
from triage_agent.models import Ticket
from triage_agent.pipeline import Pipeline
from triage_agent.store import SQLiteCaseStore, connect
from triage_agent.ticket_source import MockTicketSource, load_tickets


# --- No network: opening any new socket or connection fails for the whole test session. -------
# Only socketpair() is allowed: the in-process test client's event loop needs it for its
# self-pipe, and on Windows that is a loopback pair. It never leaves the machine.
class _NoNetwork(OSError):
    pass


def _blocked(*args, **kwargs):
    raise _NoNetwork("network access is disabled in tests")


_RealSocket = socket.socket


class _BlockedSocket(socket.socket):
    def __init__(self, family=-1, type=-1, proto=-1, fileno=None):
        if fileno is None:  # wrapping an already open fd (accept()) is not new network use
            _blocked()
        super().__init__(family, type, proto, fileno)


def _loopback_socketpair(family=socket.AF_INET, type=socket.SOCK_STREAM, proto=0):
    listener = _RealSocket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        client = _RealSocket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect(listener.getsockname())
        server, _ = listener.accept()
        return server, client
    finally:
        listener.close()


socket.socket = _BlockedSocket  # type: ignore[misc]
socket.create_connection = _blocked  # type: ignore[assignment]
socket.getaddrinfo = _blocked  # type: ignore[assignment]
socket.socketpair = _loopback_socketpair  # type: ignore[assignment]


def pytest_addoption(parser):
    parser.addoption("--update-snapshots", action="store_true", default=False,
                     help="rewrite tests/snapshots/ from the current code instead of comparing")


# --- Shared fixtures -------------------------------------------------------------------------
@pytest.fixture
def pack(tmp_path) -> Path:
    """A private, editable copy of the shipped pack."""
    dest = tmp_path / "pack"
    shutil.copytree(DEFAULT_PACK, dest)
    return dest


def edit_yaml(path: Path, change) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


@pytest.fixture
def config():
    return load_config(DEFAULT_PACK)


@pytest.fixture
def fixture_tickets() -> dict[str, Ticket]:
    return {t.id: t for t in load_tickets(DEFAULT_TICKETS)}


class Env:
    def __init__(self, config, llm=None, tickets=()):
        self.config = config
        self.llm = llm or FakeLLM()
        self.conn = connect(":memory:")
        self.store = SQLiteCaseStore(self.conn)
        self.audit = AuditLog(self.conn, config.version)
        self.source = MockTicketSource(list(tickets))
        self.pipeline = Pipeline(config, self.llm, self.store, self.audit)
        self.gate = Gate(self.store, self.audit, self.source)

    def run(self, ticket):
        return self.pipeline.process(ticket)

    def events(self, case_id=None):
        return [e.event for e in self.audit.events(case_id)]


@pytest.fixture
def make_env(config):
    def _make(llm=None, cfg=None, tickets=()):
        return Env(cfg or config, llm, tickets)

    return _make


def ticket(text, *, id="X1", language="en", metadata=None, received_at=None) -> Ticket:
    return Ticket(
        id=id,
        received_at=received_at or datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc),
        language=language,
        text=text,
        metadata={"customer_name": "Test Kunde", "plan_name": "Smart", "card_last4": "1234",
                  "transfer_reference": "TRX-1"} if metadata is None else metadata,
    )


@pytest.fixture(autouse=True)
def _clean_provider_env(monkeypatch):
    """Tests never pick up a developer's real keys or provider override."""
    for name in ("TRIAGE_PROVIDER", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "TRIAGE_DB", "TRIAGE_REVIEWER"):
        monkeypatch.delenv(name, raising=False)
