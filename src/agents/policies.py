"""Safety policies and permission gates for autonomous agents."""

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

# Attempt to import Antigravity SDK policy helpers if present
try:
    from google.antigravity.hooks import policy as sdk_policy

    HAS_SDK_POLICY = True
except ImportError:
    sdk_policy = None
    HAS_SDK_POLICY = False


class Decision(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    ASK_USER = "ASK_USER"


@dataclass
class PolicyRule:
    tool_pattern: str
    decision: Decision
    predicate: Callable[[dict[str, Any]], bool] | None = None
    reason: str | None = None
    name: str | None = None

    def matches(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        if self.tool_pattern != "*" and self.tool_pattern != tool_name:
            if self.tool_pattern.endswith("*") and tool_name.startswith(
                self.tool_pattern[:-1]
            ):
                pass
            else:
                return False
        if self.predicate is not None:
            try:
                return bool(self.predicate(arguments))
            except Exception:
                # Fail-closed: if predicate raises, treat as match
                return True
        return True


# Disallowed command patterns: destructive shell operations
DANGEROUS_COMMAND_PATTERNS = [
    r"\brm\s+-[rf]{1,2}\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\s+-[fdx]{1,3}\b",
    r"\bchmod\s+777\b",
    r"\bcurl\s+.*\|\s*(?:bash|sh)\b",
]


def is_dangerous_command(args: dict[str, Any]) -> bool:
    """Predicate checking if CommandLine contains dangerous or mutating commands."""
    cmd = args.get("CommandLine", "") or args.get("command", "")
    for pattern in DANGEROUS_COMMAND_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            return True
    return False


def is_git_push(args: dict[str, Any]) -> bool:
    """Check specifically for git push."""
    cmd = args.get("CommandLine", "") or args.get("command", "")
    return bool(re.search(r"\bgit\s+push\b", cmd, re.IGNORECASE))


def is_outside_workspace(workspace_root: str) -> Callable[[dict[str, Any]], bool]:
    """Predicate checking if file path targets outside the designated workspace."""
    abs_root = os.path.abspath(workspace_root)

    def _check(args: dict[str, Any]) -> bool:
        path = args.get("AbsolutePath") or args.get("TargetFile") or args.get("path")
        if not path:
            return False
        abs_path = os.path.abspath(path)
        # Check if path is inside abs_root
        return not (abs_path == abs_root or abs_path.startswith(abs_root + os.sep))

    return _check


def _flatten_rules(rules: list[Any]) -> list[Any]:
    flat = []
    for r in rules:
        if isinstance(r, list):
            flat.extend(_flatten_rules(r))
        elif r is not None:
            flat.append(r)
    return flat


class PolicyEngine:
    """Evaluates tool invocation requests against prioritized policy rules."""

    def __init__(self, rules: list[Any] | None = None):
        self.rules: list[Any] = _flatten_rules(rules or [])

    def evaluate(
        self, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> Decision:
        args = arguments or {}
        for rule in self.rules:
            # Native PolicyRule
            if hasattr(rule, "matches"):
                if rule.matches(tool_name, args):
                    return rule.decision
            # SDK Policy object from google.antigravity
            elif hasattr(rule, "tool"):
                tool_pattern = getattr(rule, "tool", "*")
                matched = False
                if tool_pattern == "*" or tool_pattern == tool_name:
                    matched = True
                elif tool_pattern.endswith("*") and tool_name.startswith(
                    tool_pattern[:-1]
                ):
                    matched = True

                if matched:
                    predicate = getattr(rule, "when", None)
                    if predicate is not None:
                        try:
                            if not bool(predicate(args)):
                                matched = False
                        except Exception:
                            matched = True

                if matched:
                    raw_dec = getattr(rule, "decision", None)
                    dec_str = str(getattr(raw_dec, "value", raw_dec)).upper()
                    if "DENY" in dec_str:
                        return Decision.DENY
                    elif "ASK" in dec_str:
                        return Decision.ASK_USER
                    elif "APPROVE" in dec_str or "ALLOW" in dec_str:
                        return Decision.ALLOW

        return Decision.ALLOW


def create_agent_policies(
    workspace_path: str | None = None,
    allow_git_push_interactive: bool = True,
) -> list[Any]:
    """
    Build declarative policies for Antigravity LocalAgentConfig.
    If SDK is available, returns SDK policy objects; otherwise returns PolicyRules.
    """
    if HAS_SDK_POLICY and sdk_policy is not None:
        sdk_policies = []
        if workspace_path:
            for tool in (
                "write_to_file",
                "create_file",
                "replace_file_content",
                "edit_file",
            ):
                sdk_policies.append(
                    sdk_policy.deny(
                        tool,
                        when=is_outside_workspace(workspace_path),
                        name=f"deny_outside_workspace_{tool}",
                    )
                )

        # Deny destructive commands unconditionally
        sdk_policies.append(
            sdk_policy.deny(
                "run_command",
                when=is_dangerous_command,
                name="deny_destructive_commands",
            )
        )

        # Gate git push behind ask_user if interactive
        if allow_git_push_interactive:
            sdk_policies.append(
                sdk_policy.ask_user(
                    "run_command",
                    when=is_git_push,
                    name="gate_git_push",
                )
            )

        # Allow safe remaining run_command and other tools
        sdk_policies.append(sdk_policy.allow("run_command"))
        sdk_policies.append(sdk_policy.allow("*"))
        return sdk_policies

    # Fallback to pure PolicyRule objects
    rules = []
    if workspace_path:
        rules.append(
            PolicyRule(
                tool_pattern="write_to_file",
                decision=Decision.DENY,
                predicate=is_outside_workspace(workspace_path),
                reason="Write operation target outside designated workspace directory",
            )
        )
        rules.append(
            PolicyRule(
                tool_pattern="replace_file_content",
                decision=Decision.DENY,
                predicate=is_outside_workspace(workspace_path),
                reason="Edit operation target outside designated workspace directory",
            )
        )

    rules.append(
        PolicyRule(
            tool_pattern="run_command",
            decision=Decision.DENY,
            predicate=is_dangerous_command,
            reason="Destructive command pattern detected in CommandLine",
        )
    )

    if allow_git_push_interactive:
        rules.append(
            PolicyRule(
                tool_pattern="run_command",
                decision=Decision.ASK_USER,
                predicate=is_git_push,
                reason="git push requires interactive approval",
            )
        )

    return rules
