"""
Antigravity CLI (agy) Headless Mode Runner.

Executes prompts via `agy -p "<prompt>" --output-format json` to inherit
the host machine's active Google AI subscription OAuth session with zero API keys.
"""
import asyncio
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class AgyNotInstalledError(RuntimeError):
    """Raised when agy CLI binary is not found on host."""
    pass


class AgyAuthenticationError(RuntimeError):
    """Raised when agy CLI requires interactive login."""
    pass


@dataclass
class AgyTurnResult:
    text: str
    prompt_tokens: int = 0
    candidates_tokens: int = 0
    thoughts_tokens: int = 0
    total_tokens: int = 0
    raw_response: dict[str, Any] | None = None

    @property
    def usage_dict(self) -> dict[str, int]:
        return {
            "prompt_token_count": self.prompt_tokens,
            "candidates_token_count": self.candidates_tokens,
            "thoughts_token_count": self.thoughts_tokens,
            "total_token_count": self.total_tokens,
        }


def find_agy_binary() -> str | None:
    """Find path to agy binary on host machine."""
    env_path = os.getenv("AGY_PATH")
    if env_path and os.path.isfile(env_path) and os.access(env_path, os.X_OK):
        return env_path

    which_path = shutil.which("agy")
    if which_path and os.path.isfile(which_path) and os.access(which_path, os.X_OK):
        return which_path

    common_paths = [
        Path.home() / ".local" / "bin" / "agy",
        Path("/Users/yash/.local/bin/agy"),
        Path("/usr/local/bin/agy"),
        Path("/opt/homebrew/bin/agy"),
        Path.home() / ".gemini" / "antigravity-cli" / "bin" / "agy",
    ]
    for p in common_paths:
        if p.is_file() and os.access(str(p), os.X_OK):
            return str(p)

    return None


class AgyRunner:
    """Invokes agy in headless print mode (-p) with JSON output."""

    def __init__(self, agy_path: str | None = None):
        self.agy_path = agy_path or find_agy_binary()

    def is_available(self) -> bool:
        return self.agy_path is not None

    async def execute_turn(
        self,
        prompt: str,
        workspace_dir: str | None = None,
        timeout: float = 180.0,
    ) -> AgyTurnResult:
        """
        Executes a prompt using agy -p and parses the JSON response and token metadata.
        """
        if not self.is_available():
            raise AgyNotInstalledError(
                "Antigravity CLI (`agy`) not found. Install via:\n"
                "  curl -fsSL https://antigravity.google/cli/install.sh | bash\n"
                "Then run `agy` once to login with your Google AI subscription."
            )

        cmd = [
            self.agy_path,  # type: ignore[list-item]
            "-p",
            prompt,
            "--output-format",
            "json",
            "--dangerously-skip-permissions",
        ]

        cwd = workspace_dir or os.getcwd()

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except TimeoutError:
            proc.kill()
            raise TimeoutError(f"agy prompt execution timed out after {timeout}s")

        stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            if "authentication required" in stderr_text.lower() or "login" in stderr_text.lower():
                raise AgyAuthenticationError(
                    f"agy authentication required: {stderr_text}\n"
                    "Please run `agy` in your terminal to complete 1-time subscription OAuth login."
                )
            raise RuntimeError(f"agy execution failed (exit {proc.returncode}): {stderr_text or stdout_text}")

        # Parse JSON output from stdout
        return self._parse_json_output(stdout_text)

    def _parse_json_output(self, stdout_text: str) -> AgyTurnResult:
        """Parse agy JSON output or fallback to text if plain response."""
        try:
            data = json.loads(stdout_text)
            if isinstance(data, dict):
                text = data.get("response") or data.get("text") or data.get("content") or ""
                usage = data.get("usage_metadata") or data.get("usage") or {}
                prompt_tokens = (
                    usage.get("prompt_token_count")
                    or usage.get("prompt_tokens")
                    or usage.get("input_tokens")
                    or 0
                )
                candidates_tokens = (
                    usage.get("candidates_token_count")
                    or usage.get("candidates_tokens")
                    or usage.get("output_tokens")
                    or 0
                )
                thoughts_tokens = (
                    usage.get("thoughts_token_count")
                    or usage.get("thoughts_tokens")
                    or usage.get("thinking_tokens")
                    or 0
                )
                total_tokens = (
                    usage.get("total_token_count")
                    or usage.get("total_tokens")
                    or (prompt_tokens + candidates_tokens + thoughts_tokens)
                )

                return AgyTurnResult(
                    text=text,
                    prompt_tokens=prompt_tokens,
                    candidates_tokens=candidates_tokens,
                    thoughts_tokens=thoughts_tokens,
                    total_tokens=total_tokens,
                    raw_response=data,
                )
        except json.JSONDecodeError:
            pass

        # If stdout is raw plain text, estimate/approximate tokens
        approx_tokens = max(10, len(stdout_text) // 4)
        return AgyTurnResult(
            text=stdout_text,
            prompt_tokens=approx_tokens // 2,
            candidates_tokens=approx_tokens // 2,
            thoughts_tokens=0,
            total_tokens=approx_tokens,
        )
