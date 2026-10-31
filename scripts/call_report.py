"""Where the time of a client's task goes, from the redacted audit log (logs/mcp-hub.jsonl).

    uv run python scripts/call_report.py [--log logs/mcp-hub.jsonl] [--top 10]

It reads only event names, timestamps, tool names, and durations: never arguments or results. It reports
tool time against the gaps between calls (the client's own thinking), how many calls went through
`hub__batch`, and the tool sequences clients repeat most. A repeated sequence whose steps depend on each
other is a candidate for a composite tool in that child's repository.
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Calls closer than this belong to one task; scripted runs (smoke) leave gaps under SCRIPTED_GAP_SECONDS.
TASK_GAP_SECONDS = 90.0
SCRIPTED_GAP_SECONDS = 0.3
SEQUENCE_LENGTHS = (2, 3, 4)
_ENDS = ("tool.call.succeeded", "tool.call.failed", "tool.call.cached")


@dataclass(frozen=True)
class Call:
    request_id: str
    tool: str
    start: float
    end: float


def read_calls(lines: list[str]) -> list[Call]:
    """Completed calls in start order; a batch's own call and its items (`<id>.<i>`) are both kept."""
    starts: dict[str, tuple[str, float]] = {}
    calls = []
    for line in lines:
        try:
            event = json.loads(line)
            stamp = datetime.fromisoformat(event["timestamp"]).timestamp()
        except (ValueError, KeyError, TypeError):
            continue
        request_id, name = str(event.get("request_id")), event.get("event")
        if name == "tool.call.started":
            starts[request_id] = (_exposed_name(event), stamp)
        elif name in _ENDS and request_id in starts:
            tool, start = starts.pop(request_id)
            calls.append(Call(request_id, tool, start, stamp))
    return sorted(calls, key=lambda call: call.start)


def _exposed_name(event: dict) -> str:
    """The name the client called: the log has the child's tool name and its feature (`logs` + `query`)."""
    tool, feature = str(event.get("tool")), event.get("feature")
    return tool if not feature or tool.startswith(f"{feature}__") else f"{feature}__{tool}"


def split_tasks(calls: list[Call]) -> list[list[Call]]:
    """Top-level calls (not batch items) grouped into tasks by the gap between them."""
    top = [call for call in calls if "." not in call.request_id]
    tasks: list[list[Call]] = []
    for call in top:
        if tasks and call.start - tasks[-1][-1].end < TASK_GAP_SECONDS:
            tasks[-1].append(call)
        else:
            tasks.append([call])
    return tasks


def turn_gaps(tasks: list[list[Call]]) -> list[float]:
    return [later.start - earlier.end for task in tasks for earlier, later in zip(task, task[1:])
            if later.start - earlier.end >= SCRIPTED_GAP_SECONDS]


def sequences(tasks: list[list[Call]], top: int) -> list[tuple[tuple[str, ...], int]]:
    counts: Counter[tuple[str, ...]] = Counter()
    for task in tasks:
        tools = [call.tool for call in task]
        for length in SEQUENCE_LENGTHS:
            counts.update(tuple(tools[i:i + length]) for i in range(len(tools) - length + 1))
    return [(sequence, count) for sequence, count in counts.most_common(top) if count > 1]


def report(calls: list[Call], top: int = 10) -> list[str]:
    if not calls:
        return ["No completed tool calls in the log."]
    tasks = split_tasks(calls)
    durations = [call.end - call.start for call in calls if "." not in call.request_id]
    gaps = turn_gaps(tasks)
    batch_items = sum("." in call.request_id for call in calls)
    lines = [
        f"Tool calls: {len(durations)} top-level, {batch_items} inside hub__batch",
        f"Tool time per call: median {statistics.median(durations):.1f}s",
        f"Gap between calls (client thinking): median {statistics.median(gaps):.1f}s over {len(gaps)} gaps"
        if gaps else "Gap between calls: no sequential calls yet",
    ]
    multi = [task for task in tasks if len(task) >= 3]
    if multi:
        tool_time = sum(call.end - call.start for task in multi for call in task)
        wall = sum(task[-1].end - task[0].start for task in multi)
        lines.append(f"Tasks with 3+ calls: {len(multi)}; tools take {tool_time / wall:.0%} of their time")
    lines.append("Most repeated tool sequences:")
    lines.extend(f"  {count:>4}x  {' -> '.join(sequence)}" for sequence, count in sequences(tasks, top))
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report where a client's task time goes, from the audit log.")
    parser.add_argument("--log", type=Path, default=ROOT / "logs" / "mcp-hub.jsonl")
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args(argv)
    try:
        lines = args.log.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        print(f"Could not read the audit log ({type(exc).__name__}).")
        return 1
    print("\n".join(report(read_calls(lines), args.top)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
