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
        self.notifications: list[dict] = []

    def send(self, message: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def read(self) -> dict:
        """Next response, collecting any notifications that arrive first.

        A real client demultiplexes by `id`; notifications have none. Reading
        blindly makes a log notification look like a malformed response.
        """
        while True:
            assert self.proc.stdout is not None
            line = self.proc.stdout.readline()
            if not line:
                assert self.proc.stderr is not None
                raise AssertionError(f"server died. stderr:\n{self.proc.stderr.read()}")
            message = json.loads(line)
            if "id" not in message:
                self.notifications.append(message)
                continue
            return message

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
        assert names == {"search_code", "search_docs", "count_matches", "reindex"}

        hits = client.call("search_code", query="tokens", top_k=5)["results"]
        assert any(h["path"] == "auth.md" for h in hits)
    finally:
        client.close()


def test_server_starts_with_index_dir(project):
    # The exact combination that shipped broken.
    client = Client(
        "--root", str(project), "--index-dir", ".forge_search", cwd=str(project)
    )
    try:
        client.handshake()
        assert client.call("search_code", query="tokens", top_k=5)["results"]
        assert (project / ".forge_search").is_dir()
    finally:
        client.close()


def test_startup_reports_roots_on_stderr(project):
    # stderr is the only channel proven to reach a VS Code MCP output panel.
    # It is labelled `[warning]` there, which is the client's doing — stderr
    # carries no severity for it to read. Visible and mislabelled beats
    # correctly-labelled and invisible.
    client = Client("--root", str(project), cwd=str(project))
    try:
        client.handshake()
        client.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        client.read()
        client.call("search_code", query="tokens", top_k=5)
    finally:
        err = client.close()

    assert "forge-search: roots:" in err
    # Still nothing else — the SDK's per-request INFO chatter stays off.
    assert "Processing request of type" not in err
    assert "Traceback" not in err


def test_quiet_suppresses_the_stderr_line(project):
    client = Client("--root", str(project), "--quiet", cwd=str(project))
    try:
        client.handshake()
    finally:
        err = client.close()
    assert err.strip() == ""


def test_config_is_announced_as_info_not_a_stderr_warning(project):
    # Where the startup line went. stderr would arrive as `[warning]` in the
    # client log, so a healthy launch would always look like a problem;
    # notifications/message carries a level, so this arrives as information.
    (project / "workspace" / "sub").mkdir(parents=True)
    (project / "workspace" / "sub" / ".git").mkdir()
    client = Client(
        "--root", str(project), "--sub-projects", "workspace", cwd=str(project)
    )
    try:
        client.handshake()
        client.call("search_code", query="tokens", top_k=5)
        client.call("search_code", query="tokens", top_k=5)
    finally:
        err = client.close()

    logs = [n for n in client.notifications if n.get("method") == "notifications/message"]
    assert len(logs) == 1, f"expected exactly one announcement, got {len(logs)}"
    assert logs[0]["params"]["level"] == "info"
    text = logs[0]["params"]["data"]
    assert str(project) in text
    assert "expanded into 1 sub-project(s)" in text
    assert "forge-search: roots:" in err


def test_logging_capability_is_advertised(project):
    # Without this, notifications/message may be dropped by a spec-compliant
    # client — which is what VS Code does. The notification was being sent
    # correctly and reaching nobody.
    client = Client("--root", str(project), cwd=str(project))
    try:
        caps = client.handshake()["result"]["capabilities"]
    finally:
        client.close()
    assert "logging" in caps


def test_set_level_is_honoured_not_just_accepted(project):
    # Accepting logging/setLevel and then ignoring it would be its own quiet
    # lie: a client asking for `warning` must stop receiving `info`.
    client = Client("--root", str(project), cwd=str(project))
    try:
        client.handshake()
        client.send(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "logging/setLevel",
                "params": {"level": "warning"},
            }
        )
        client.read()
        client.call("search_code", query="tokens", top_k=5)
    finally:
        client.close()

    assert not [
        n for n in client.notifications if n.get("method") == "notifications/message"
    ]
