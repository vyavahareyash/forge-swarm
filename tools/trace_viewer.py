#!/usr/bin/env python3
"""
Standalone Trace Viewer CLI & Streamlit Entrypoint.

Usage:
    # Direct Streamlit:
    streamlit run tools/trace_viewer.py
    # or
    streamlit run src/observability/app.py

    # CLI launcher:
    python tools/trace_viewer.py
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_PATH = REPO_ROOT / "src" / "observability" / "app.py"


def main():
    # If run directly under Streamlit (`streamlit run tools/trace_viewer.py`)
    try:
        import streamlit as st

        # If inside streamlit runtime, load app.py
        if st.runtime.exists():
            import runpy

            sys.path.insert(0, str(REPO_ROOT))
            runpy.run_path(str(APP_PATH), run_name="__main__")
            return
    except ImportError:
        pass

    # Otherwise launch streamlit subprocess
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(APP_PATH),
        *sys.argv[1:],
    ]
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT),
        "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false",
        "STREAMLIT_SERVER_HEADLESS": "true",
    }
    try:
        if not os.access(os.path.expanduser("~"), os.W_OK):
            env["HOME"] = "/tmp"
    except Exception:
        env["HOME"] = "/tmp"

    try:
        sys.exit(subprocess.call(cmd, env=env))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
