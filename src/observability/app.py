"""
Dedicated Streamlit Session & Trace Observability Dashboard.
ADR 0006: Tier 2 Local Observability Console.

Run via:
    streamlit run src/observability/app.py
    streamlit run tools/trace_viewer.py
    python tools/trace_viewer.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Ensure workspace root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from src.observability.data_loader import (  # noqa: E402
    TaskDetailView,
    TokenBurnAnalytics,
    fetch_all_tasks,
    load_task_detail,
)
from src.storage.db import DEFAULT_DB_PATH  # noqa: E402

# -----------------------------------------------------------------------------
# PAGE CONFIGURATION & STYLES
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="SWE Swarm Observability Console",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .status-chip {
        display: inline-block;
        padding: 4px 10px;
        border-radius: 6px;
        font-weight: 700;
        font-size: 0.85rem;
        letter-spacing: 0.5px;
    }
    .status-running {
        background-color: rgba(59, 130, 246, 0.15);
        color: #3b82f6;
        border: 1px solid rgba(59, 130, 246, 0.3);
    }
    .status-completed, .status-success {
        background-color: rgba(16, 185, 129, 0.15);
        color: #10b981;
        border: 1px solid rgba(16, 185, 129, 0.3);
    }
    .status-failed {
        background-color: rgba(239, 68, 68, 0.15);
        color: #ef4444;
        border: 1px solid rgba(239, 68, 68, 0.3);
    }
    .status-paused_budget {
        background-color: rgba(245, 158, 11, 0.15);
        color: #f59e0b;
        border: 1px solid rgba(245, 158, 11, 0.3);
    }
    .thinking-high {
        background-color: rgba(139, 92, 246, 0.15);
        color: #8b5cf6;
        padding: 2px 8px;
        border-radius: 4px;
        font-size: 0.75rem;
        font-weight: 600;
    }
    .thinking-medium {
        background-color: rgba(59, 130, 246, 0.15);
        color: #3b82f6;
        padding: 2px 8px;
        border-radius: 4px;
        font-size: 0.75rem;
        font-weight: 600;
    }
    .thinking-minimal {
        background-color: rgba(107, 114, 128, 0.15);
        color: #6b7280;
        padding: 2px 8px;
        border-radius: 4px;
        font-size: 0.75rem;
        font-weight: 600;
    }
    .persona-card {
        border-left: 4px solid #3b82f6;
        background-color: rgba(128, 128, 128, 0.05);
        padding: 12px;
        border-radius: 6px;
        margin-bottom: 12px;
    }
    .persona-atlas { border-left-color: #8b5cf6; }
    .persona-forge { border-left-color: #f97316; }
    .persona-sentinel { border-left-color: #10b981; }
    .persona-mira { border-left-color: #ec4899; }
    .persona-vivek { border-left-color: #06b6d4; }
    .persona-dhruv { border-left-color: #eab308; }
    </style>
    """,
    unsafe_allow_html=True,
)


def get_status_badge_html(status: str) -> str:
    s_upper = (status or "UNKNOWN").upper()
    css_cls = "status-running"
    if s_upper in ("COMPLETED", "SUCCESS"):
        css_cls = "status-completed"
    elif s_upper in ("FAILED", "ABORTED"):
        css_cls = "status-failed"
    elif "PAUSED" in s_upper:
        css_cls = "status-paused_budget"
    return f'<span class="status-chip {css_cls}">{s_upper}</span>'


def get_persona_card_class(persona: str) -> str:
    low = (persona or "").lower()
    if "atlas" in low:
        return "persona-card persona-atlas"
    if "forge" in low:
        return "persona-card persona-forge"
    if "sentinel" in low:
        return "persona-card persona-sentinel"
    if "mira" in low:
        return "persona-card persona-mira"
    if "vivek" in low:
        return "persona-card persona-vivek"
    if "dhruv" in low or "warden" in low:
        return "persona-card persona-dhruv"
    return "persona-card"


def get_persona_emoji(persona: str) -> str:
    low = (persona or "").lower()
    if "atlas" in low or "architect" in low:
        return "🧭"
    if "forge" in low or "coder" in low:
        return "🔨"
    if "sentinel" in low or "qa" in low:
        return "🛡️"
    if "mira" in low or "specifier" in low:
        return "🎯"
    if "vivek" in low or "reviewer" in low:
        return "🧐"
    if "dhruv" in low or "warden" in low or "quota" in low:
        return "⚖️"
    return "🤖"


# -----------------------------------------------------------------------------
# SIDEBAR CONTROLS & TASK SELECTION
# -----------------------------------------------------------------------------
db_path = os.getenv("SWARM_DB_PATH", DEFAULT_DB_PATH)
sessions_dir = os.getenv("SWARM_SESSIONS_DIR", str(REPO_ROOT / "storage" / "sessions"))

with st.sidebar:
    st.title("⚡ SWE Swarm")
    st.caption("Trace & Observability Console (Tier 2)")

    # Auto-refresh mechanism for actively running tasks
    auto_refresh = st.checkbox("Auto-refresh active tasks (5s)", value=False)
    if auto_refresh:
        time.sleep(5)
        st.rerun()

    st.divider()
    st.subheader("Filter Tasks")

    status_filter = st.selectbox(
        "Status Filter:",
        ["ALL", "RUNNING", "COMPLETED", "FAILED", "PAUSED_BUDGET"],
        index=0,
    )

    search_query = st.text_input("Search Task / Issue ID:", value="")

    # Fetch tasks
    tasks = fetch_all_tasks(
        status=status_filter if status_filter != "ALL" else None,
        search=search_query.strip() if search_query else None,
        db_path=db_path,
    )

    # Sidebar Summary Metrics
    total_tasks = len(tasks)
    running_tasks = sum(1 for t in tasks if t.get("status") == "RUNNING")
    completed_tasks = sum(
        1 for t in tasks if t.get("status") in ("COMPLETED", "SUCCESS")
    )
    total_tokens_all = sum(t.get("current_tokens", 0) for t in tasks)

    st.write(
        f"📊 **Tasks**: {total_tasks} | 🏃 **Active**: {running_tasks} | ✅ **Done**: {completed_tasks}"
    )
    st.write(f"🪙 **Cumulative Spend**: `{total_tokens_all:,}` tokens")

    st.divider()
    st.subheader("Select Task")

    if not tasks:
        st.info("No tasks found matching current filters.")
        st.stop()

    task_options = {}
    for t in tasks:
        tid = t["task_id"]
        iid = t.get("issue_id") or "N/A"
        st_val = t.get("status", "UNKNOWN")
        tokens = t.get("current_tokens", 0)
        turns = t.get("turn_count", 0)
        label = f"[{st_val}] {tid} (Issue #{iid} • {tokens:,} tok • {turns} t)"
        task_options[label] = tid

    selected_label = st.selectbox(
        "Task Trajectory:", list(task_options.keys()), index=0
    )
    selected_task_id = task_options[selected_label]

    if st.button("↺ Force Reload", use_container_width=True):
        st.rerun()


# -----------------------------------------------------------------------------
# MAIN DASHBOARD CONTENT
# -----------------------------------------------------------------------------
task_view: TaskDetailView = load_task_detail(
    selected_task_id, db_path=db_path, sessions_dir=sessions_dir
)

task_rec = task_view.task_record or {}
current_status = task_rec.get("status", "UNKNOWN")
issue_id = task_rec.get("issue_id", "N/A")
thread_ts = task_rec.get("slack_thread_ts", "None")

# Header & Status Badge
col_header, col_badge = st.columns([3, 1])
with col_header:
    st.title(f"Task: `{task_view.task_id}`")
    st.caption(
        f"Issue: **{issue_id}** &bull; Slack Thread: `{thread_ts}` &bull; Last Updated: `{task_rec.get('updated_at', 'N/A')}`"
    )
with col_badge:
    st.write("")
    st.markdown(get_status_badge_html(current_status), unsafe_allow_html=True)

# Telemetry Banner Metrics
analytics: TokenBurnAnalytics = task_view.analytics
m1, m2, m3, m4 = st.columns(4)
m1.metric("Status", current_status)
m2.metric(
    "Tokens Spent",
    f"{analytics.total_tokens:,}",
    f"{analytics.percentage_budget_used}% of limit",
)
m3.metric(
    "Budget Limit",
    f"{analytics.budget_limit:,}",
    "Ceiling" if not analytics.is_budget_exceeded else "EXCEEDED",
)
m4.metric(
    "Turns Executed",
    f"{analytics.current_turns} / {analytics.turn_limit}",
    f"{max(0, analytics.turn_limit - analytics.current_turns)} remaining",
)

pct_burn = min(1.0, analytics.percentage_budget_used / 100.0)
st.progress(
    pct_burn,
    text=f"Task Budget Burn: {analytics.total_tokens:,} / {analytics.budget_limit:,} tokens ({analytics.percentage_budget_used}%)",
)

if analytics.is_budget_exceeded or current_status == "PAUSED_BUDGET":
    st.error(
        f"🛑 **TASK PAUSED ON BUDGET LIMIT**: {analytics.total_tokens:,} consumed exceeds limit of {analytics.budget_limit:,} tokens."
    )

st.write("")

# -----------------------------------------------------------------------------
# TABS
# -----------------------------------------------------------------------------
tab_plan, tab_timeline, tab_analytics, tab_qa, tab_audit = st.tabs(
    [
        "📋 1. Implementation Plan & Spec",
        "⏱️ 2. Multi-Agent Timeline",
        "📈 3. Token Burn Analytics",
        "🧪 4. QA Report & Git Diff",
        "📜 5. Audit Ledger & Raw Data",
    ]
)

# =============================================================================
# TAB 1: IMPLEMENTATION PLAN & LIVING SPEC
# =============================================================================
with tab_plan:
    st.subheader("Architectural Blueprint & Living Spec")

    plan = task_view.plan
    if plan:
        col_p1, col_p2, col_p3 = st.columns(3)
        col_p1.metric("Target Issue", f"#{plan.issue_number}")
        col_p2.metric("Target Branch", plan.branch_name)
        col_p3.metric("Estimated Budget", f"{plan.estimated_token_budget:,} tok")

        st.markdown(f"**High-Level Strategy**: {plan.summary}")

        st.divider()
        st.subheader(f"Planned Subtasks ({len(plan.subtasks)})")

        for idx, sub in enumerate(plan.subtasks, 1):
            with st.container():
                st.markdown(f"#### Subtask {idx}: `{sub.id}` — **{sub.title}**")
                col_sub_files, col_sub_crit = st.columns([1, 2])
                with col_sub_files:
                    st.write("**Target Files:**")
                    if sub.target_files:
                        for f in sub.target_files:
                            st.markdown(f"- `{f}`")
                    else:
                        st.caption("None specified")
                with col_sub_crit:
                    st.write("**Acceptance Criteria:**")
                    st.info(sub.acceptance_criteria)
                st.write("")
    else:
        st.info("No structured ImplementationPlan generated for this task yet.")

    # Living Spec Section
    st.divider()
    st.subheader("Authoritative Living Specification")
    spec = task_view.living_spec
    if spec:
        st.caption(
            f"Spec Version: **v{spec.version}** &bull; Last Updated: `{spec.updated_at}`"
        )
        col_ls1, col_ls2 = st.columns(2)
        with col_ls1:
            st.markdown("##### Core Requirements")
            if spec.requirements:
                for req in spec.requirements:
                    st.markdown(f"- {req}")
            else:
                st.caption("No requirements recorded.")

            st.markdown("##### Open Questions")
            if spec.open_questions:
                for q in spec.open_questions:
                    st.warning(f"❓ {q}")
            else:
                st.success("No open questions remaining.")

        with col_ls2:
            st.markdown("##### Architectural Decisions")
            if spec.architecture_decisions:
                for ad in spec.architecture_decisions:
                    st.markdown(f"- {ad}")
            else:
                st.caption("No decisions logged.")

            st.markdown("##### Review State")
            st.json(spec.review_state or {"review_state": "Pending"})
    else:
        st.info("No Living Spec recorded in SQLite for this task.")


# =============================================================================
# TAB 2: MULTI-AGENT TIMELINE & WATERFALL
# =============================================================================
with tab_timeline:
    st.subheader("Turn-by-Turn Multi-Agent Execution Trajectory")

    turns = task_view.turns
    if not turns:
        st.info("No turns recorded in session trajectory yet.")
    else:
        all_personas = sorted(list(set(t.persona for t in turns)))
        filter_persona = st.selectbox(
            "Filter by Persona:", ["ALL"] + all_personas, index=0
        )

        filtered_turns = [
            t for t in turns if filter_persona == "ALL" or t.persona == filter_persona
        ]

        st.caption(f"Showing **{len(filtered_turns)}** / **{len(turns)}** turns")

        for turn in filtered_turns:
            card_cls = get_persona_card_class(turn.persona)
            emoji = get_persona_emoji(turn.persona)
            t_level_cls = f"thinking-{turn.thinking_level.lower()}"

            st.markdown(
                f"""
                <div class="{card_cls}">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <div>
                            <b>{emoji} Turn {turn.turn_index}: {turn.persona}</b>
                            <span style="opacity:0.75; font-size:0.85rem;">({turn.role})</span>
                            &nbsp;<span class="{t_level_cls}">{turn.thinking_level}</span>
                        </div>
                        <div style="font-size:0.85rem; font-weight:600;">
                            🪙 {turn.tokens_spent:,} tokens &bull; <span style="opacity:0.7;">{turn.timestamp}</span>
                        </div>
                    </div>
                    <div style="margin-top:6px; font-size:0.95rem;">
                        <b>Action:</b> {turn.action}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            # Details expander for prompts, thoughts, tool calls, and results
            with st.expander(
                f"🔍 Inspect Turn {turn.turn_index} Telemetry & Tool Calls",
                expanded=False,
            ):
                if turn.prompt:
                    st.markdown("**Prompt:**")
                    st.code(turn.prompt)

                if turn.thoughts:
                    st.markdown("**Internal Thoughts / Extended Thinking:**")
                    st.info(turn.thoughts)

                if turn.tool_calls:
                    st.markdown(f"**Tool Invocations ({len(turn.tool_calls)}):**")
                    for tc in turn.tool_calls:
                        st.markdown(f"- 🛠️ `{tc.tool_name}`")
                        st.json(tc.arguments)

                if turn.tool_results:
                    st.markdown(f"**Tool Results ({len(turn.tool_results)}):**")
                    for tr in turn.tool_results:
                        st.markdown(f"- 📤 `{tr.tool_name}` ({tr.status}):")
                        st.code(tr.output)

                if turn.response_text:
                    st.markdown("**Response Text:**")
                    st.markdown(turn.response_text)

                if turn.details:
                    st.markdown("**Turn Metadata:**")
                    st.json(turn.details)


# =============================================================================
# TAB 3: TOKEN BURN ANALYTICS
# =============================================================================
with tab_analytics:
    st.subheader("Token Burn & Quota Telemetry")

    if not analytics.burn_records:
        st.info("No token turn telemetry recorded for this task.")
    else:
        df_burn = pd.DataFrame(analytics.burn_records)

        # Charts: Cumulative vs Budget & Per-Turn Burn
        col_chart1, col_chart2 = st.columns(2)
        with col_chart1:
            st.markdown("##### Cumulative Token Spend vs Task Budget")
            chart_data = df_burn.set_index("turn_index")[
                ["cumulative_tokens", "budget_limit"]
            ]
            st.line_chart(chart_data)

        with col_chart2:
            st.markdown("##### Per-Turn Token Consumption")
            bar_data = df_burn.set_index("turn_index")[
                [
                    "total_tokens",
                    "prompt_tokens",
                    "candidates_tokens",
                    "thoughts_tokens",
                ]
            ]
            st.bar_chart(bar_data)

        st.divider()
        st.subheader("Persona Allocation Breakdown")

        if analytics.persona_allocations:
            col_p_table, col_p_chart = st.columns([3, 2])
            with col_p_table:
                df_persona = pd.DataFrame(
                    [
                        {
                            "Persona": pb.persona,
                            "Tokens Spent": pb.tokens,
                            "Spend %": f"{pb.percentage}%",
                            "Turns Count": pb.turns_count,
                        }
                        for pb in analytics.persona_allocations
                    ]
                )
                st.dataframe(df_persona, use_container_width=True)

            with col_p_chart:
                df_p_chart = pd.DataFrame(
                    {
                        "Persona": [pb.persona for pb in analytics.persona_allocations],
                        "Tokens": [pb.tokens for pb in analytics.persona_allocations],
                    }
                ).set_index("Persona")
                st.bar_chart(df_p_chart)


# =============================================================================
# TAB 4: QA TEST REPORT & GIT DIFF
# =============================================================================
with tab_qa:
    st.subheader("Sentinel QA Verification & Patch Inspector")

    qa_report = task_view.final_report
    git_diff = task_view.git_diff

    if qa_report:
        col_qa1, col_qa2, col_qa3 = st.columns(3)
        with col_qa1:
            if qa_report.passed:
                st.success("✅ **QA GATE: PASSED**")
            else:
                st.error("❌ **QA GATE: FAILED**")
        with col_qa2:
            st.metric("Total Tests Executed", qa_report.total_tests)
        with col_qa3:
            st.metric("Failed Tests", qa_report.failed_tests)

        st.markdown(f"**Command Executed:** `{qa_report.command_executed}`")

        if qa_report.suggested_fix_files:
            st.write("**Suggested Fix Targets:**")
            for sf in qa_report.suggested_fix_files:
                st.markdown(f"- `{sf}`")

        if qa_report.failure_summary:
            st.markdown("##### Failure Traceback / Test Output")
            st.code(qa_report.failure_summary, language="text")
    else:
        st.info("No QA Test Report recorded yet for this task.")

    st.divider()
    st.subheader("Synthesized Git Patch Diff")
    if git_diff and git_diff.strip():
        st.code(git_diff, language="diff")
    else:
        st.info("No git diff recorded for this task session.")


# =============================================================================
# TAB 5: AUDIT LEDGER & RAW DATA
# =============================================================================
with tab_audit:
    st.subheader("Raw SQLite Ledger & Session JSON Inspector")

    col_a1, col_a2 = st.columns(2)
    with col_a1:
        st.markdown("##### SQLite `tasks` Record")
        st.json(task_rec or {})

    with col_a2:
        st.markdown(f"##### Slack Events ({len(task_view.slack_events)})")
        if task_view.slack_events:
            st.dataframe(pd.DataFrame(task_view.slack_events), use_container_width=True)
        else:
            st.caption("No Slack interactions recorded for this task.")

    st.divider()
    st.subheader("Complete Session Trajectory JSON")
    if task_view.trajectory:
        raw_json_str = json.dumps(
            task_view.trajectory.model_dump(), indent=2, ensure_ascii=False
        )
        st.download_button(
            label="💾 Download Session Trajectory JSON",
            data=raw_json_str,
            file_name=f"session_{task_view.task_id}.json",
            mime="application/json",
        )
        with st.expander("Inspect Raw Trajectory JSON"):
            st.code(raw_json_str, language="json")
    else:
        st.info("No raw JSON trajectory file on disk.")
