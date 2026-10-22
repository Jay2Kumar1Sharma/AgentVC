"""Write the schema-1 ledger fixture with an old AgentVC's own code (ADR-0007 upgrade tests).

Usage, from the repository root, with a checkout of the old version (session 4 used
`git worktree add <dir> alpha/session-3`):

    python tests/fixtures/ledger/make_schema_1.py <old-checkout> tests/fixtures/ledger/schema-1

It replays the captured Claude Code 2.1.302 session through the old recorder into a fresh
ledger, then writes `ledger.db` and `head.json` (the newest entry's seq and hash, which an
upgrade test puts in the anchor). The old code is imported from <old-checkout>, never from this
tree.
"""

import json
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath

old, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
fixtures = Path(__file__).resolve().parents[1] / "hosts" / "claude-code" / "2.1.302"
sys.path.insert(0, str(old))

from agentvc import ledger as old_ledger  # noqa: E402
from agentvc.adapters import claude_code  # noqa: E402
from agentvc.clock import FakeClock  # noqa: E402
from agentvc.events import Event  # noqa: E402
from agentvc.io import IO  # noqa: E402
from agentvc.recorder import Recorder  # noqa: E402

assert Path(old_ledger.__file__).resolve().is_relative_to(old), old_ledger.__file__
assert old_ledger.SCHEMA_VERSION == 1, old_ledger.SCHEMA_VERSION

# The capture's (scrubbed) project folder, as the payloads' cwd spells it.
CAPTURED = str(PureWindowsPath("~", "AppData", "Local", "Temp", "agentvc-capture-2.1.302"))

with tempfile.TemporaryDirectory() as tmp:
    work = Path(tmp)
    clock = FakeClock(datetime(2026, 10, 22, tzinfo=UTC))
    lg = old_ledger.Ledger.open(
        IO(), work / "ledger.db", workspace=work, anchor_dir=work / "anchors", clock=clock
    )
    try:
        recorder = Recorder(lg, Path(CAPTURED), clock)
        for path in sorted(fixtures.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))["payload"]
            event = claude_code.parse(payload, record_prompts=False)
            if isinstance(event, Event):
                recorder.apply(event, timeout=5)
                clock.advance(1)
        assert lg.verify().status == "ok"
        assert lg.rows("steps"), "nothing was recorded"
    finally:
        lg.close()
    [anchor] = (work / "anchors").glob("*.json")
    head = json.loads(anchor.read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(work / "ledger.db", out / "ledger.db")
    (out / "head.json").write_text(
        json.dumps({"seq": head["seq"], "hash": head["hash"]}) + "\n", encoding="utf-8"
    )
print(f"wrote {out / 'ledger.db'} (head seq {head['seq']})")
