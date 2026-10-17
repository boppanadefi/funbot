"""
Gemini AI Chat Panel UI Component.
Renders thought segregation, streaming markdown responses, and interactive follow-up suggestions.
"""

from typing import Dict, List
import streamlit as st
from src.ui.chat_backend import TelemetryChatBackend


def render_chat_panel(backend: TelemetryChatBackend):
    st.markdown(
        """
        <div class="metric-card" style="margin-bottom: 1rem;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <span style="font-weight: 700; font-size: 0.95rem;">✦ Gemini Telemetry Assistant</span>
                    <span style="color: var(--text-muted); font-size: 0.78rem; margin-left: 8px;">Natural language analytics over observational Solana telemetry</span>
                </div>
                <span class="badge badge-blue">GEMINI 2.5 ANALYTICS</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if "chat_history" not in st.session_state:
        st.session_state.chat_history = [
            {
                "role": "assistant",
                "thoughts": "[Initialized connection to local telemetry buffer]",
                "content": (
                    "Hello! I am your **Funbot Observational Telemetry Assistant**.\n\n"
                    "Ask me anything about active pool migrations, buyback cliff volume decay, "
                    "round-trip friction models, or the **Phase 1 Falsification Gate** criteria."
                ),
                "suggestions": [
                    "Check Phase 1 Falsification Gate status",
                    "Analyze BOOST buyback cliff decay",
                    "Show friction breakdown for Path A+",
                ],
            }
        ]

    # Render conversation history
    for idx, msg in enumerate(st.session_state.chat_history):
        role = msg["role"]
        if role == "user":
            with st.chat_message("user"):
                st.markdown(msg["content"])
        else:
            with st.chat_message("assistant"):
                thoughts = msg.get("thoughts", "")
                if thoughts:
                    st.markdown(f'<div class="thought-pill">{thoughts}</div>', unsafe_allow_html=True)
                st.markdown(msg["content"])

                # Render interactive suggestion chips
                suggestions = msg.get("suggestions", [])
                if suggestions:
                    st.markdown("<div style='margin-top: 8px; font-size: 0.75rem; color: var(--text-muted);'>Suggested Follow-ups:</div>", unsafe_allow_html=True)
                    s_cols = st.columns(len(suggestions))
                    for s_idx, s_text in enumerate(suggestions):
                        with s_cols[s_idx]:
                            if st.button(f"↳ {s_text}", key=f"sug_{idx}_{s_idx}", use_container_width=True):
                                st.session_state.pending_query = s_text
                                st.rerun()

    # Query Input Handling
    pending = st.session_state.pop("pending_query", None)
    prompt = st.chat_input("Ask a question about telemetry, cliff decay, or friction gates...")

    user_query = pending or prompt

    if user_query:
        # Append user message
        st.session_state.chat_history.append({"role": "user", "content": user_query})

        # Process through backend
        thoughts_accum = []
        final_resp = ""
        suggestions_accum = []

        with st.chat_message("assistant"):
            thought_placeholder = st.empty()
            response_placeholder = st.empty()

            for event in backend.stream_query(user_query, st.session_state.chat_history):
                if event.event_type == "THOUGHT":
                    thoughts_accum.append(event.content)
                    thought_placeholder.markdown(
                        f'<div class="thought-pill">{" ".join(thoughts_accum)}</div>',
                        unsafe_allow_html=True,
                    )
                elif event.event_type == "FINAL_RESPONSE":
                    final_resp = event.content
                    response_placeholder.markdown(final_resp)
                elif event.event_type == "SUGGESTION":
                    suggestions_accum.append(event.content)

        # Record assistant reply into history
        st.session_state.chat_history.append(
            {
                "role": "assistant",
                "thoughts": " ".join(thoughts_accum),
                "content": final_resp,
                "suggestions": suggestions_accum,
            }
        )
        st.rerun()
