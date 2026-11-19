from __future__ import annotations

import shlex

from ..paths import herjev_home
from ..registry import register
from .command import CommandSource


@register("source", "ssh")
class SSHSource(CommandSource):
    """Tail logs on a remote server over SSH (uses the system ``ssh`` client,
    so ``~/.ssh/config``, agents, jump hosts and keys all just work).

    Host keys are pinned on first use (``accept-new``) into
    ``$HERJEV_HOME/known_hosts`` so it works in a fresh container; set
    ``strict_host_key_checking: "yes"`` and ``known_hosts_file`` to pre-pin.

    Either give ``paths`` (tailed with ``tail -F``), ``journal_units``
    (``journalctl -f``), or a raw remote ``command``.
    """

    def __init__(self, name: str, target: str, paths: list[str] | None = None,
                 journal_units: list[str] | None = None, command: str | None = None,
                 ssh_options: list[str] | None = None, port: int | None = None,
                 identity_file: str | None = None, known_hosts_file: str | None = None,
                 strict_host_key_checking: str = "accept-new", **kw):
        if not (paths or journal_units or command):
            raise ValueError(f"ssh source {name!r} needs one of: paths, journal_units, command")
        kw.setdefault("host", target.split("@")[-1])
        super().__init__(name, command=["ssh"], **kw)
        self.target = target
        self.paths = paths or []
        self.journal_units = journal_units or []
        self.remote_command = command
        self.ssh_options = ssh_options or []
        self.port = port
        self.identity_file = identity_file
        self.known_hosts_file = known_hosts_file or str(herjev_home() / "known_hosts")
        self.strict_host_key_checking = strict_host_key_checking

    def remote(self) -> str:
        if self.remote_command:
            return self.remote_command
        if self.paths:
            return "tail -n0 -F " + " ".join(shlex.quote(p) for p in self.paths)
        units = " ".join(f"-u {shlex.quote(u)}" for u in self.journal_units)
        return f"journalctl -f -n0 -o cat {units}"

    def build_argv(self) -> list[str]:
        herjev_home().mkdir(parents=True, exist_ok=True)
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=15",
                "-o", "ServerAliveCountMax=3",
                "-o", f"StrictHostKeyChecking={self.strict_host_key_checking}",
                "-o", f"UserKnownHostsFile={self.known_hosts_file}"]
        if self.port:
            argv += ["-p", str(self.port)]
        if self.identity_file:
            argv += ["-i", self.identity_file]
        for opt in self.ssh_options:
            argv += ["-o", opt]
        return argv + [self.target, self.remote()]
