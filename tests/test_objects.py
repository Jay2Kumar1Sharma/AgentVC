"""agentvc/objects.py: AgentVC's own git object store (ADR-0005, ADR-0010, snapshots note).

Objects are written by AgentVC itself in git's loose format, so the differential tests here
check them against real git: `hash-object`, `ls-tree`, `cat-file`, `fsck`, and `gc` on the store.
"""

import errno
import io as stdio
import subprocess
import zlib
from pathlib import Path

import pytest

from agentvc import crash
from agentvc.io import IO
from agentvc.objects import EMPTY_TREE, ObjectCorrupt, ObjectStore, SizeMismatch

FLAT = {
    "README.md": ("100644", ""),
    "a.b": ("100644", ""),
    "a/x.txt": ("100644", ""),  # git sorts the folder "a" as "a/": after "a.b", before "a0"
    "a-b": ("100644", ""),
    "a0": ("100644", ""),
    "src/deep/er/f.py": ("100755", ""),
    "src/name with spaces.txt": ("100644", ""),
    "src/ünïcode-名前.txt": ("100644", ""),
    "src/link": ("120000", ""),
}


def git(store: Path, *args: str, input: bytes | None = None) -> bytes:
    done = subprocess.run(
        ["git", "--git-dir", str(store), *args], input=input, capture_output=True, check=True
    )
    return done.stdout


def make(tmp_path: Path, io: IO | None = None) -> ObjectStore:
    store = ObjectStore(io or IO(), tmp_path / "objects")
    store.ensure()
    return store


def filled(store: ObjectStore) -> dict[str, tuple[str, str]]:
    return {
        path: (mode, store.write_blob(f"content of {path}\n".encode()))
        for path, (mode, _) in FLAT.items()
    }


def test_blobs_hash_exactly_like_git(tmp_path: Path) -> None:
    store = make(tmp_path)
    for data in (b"", b"hello\n", bytes(range(256)) * 300, "名前\r\n".encode()):
        sha = store.write_blob(data)
        assert sha == git(store.path, "hash-object", "--stdin", input=data).decode().strip()
        assert git(store.path, "cat-file", "-p", sha) == data
        assert store.read_blob(sha) == data


def test_streamed_blob_matches_git_and_checks_the_size(tmp_path: Path) -> None:
    store = make(tmp_path)
    data = b"0123456789" * 100_000  # several chunks
    sha = store.write_blob_stream(stdio.BytesIO(data), len(data))
    assert sha == git(store.path, "hash-object", "--stdin", input=data).decode().strip()
    for wrong in (len(data) - 1, len(data) + 1):
        with pytest.raises(SizeMismatch):
            store.write_blob_stream(stdio.BytesIO(data), wrong)
    assert not list((store.path / "objects").glob("tmp_*"))


def test_trees_round_trip_and_pass_git_fsck(tmp_path: Path) -> None:
    store = make(tmp_path)
    flat = filled(store)
    tree = store.write_flat(flat)
    listed = git(store.path, "ls-tree", "-r", "-z", tree).split(b"\0")
    from_git = {}
    for line in filter(None, listed):
        meta, name = line.split(b"\t", 1)
        mode, _kind, sha = meta.decode().split()
        from_git[name.decode("utf-8")] = (mode, sha)
    assert from_git == flat
    assert store.read_flat(tree) == flat
    store.pin(tree)
    git(store.path, "fsck", "--full", "--strict", "--no-dangling")


def test_an_empty_snapshot_is_gits_empty_tree(tmp_path: Path) -> None:
    store = make(tmp_path)
    assert store.write_flat({}) == EMPTY_TREE
    assert store.read_flat(EMPTY_TREE) == {}


def test_names_git_allows_but_file_systems_may_not_round_trip(tmp_path: Path) -> None:
    store = make(tmp_path)
    blob = store.write_blob(b"x")
    flat = {"new\nline": ("100644", blob), "tab\there": ("100644", blob), "...": ("100644", blob)}
    assert store.read_flat(store.write_flat(flat)) == flat


def test_a_changed_object_is_detected_on_read(tmp_path: Path) -> None:
    store = make(tmp_path)
    sha = store.write_blob(b"the agent's version\n")
    obj = store.path / "objects" / sha[:2] / sha[2:]
    obj.chmod(0o644)
    obj.write_bytes(zlib.compress(b"blob 5\0evil\n"))
    with pytest.raises(ObjectCorrupt):
        store.read_blob(sha)
    obj.write_bytes(b"not zlib at all")
    with pytest.raises(ObjectCorrupt):
        store.read_blob(sha)


def test_a_missing_object_is_reported(tmp_path: Path) -> None:
    store = make(tmp_path)
    with pytest.raises(ObjectCorrupt):
        store.read_blob("0" * 40)


def test_disk_full_leaves_no_object_and_no_temp_file(tmp_path: Path) -> None:
    io = IO()
    store = make(tmp_path, io)
    io.faults.fail("write", OSError(errno.ENOSPC, "No space left on device"), match="tmp_")
    with pytest.raises(OSError):
        store.write_blob(b"never stored")
    assert not list((store.path / "objects").glob("tmp_*"))
    assert not store.has(store.hash_blob(b"never stored"))


def test_a_crash_before_the_rename_leaves_only_a_temp_file(tmp_path: Path) -> None:
    store = make(tmp_path)
    with crash.armed("objects.before_rename"), pytest.raises(crash.CrashPointHit):
        store.write_blob(b"half")
    assert not store.has(store.hash_blob(b"half"))
    store.write_blob(b"half")  # a retry succeeds
    assert store.read_blob(store.hash_blob(b"half")) == b"half"


def test_alternates_are_reported_never_used(tmp_path: Path) -> None:
    store = make(tmp_path)
    assert store.problems() == []
    (store.path / "objects" / "info").mkdir(exist_ok=True)
    (store.path / "objects" / "info" / "alternates").write_text("/elsewhere/objects\r\n")
    [problem] = store.problems()
    assert "alternates" in problem


def test_pinned_snapshots_survive_git_gc_and_packed_objects_still_read(tmp_path: Path) -> None:
    store = make(tmp_path)
    flat = filled(store)
    tree = store.write_flat(flat)
    store.pin(tree)
    unpinned = store.write_blob(b"garbage nobody references")
    git(store.path, "gc", "--prune=now", "--quiet")
    assert not (store.path / "objects" / tree[:2] / tree[2:]).exists()  # packed now
    assert store.read_flat(tree) == flat
    assert store.read_blob(flat["README.md"][1]) == b"content of README.md\n"
    assert not store.has(unpinned)


def test_inherited_git_variables_cannot_redirect_agentvcs_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make(tmp_path)
    elsewhere = tmp_path / "elsewhere.git"
    subprocess.run(["git", "init", "-q", "--bare", str(elsewhere)], check=True)
    monkeypatch.setenv("GIT_DIR", str(elsewhere))
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", str(elsewhere / "objects"))
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.fsmonitor=evil'")
    done = store.git(["rev-parse", "--absolute-git-dir"], timeout=10)
    assert done.returncode == 0
    assert Path(done.stdout.decode().strip()).resolve() == store.path.resolve()
    sha = store.write_blob(b"mine")
    assert store.git(["cat-file", "-t", sha], timeout=10).stdout.strip() == b"blob"
