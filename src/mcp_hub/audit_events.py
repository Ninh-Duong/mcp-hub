"""Names of the audit events that Hub modules write, outside the literal `log.write("...")` calls.

Each name is a row in the event table of docs/security-and-logging.md; scripts/check_docs.py checks both ways.
Modules import the constant instead of retyping the string, so a typo fails at import time, not in a log.
"""
from __future__ import annotations

# The Hub published notifications/tools/list_changed after the tool set or a tool definition changed (debounced).
TOOLS_LIST_CHANGED_PUBLISHED = "tools.list_changed.published"
# A child's tool definition differs from the pinned one (warn mode only; the tool keeps running).
TOOL_DEFINITION_CHANGED = "tool.definition.changed"
# A tool description has hidden characters or is longer than security.maxDescriptionChars.
TOOL_DESCRIPTION_SUSPICIOUS = "tool.description.suspicious"
# A call's arguments do not match the child's input schema (protocol.validateArguments warn mode).
TOOL_ARGUMENTS_INVALID = "tool.arguments.invalid"

ALL = (
    TOOLS_LIST_CHANGED_PUBLISHED,
    TOOL_DEFINITION_CHANGED,
    TOOL_DESCRIPTION_SUSPICIOUS,
    TOOL_ARGUMENTS_INVALID,
)
