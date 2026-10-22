"""AgentVC's own git object store (ADR-0005, ADR-0010, design note snapshots.md).

`.agentvc/objects` is a bare git directory that AgentVC writes itself: blobs and trees as git
loose objects, so any `git` can read and check them, without starting a git process per
snapshot. It owns every object it references (no alternates), pins every snapshot tree with a
ref, and checks each object's hash when reading it back.
"""

from __future__ import annotations

import hashlib
import os
import threading
import zlib
from collections.abc import Sequence
from itertools import count
from pathlib import Path
from typing import Protocol

from agentvc import crash
from agentvc.io import IO, RunResult

BEFORE_RENAME = crash.declare("objects.before_rename")

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
TREE_MODE = "40000"
CHUNK = 1024 * 1024
GIT_TIMEOUT_S = 30

# path ("dir/file", "/"-separated) -> (mode, blob sha)
Flat = dict[str, tuple[str, str]]

_CONFIG = (
    b"[core]\n\trepositoryformatversion = 0\n\tfilemode = false\n\tbare = true\n"
    b"\tfsmonitor = false\n[gc]\n\tauto = 0\n"
)


class Readable(Protocol):
    def read(self, n: int, /) -> bytes: ...


class ObjectCorrupt(Exception):
    """An object is missing, unreadable, or its content doesn't match its name."""


class SizeMismatch(Exception):
    """A stream held a different number of bytes than its size said (it changed while read)."""


def _encode(name: str) -> bytes:
    return os.fsencode(name)


def _decode(name: bytes) -> str:
    return os.fsdecode(name)


class ObjectStore:
    def __init__(self, io: IO, path: Path) -> None:
        self.io = io
        self.path = path
        self.objects = path / "objects"
        self._counter = count()
        self._lock = threading.Lock()

    # --- layout -----------------------------------------------------------------------------

    def ensure(self) -> None:
        for folder in (self.objects, self.path / "refs" / "agentvc" / "keep"):
            self.io.mkdir(folder)
        if not self.io.exists(self.path / "HEAD"):
            self.io.write_atomic(self.path / "HEAD", b"ref: refs/heads/main\n")
        if not self.io.exists(self.path / "config"):
            self.io.write_atomic(self.path / "config", _CONFIG)
        if not self.io.exists(self.path / "empty-gitconfig"):
            self.io.write_atomic(self.path / "empty-gitconfig", b"")

    def problems(self) -> list[str]:
        """Reasons the store isn't self-contained; snapshots don't count as protected then."""
        out = []
        alternates = self.objects / "info" / "alternates"
        if self.io.exists(alternates):
            out.append(
                f"{alternates} exists: the store would borrow objects it doesn't own (ADR-0005)"
            )
        return out

    # --- writing ----------------------------------------------------------------------------

    def _object_path(self, sha: str) -> Path:
        return self.objects / sha[:2] / sha[2:]

    def has(self, sha: str) -> bool:
        return self.io.exists(self._object_path(sha))

    def _temp(self) -> Path:
        with self._lock:
            n = next(self._counter)
        return self.objects / f"tmp_obj_{os.getpid()}_{threading.get_ident()}_{n}"

    def _install(self, temp: Path, sha: str) -> None:
        final = self._object_path(sha)
        try:
            crash.hit(BEFORE_RENAME)
            if self.io.exists(final):
                self.io.remove(temp)
                return
            self.io.mkdir(final.parent)
            try:
                self.io.replace(temp, final)
            except OSError:
                # Another writer may have installed the same object (or a reader holds it open).
                if not self.io.exists(final):
                    raise
                self.io.remove(temp)
        except BaseException:
            self.io.remove(temp)
            raise

    def _write(self, kind: str, data: bytes) -> str:
        raw = f"{kind} {len(data)}\0".encode() + data
        sha = hashlib.sha1(raw, usedforsecurity=False).hexdigest()
        if self.has(sha):
            return sha
        temp = self._temp()
        try:
            self.io.write_file(temp, zlib.compress(raw, 1), fsync=False)
        except BaseException:
            self.io.remove(temp)
            raise
        self._install(temp, sha)
        return sha

    @staticmethod
    def hash_blob(data: bytes) -> str:
        raw = f"blob {len(data)}\0".encode() + data
        return hashlib.sha1(raw, usedforsecurity=False).hexdigest()

    def write_blob(self, data: bytes) -> str:
        return self._write("blob", data)

    def write_blob_stream(self, stream: Readable, size: int) -> str:
        """Store ``size`` bytes from ``stream``; `SizeMismatch` if it holds more or fewer."""
        header = f"blob {size}\0".encode()
        digest = hashlib.sha1(header, usedforsecurity=False)
        packer = zlib.compressobj(1)
        temp = self._temp()
        try:
            with self.io.open_new(temp) as out:
                out.write(packer.compress(header))
                seen = 0
                while chunk := stream.read(CHUNK):
                    seen += len(chunk)
                    if seen > size:
                        raise SizeMismatch(f"more than {size} bytes")
                    digest.update(chunk)
                    out.write(packer.compress(chunk))
                if seen != size:
                    raise SizeMismatch(f"{seen} bytes, expected {size}")
                out.write(packer.flush())
        except BaseException:
            self.io.remove(temp)
            raise
        sha = digest.hexdigest()
        self._install(temp, sha)
        return sha

    def write_tree(self, entries: Sequence[tuple[str, str, str]]) -> str:
        """One tree object from (mode, name, sha) entries, in git's order."""

        def key(entry: tuple[str, str, str]) -> bytes:
            mode, name, _ = entry
            return _encode(name) + (b"/" if mode == TREE_MODE else b"")

        body = b"".join(
            f"{mode} ".encode() + _encode(name) + b"\0" + bytes.fromhex(sha)
            for mode, name, sha in sorted(entries, key=key)
        )
        return self._write("tree", body)

    def write_flat(self, flat: Flat) -> str:
        """The tree for a whole snapshot, from its paths."""
        root: dict[str, object] = {}
        for path, entry in flat.items():
            *dirs, name = path.split("/")
            node = root
            for part in dirs:
                child = node.setdefault(part, {})
                assert isinstance(child, dict), f"{path}: a file is in the way"
                node = child
            node[name] = entry
        return self._write_node(root)

    def _write_node(self, node: dict[str, object]) -> str:
        entries = []
        for name, value in node.items():
            if isinstance(value, dict):
                entries.append((TREE_MODE, name, self._write_node(value)))
            else:
                assert isinstance(value, tuple)
                mode, sha = value
                entries.append((mode, name, sha))
        return self.write_tree(entries)

    def pin(self, sha: str) -> None:
        """Keep ``sha`` (and all it references) through any `gc` of the store (ADR-0005)."""
        ref = self.path / "refs" / "agentvc" / "keep" / sha
        if not self.io.exists(ref):
            self.io.write_atomic(ref, sha.encode() + b"\n", fsync=False)

    # --- reading ----------------------------------------------------------------------------

    def read(self, sha: str) -> tuple[str, bytes]:
        try:
            packed = self.io.read_bytes(self._object_path(sha))
        except FileNotFoundError:
            return self._read_packed(sha)
        except OSError as exc:
            raise ObjectCorrupt(f"object {sha}: can't read it ({exc})") from exc
        try:
            raw = zlib.decompress(packed)
        except zlib.error as exc:
            raise ObjectCorrupt(f"object {sha} is damaged ({exc})") from exc
        if hashlib.sha1(raw, usedforsecurity=False).hexdigest() != sha:
            raise ObjectCorrupt(f"object {sha} doesn't match its content")
        header, _, data = raw.partition(b"\0")
        kind, _, size = header.decode("ascii", "replace").partition(" ")
        if not size.isdigit() or int(size) != len(data):
            raise ObjectCorrupt(f"object {sha} has a bad header")
        return kind, data

    def _read_packed(self, sha: str) -> tuple[str, bytes]:
        try:
            done = self.git(
                ["cat-file", "--batch"], timeout=GIT_TIMEOUT_S, input=sha.encode() + b"\n"
            )
        except OSError as exc:
            raise ObjectCorrupt(f"object {sha} is missing (git unavailable: {exc})") from exc
        head, _, rest = done.stdout.partition(b"\n")
        parts = head.decode("ascii", "replace").split()
        if done.returncode != 0 or len(parts) != 3 or parts[1] == "missing":
            raise ObjectCorrupt(f"object {sha} is missing")
        kind, size = parts[1], int(parts[2])
        data = rest[:size]
        raw = f"{kind} {size}\0".encode() + data
        if len(data) != size or hashlib.sha1(raw, usedforsecurity=False).hexdigest() != sha:
            raise ObjectCorrupt(f"object {sha} doesn't match its content")
        return kind, data

    def read_blob(self, sha: str) -> bytes:
        kind, data = self.read(sha)
        if kind != "blob":
            raise ObjectCorrupt(f"object {sha} is a {kind}, not a blob")
        return data

    def read_tree(self, sha: str) -> list[tuple[str, str, str]]:
        kind, data = self.read(sha)
        if kind != "tree":
            raise ObjectCorrupt(f"object {sha} is a {kind}, not a tree")
        entries, pos = [], 0
        try:
            while pos < len(data):
                space = data.index(b" ", pos)
                nul = data.index(b"\0", space)
                mode = data[pos:space].decode("ascii")
                name = _decode(data[space + 1 : nul])
                entries.append((mode, name, data[nul + 1 : nul + 21].hex()))
                pos = nul + 21
        except ValueError as exc:  # includes UnicodeDecodeError
            raise ObjectCorrupt(f"tree {sha} is malformed") from exc
        return entries

    def read_flat(self, sha: str) -> Flat:
        flat: Flat = {}
        self._flatten(sha, "", flat)
        return flat

    def _flatten(self, sha: str, prefix: str, flat: Flat) -> None:
        for mode, name, child in self.read_tree(sha):
            if mode == TREE_MODE:
                self._flatten(child, f"{prefix}{name}/", flat)
            else:
                flat[prefix + name] = (mode, child)

    # --- git, isolated ----------------------------------------------------------------------

    def git(
        self,
        args: Sequence[str],
        *,
        timeout: float,
        input: bytes | None = None,
        work_tree: Path | None = None,
    ) -> RunResult:
        """Run git on this store only: no inherited GIT_* variables, no user or system config."""
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        env.update(
            GIT_DIR=str(self.path),
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=str(self.path / "empty-gitconfig"),
            GIT_TERMINAL_PROMPT="0",
        )
        if work_tree is not None:
            env["GIT_WORK_TREE"] = str(work_tree)
            env["GIT_INDEX_FILE"] = str(self.path / "no-index")
        argv = ["git", "-c", "core.fsmonitor=false", "-c", "core.quotepath=false", *args]
        return self.io.run(argv, timeout=timeout, input=input, env=env, cwd=work_tree)
