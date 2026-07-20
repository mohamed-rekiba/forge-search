"""End-to-end: launch the server and speak MCP to it over stdio.

This file exists because `serve` shipped broken while 197 tests passed. A
`--index-dir` argument was threaded through the CLI and into `ForgeSearch`
without the field ever being added, so every unit test — which constructs
`HybridIndex` directly — stayed green while the only path an MCP client
actually uses died on startup:

    TypeError: ForgeSearch.__init__() got an unexpected keyword argument
    'index_dir'

Unit tests cannot catch that class of bug: the wiring between CLI and server
is exactly the seam they mock away. So this test starts a real subprocess and
performs a real handshake, which is slower and worth it.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

TIMEOUT = 30


class Client:
    """The smallest MCP client that can prove the server works."""

    def __init__(self, *args: str, cwd: str) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "forge_search.cli", "serve", *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=cwd,
        )

    def send(self, message: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def read(self) -> dict:
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline()
        if not line:
            assert self.proc.stderr is not None
            raise AssertionError(f"server died. stderr:\n{self.proc.stderr.read()}")
        return json.loads(line)

    def handshake(self) -> dict:
        self.send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            }
        )
        result = self.read()
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def call(self, name: str, **arguments) -> dict:
        self.send(
            {
                "jsonrpc": "2.0",
                "id": 99,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        return json.loads(self.read()["result"]["content"][0]["text"])

    def close(self) -> str:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - shutdown hygiene
            self.proc.kill()
        assert self.proc.stderr is not None
        return self.proc.stderr.read()


@pytest.fixture
def project(tmp_path):
    (tmp_path / "auth.md").write_text("# Tokens\n\nHow tokens are validated.\n")
    (tmp_path / "notes.txt").write_text("unrelated content\n")
    return tmp_path


def test_server_starts_and_serves_a_search(project):
    client = Client("--root", str(project), cwd=str(project))
    try:
        assert client.handshake()["result"]["protocolVersion"]

        client.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in client.read()["result"]["tools"]}
        assert names == {"search_code", "search_docs", "reindex"}

        hits = client.call("search_code", query="tokens", top_k=5)["results"]
        assert any(h["path"] == "auth.md" for h in hits)
    finally:
        client.close()


def test_server_starts_with_index_dir(project):
    # The exact combination that shipped broken.
    client = Client(
        "--root", str(project), "--index-dir", ".forge-search", cwd=str(project)
    )
    try:
        client.handshake()
        assert client.call("search_code", query="tokens", top_k=5)["results"]
        assert (project / ".forge-search").is_dir()
    finally:
        client.close()


def test_startup_stderr_is_one_informational_line(project):
    # Every stderr line is rendered as a warning by MCP clients, so routine
    # traffic showing up there reads as breakage. Only the resolved-roots
    # line — the wrong-root safety net — is allowed.
    client = Client("--root", str(project), cwd=str(project))
    try:
        client.handshake()
        client.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        client.read()
        client.call("search_code", query="tokens", top_k=5)
    finally:
        err = client.close()

    lines = [ln for ln in err.strip().splitlines() if ln.strip()]
    assert len(lines) == 1, f"unexpected stderr chatter:\n{err}"
    assert lines[0].startswith("forge-search: roots:")
    assert "Processing request of type" not in err
    assert "Traceback" not in err
