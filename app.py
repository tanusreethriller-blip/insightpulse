from __future__ import annotations

import os
import secrets
import threading
import time
from collections import Counter
from datetime import date, timedelta

import httpx
import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError
import uvicorn

st.set_page_config(page_title="Review Pulse", page_icon=":material/monitoring:", layout="wide")
st.markdown(
    """
    <style>
    .stApp {
        background:
            radial-gradient(ellipse at 90% 0%, rgba(237, 121, 95, 0.08), transparent 32rem),
            #f6f7f9;
    }
    .block-container {
        max-width: 1480px;
        padding-top: 5rem !important;
        padding-bottom: 3rem;
    }
    [data-testid="stSidebar"] {
        background: rgba(255, 255, 255, 0.88);
        border-right: 1px solid #e8ebf0;
    }
    [data-testid="stMetric"] {
        background: #ffffff;
        border: 1px solid #e8ebf0;
        border-radius: 16px;
        padding: 1rem 1.1rem;
        box-shadow: 0 5px 18px rgba(39, 49, 61, 0.035);
    }
    [data-testid="stMetricLabel"] {
        color: #737e8c;
        font-size: 0.82rem;
    }
    [data-testid="stMetricValue"] {
        color: #27313d;
        font-weight: 700;
    }
    [data-testid="stRadio"] > div[role="radiogroup"] {
        justify-content: flex-end;
        gap: 0.35rem;
        flex-wrap: wrap !important;
        overflow: visible;
        padding: 0.15rem 0;
    }
    [data-testid="stRadioGroup"] > div {
        flex: 0 0 auto !important;
        width: auto !important;
        min-width: 0 !important;
    }
    [data-testid="stRadioOption"] {
        border: 1px solid #e8ebf0;
        border-radius: 999px;
        background: rgba(255, 255, 255, 0.86);
        padding: 0.3rem 0.62rem !important;
        white-space: nowrap;
        transition: background 120ms ease, border-color 120ms ease;
    }
    [data-testid="stRadioOption"] > div > div:first-child {
        display: none;
    }
    [data-testid="stRadioOption"][data-selected="true"] {
        background: #f0eafb;
        border-color: #d9c9ee;
        color: #684f88;
    }
    @media (max-width: 760px) {
        [data-testid="stRadio"] > div[role="radiogroup"] {
            justify-content: flex-start;
        }
        [data-testid="stRadioOption"] {
            padding: 0.24rem 0.48rem !important;
            font-size: 0.88rem;
        }
    }
    .rp-brandmark {
        display: inline-flex;
        width: 2.25rem;
        height: 2.25rem;
        align-items: center;
        justify-content: center;
        border-radius: 0.8rem;
        background: #ed795f;
        color: #ffffff;
        font-size: 1rem;
        font-weight: 800;
        letter-spacing: -0.05em;
        box-shadow: 0 5px 12px rgba(237, 121, 95, 0.2);
    }
    [data-testid="stVerticalBlockBorderWrapper"] {
        border-color: #e8ebf0 !important;
        border-radius: 18px !important;
        background: rgba(255, 255, 255, 0.78);
    }
    h1, h2, h3 {
        letter-spacing: -0.035em;
    }
    h1 {
        color: #27313d;
        font-size: 2.25rem !important;
    }
    div.stButton > button[kind="primary"],
    div.stFormSubmitButton > button[kind="primary"] {
        border-radius: 11px;
        box-shadow: 0 5px 12px rgba(237, 121, 95, 0.16);
    }
    </style>
    """,
    unsafe_allow_html=True,
)

try:
    configured_api_url = st.secrets.get("REVIEW_PULSE_API_URL")
except StreamlitSecretNotFoundError:
    configured_api_url = None
API_URL = (
    configured_api_url or os.environ.get("REVIEW_PULSE_API_URL") or "http://127.0.0.1:8000"
).rstrip("/")
DEFAULT_API_URL = "http://127.0.0.1:8000"


class ApiError(RuntimeError):
    pass


class LocalApiRuntime:
    def __init__(self):
        self.lock = threading.Lock()
        self.server: uvicorn.Server | None = None
        self.thread: threading.Thread | None = None

    def ensure_started(self) -> None:
        with self.lock:
            try:
                response = httpx.get(API_URL + "/healthz", timeout=1)
            except httpx.ConnectError:
                pass
            except httpx.RequestError as exc:
                raise ApiError(f"Could not check the local API at {API_URL}: {exc}") from exc
            else:
                if response.is_success:
                    return
                raise ApiError(
                    f"The service at {API_URL} returned HTTP {response.status_code}, "
                    "not an API health response."
                )

            if self.thread is None or not self.thread.is_alive():
                if len(os.environ.get("INGEST_API_TOKEN", "")) < 32:
                    os.environ["INGEST_API_TOKEN"] = secrets.token_urlsafe(32)
                os.environ.setdefault("PUBLIC_URL", DEFAULT_API_URL)
                config = uvicorn.Config(
                    "reviewpulse.api:create_app",
                    factory=True,
                    host="127.0.0.1",
                    port=8000,
                    workers=1,
                    access_log=False,
                )
                self.server = uvicorn.Server(config)
                self.thread = threading.Thread(
                    target=self.server.run,
                    daemon=True,
                    name="reviewpulse-api",
                )
                self.thread.start()

            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if self.thread is None or not self.thread.is_alive():
                    break
                try:
                    response = httpx.get(API_URL + "/healthz", timeout=0.5)
                    if response.is_success:
                        return
                    raise ApiError(
                        f"The built-in API returned HTTP {response.status_code} during startup."
                    )
                except httpx.RequestError:
                    time.sleep(0.2)
            raise ApiError(
                "The built-in API did not become healthy. Check the Streamlit app logs for startup errors."
            )


@st.cache_resource
def default_api_runtime() -> LocalApiRuntime:
    return LocalApiRuntime()


def start_default_api() -> None:
    if API_URL == DEFAULT_API_URL:
        default_api_runtime().ensure_started()


def api(path: str, method: str = "GET", **kwargs):
    try:
        response = httpx.request(method, API_URL + path, timeout=30, **kwargs)
    except httpx.RequestError as exc:
        if API_URL.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]")):
            raise ApiError(
                "The dashboard is configured for a local API, which is not available to a hosted "
                "Streamlit app. Deploy the FastAPI service separately and set "
                "`REVIEW_PULSE_API_URL = \"https://<your-api-host>\"` in Streamlit Cloud → "
                "App settings → Secrets."
            ) from exc
        raise ApiError(
            f"Cannot reach the Review Pulse API at {API_URL}. Confirm the API is running and "
            "that `REVIEW_PULSE_API_URL` is its reachable HTTPS URL."
        ) from exc
    try:
        data = response.json()
    except ValueError:
        data = {}
    if not response.is_success:
        raise ApiError(data.get("detail", f"API request failed ({response.status_code})."))
    return data


def show_api_error(error: Exception) -> None:
    st.error(str(error))


def render_upload() -> None:
    st.subheader("Start an analysis")
    with st.form("upload_reviews"):
        name = st.text_input("Analysis name", value="Customer feedback", max_chars=100)
        question = st.text_input("Optional support question", max_chars=500)
        upload = st.file_uploader("Review CSV", type=["csv"])
        submitted = st.form_submit_button("Analyze reviews", type="primary")
    if submitted:
        if upload is None:
            st.error("Choose a CSV file.")
        else:
            try:
                csv_text = upload.getvalue().decode("utf-8-sig")
                job = api("/api/jobs", "POST", json={"name": name, "question": question, "csv": csv_text})
                st.session_state["selected_job"] = job["id"]
                st.success("Analysis queued.")
                st.rerun()
            except (UnicodeDecodeError, ApiError) as exc:
                show_api_error(exc)


def render_overview(job: dict | None) -> None:
    st.title("Your feedback, in focus.")
    st.caption("Turn customer reviews into evidence-backed product decisions.")

    if job is None or not job.get("result"):
        if job:
            st.info(f"Analysis status: {job['status']} — {job.get('progress') or 'Waiting for the worker.'}")
            if job.get("error"):
                st.error(job["error"])
        else:
            st.info("No completed analysis selected. Upload a review CSV or start the synthetic demo.")
        render_upload()
        return

    result = job["result"]
    metrics = result["metrics"]
    a, b, c, d = st.columns(4)
    a.metric("Reviews analyzed", f"{metrics['review_count']:,}")
    b.metric("Average rating", f"{metrics['average_rating']:.2f} / 5")
    c.metric("1–2 star reviews", f"{metrics['sentiment']['negative']:,}")
    d.metric("Negative share", f"{metrics['negative_share']}%")
    st.caption(f"Review period: {metrics['date_from']} to {metrics['date_to']}")
    if result.get("is_demo"):
        st.warning("Synthetic demonstration data — not real customer feedback.")

    themes = metrics["themes"]
    st.subheader("Leading themes")
    if themes:
        frame = pd.DataFrame([
            {"Theme": theme["name"], "Reviews": theme["count"], "1–2★ reviews": theme["negative_count"],
             "Share": f"{theme['share']}%"}
            for theme in themes
        ])
        st.dataframe(frame, width="stretch", hide_index=True)
        chart = pd.DataFrame(
            {"1–2 star reviews": [theme["negative_count"] for theme in themes]},
            index=[theme["name"] for theme in themes],
        )
        st.bar_chart(chart)

    with st.expander("Review explorer"):
        reviews = result.get("reviews", [])
        filter_col, search_col = st.columns([1, 2])
        rating = filter_col.selectbox("Rating", ["All", "Negative (1–2)", "Neutral (3)", "Positive (4–5)"])
        search = search_col.text_input("Search review text")
        filtered = reviews
        if rating == "Negative (1–2)":
            filtered = [review for review in filtered if review["rating"] <= 2]
        elif rating == "Neutral (3)":
            filtered = [review for review in filtered if review["rating"] == 3]
        elif rating == "Positive (4–5)":
            filtered = [review for review in filtered if review["rating"] >= 4]
        if search:
            filtered = [review for review in filtered if search.casefold() in review["text"].casefold()]
        st.dataframe(pd.DataFrame(filtered), width="stretch", hide_index=True)


def render_weekly_pulse(job: dict | None) -> None:
    st.title("Weekly pulse")
    st.caption("A week-by-week view of customer feedback, ratings, and emerging themes.")

    if job is None or not job.get("result"):
        st.info("Choose a completed analysis run or start the synthetic demo to see its weekly pulse.")
        return

    result = job["result"]
    metrics = result["metrics"]
    weekly = [week for week in metrics["weekly"] if week["count"] > 0]
    if not weekly:
        st.info("There is not enough dated feedback to build a weekly pulse yet.")
        return

    latest = weekly[-1]
    week_start = date.fromisoformat(latest["week"])
    week_end = week_start + timedelta(days=6)
    prior = next(
        (week for week in metrics["weekly"] if week["week"] == (week_start - timedelta(days=7)).isoformat()),
        None,
    )
    current_reviews = [
        review for review in result.get("reviews", [])
        if week_start <= date.fromisoformat(review["date"]) < week_start + timedelta(days=7)
    ]
    theme_counts = Counter(review.get("theme_id") for review in current_reviews if review.get("theme_id"))
    theme_names = {theme["id"]: theme["name"] for theme in metrics["themes"]}
    top_theme_id = max(theme_counts, key=lambda theme_id: (theme_counts[theme_id], theme_id)) if theme_counts else None
    top_theme = theme_names.get(top_theme_id, "Not available")
    previous_delta = (
        latest["count"] - prior["count"]
        if prior and prior["count"] > 0
        else None
    )

    st.caption(f"Latest active week · {week_start:%b %d} – {week_end:%b %d, %Y}")
    a, b, c, d = st.columns(4)
    a.metric(
        "Reviews this week",
        f"{latest['count']:,}",
        delta=f"{previous_delta:+,} vs prior week" if previous_delta is not None else None,
    )
    b.metric("Average rating", f"{latest['average_rating']:.2f} / 5")
    c.metric("1–2 star reviews", f"{latest['negative']:,}")
    d.metric("Top theme", top_theme)

    chart_weeks = weekly[-8:]
    trend_col, volume_col = st.columns([1.5, 1])
    with trend_col:
        with st.container(border=True):
            st.subheader("Rating trend")
            st.caption("Average star rating by active review week.")
            ratings = pd.DataFrame(
                {"Average rating": [week["average_rating"] for week in chart_weeks]},
                index=[date.fromisoformat(week["week"]).strftime("%b %d") for week in chart_weeks],
            )
            st.line_chart(ratings, y="Average rating", color="#9b82bd", height=270)
    with volume_col:
        with st.container(border=True):
            st.subheader("Weekly feedback")
            st.caption("Review volume and 1–2 star feedback.")
            counts = pd.DataFrame(
                {
                    "All reviews": [week["count"] for week in chart_weeks],
                    "1–2 star reviews": [week["negative"] for week in chart_weeks],
                },
                index=[date.fromisoformat(week["week"]).strftime("%b %d") for week in chart_weeks],
            )
            st.bar_chart(counts, height=270, color=["#b7a4ce", "#ed795f"])

    with st.container(border=True):
        st.subheader("What customers talked about")
        if theme_counts:
            theme_rows = [
                {
                    "Theme": theme_names.get(theme_id, theme_id),
                    "Reviews": count,
                    "Share of this week": f"{count / len(current_reviews) * 100:.0f}%",
                }
                for theme_id, count in theme_counts.most_common(5)
            ]
            st.dataframe(pd.DataFrame(theme_rows), width="stretch", hide_index=True)
        else:
            st.info("Theme details are not available for this week.")
    if result.get("is_demo"):
        st.caption("This pulse is based on synthetic demonstration reviews.")


def render_report(job: dict) -> None:
    result = job.get("result")
    if not result:
        st.info(f"Analysis status: {job.get('status', 'unknown')} — {job.get('progress', '')}")
        if job.get("error"):
            st.error(job["error"])
        return

    approval = job.get("approval")
    st.subheader(f"Report · {job['name']}")
    st.caption(f"Status: {approval['status'] if approval else job['status']}")
    st.markdown(result["report_markdown"])
    st.download_button(
        "Download Markdown",
        result["report_markdown"],
        file_name=f"review-pulse-{job['id']}.md",
        mime="text/markdown",
    )
    if approval:
        st.info(f"Approval state: {approval['status']}")
        if approval.get("error"):
            st.warning(approval["error"])
    elif job["status"] == "ready":
        st.caption("Review the report and payload preview before approving.")
        with st.expander("Exact publishing payload"):
            payload = result["payload"]
            st.write(f"**Destination:** {payload['target']['mode']}")
            st.write(f"**Recipient:** {payload['target'].get('recipient', '')}")
            st.write(f"**Subject:** {payload['subject']}")
            st.code(payload["doc_entry"])
            st.code(f"Payload SHA-256: {result['payload_hash']}")
        with st.form(f"approve_{job['id']}"):
            confirmation = st.text_input("Type APPROVE to confirm")
            approved = st.form_submit_button("Approve & publish", type="primary")
        if approved:
            if confirmation != "APPROVE":
                st.error("Type APPROVE exactly to continue.")
            else:
                try:
                    api(f"/api/jobs/{job['id']}/approve", "POST",
                        json={"confirmation": confirmation, "payload_hash": result["payload_hash"]})
                    st.success("Approval recorded. Publishing is queued.")
                    st.rerun()
                except ApiError as exc:
                    show_api_error(exc)
        with st.form(f"reject_{job['id']}"):
            rejected = st.form_submit_button("Reject this report")
        if rejected:
            try:
                api(f"/api/jobs/{job['id']}/reject", "POST")
                st.success("Report rejected; nothing was published.")
                st.rerun()
            except ApiError as exc:
                show_api_error(exc)

    if approval and approval["status"] == "needs_attention":
        st.warning("Check the destination document, Gmail drafts, and active n8n executions before reconciliation.")
        with st.form(f"reconcile_{job['id']}"):
            doc_state = st.selectbox("Document write", ["done", "pending"])
            draft_state = st.selectbox("Gmail draft", ["done", "pending"])
            note = st.text_area("What did you verify? (at least 15 characters)")
            confirmation = st.text_input("Type VERIFIED")
            reconciled = st.form_submit_button("Save reconciliation")
        if reconciled:
            if confirmation != "VERIFIED" or len(note.strip()) < 15:
                st.error("Type VERIFIED and add a verification note of at least 15 characters.")
            else:
                try:
                    api(f"/api/approvals/{approval['id']}/reconcile", "POST", json={
                        "confirmation": confirmation, "doc_state": doc_state, "draft_state": draft_state,
                        "note": note.strip(),
                    })
                    st.success("Reconciliation saved.")
                    st.rerun()
                except ApiError as exc:
                    show_api_error(exc)


def render_knowledge() -> None:
    st.title("Knowledge base")
    st.caption("Add reviewed product documentation for evidence-grounded support answers.")
    try:
        sources = api("/api/sources")["sources"]
    except ApiError as exc:
        show_api_error(exc)
        return
    with st.form("add_source"):
        title = st.text_input("Document title", max_chars=150)
        url = st.text_input("Reference URL (optional)")
        content = st.text_area("Document text", height=220, max_chars=100000)
        submitted = st.form_submit_button("Add source", type="primary")
    if submitted:
        try:
            api("/api/sources", "POST", json={"title": title, "url": url, "content": content})
            st.success("Source added.")
            st.rerun()
        except ApiError as exc:
            show_api_error(exc)
    for source in sources:
        with st.expander(f"{source['title']} · {source['created_at']}"):
            st.caption(source.get("url") or "Owner-provided document")
            st.text(source["content"])
            if st.button("Delete source", key=f"delete_{source['id']}"):
                try:
                    api(f"/api/sources/{source['id']}", "DELETE")
                    st.success("Source deleted from future analyses.")
                    st.rerun()
                except ApiError as exc:
                    show_api_error(exc)


def render_activity(settings: dict) -> None:
    st.title("Activity & n8n setup")
    st.write(f"**Analysis mode:** {settings['analysis_mode']}  ·  **Publishing mode:** {settings['publish_mode']}")
    st.info("n8n handles scheduled ingestion and MCP publishing. Keep the dashboard and API on a trusted network; this app has no login.")
    if settings["publish_mode"] == "mcp" and st.button("Check n8n MCP connection"):
        try:
            st.json(api("/api/integrations/check", "POST"))
        except ApiError as exc:
            show_api_error(exc)
    try:
        events = api("/api/audit")["events"]
        st.subheader("Recent activity")
        st.dataframe(pd.DataFrame(events), width="stretch", hide_index=True)
        if settings["publish_mode"] == "local":
            with st.expander("Local publishing demonstration records"):
                st.json(api("/api/local-effects")["effects"])
    except ApiError as exc:
        show_api_error(exc)


try:
    start_default_api()
    settings = api("/api/me")
    jobs = api("/api/jobs")["jobs"]
except ApiError as exc:
    show_api_error(exc)
    st.stop()

st.markdown(
    '<div style="display:flex;align-items:center;gap:.7rem;margin-bottom:.25rem">'
    '<span class="rp-brandmark">RP</span><strong style="font-size:1.2rem;color:#27313d">'
    'Review Pulse</strong></div>',
    unsafe_allow_html=True,
)
navigation = st.radio(
    "Navigate",
    ["Overview", "Upload", "Reports", "Sources", "Activity", "Weekly pulse"],
    horizontal=True,
    label_visibility="collapsed",
    key="main_navigation",
)
st.divider()

st.sidebar.markdown("### Review Pulse")
st.sidebar.caption("Customer feedback analysis")
if st.sidebar.button("Explore the synthetic demo", width="stretch"):
    try:
        created = api("/api/demo", "POST")
        st.session_state["selected_job"] = created["id"]
        st.success("Synthetic demo queued.")
        st.rerun()
    except ApiError as exc:
        show_api_error(exc)

if jobs:
    job_ids = [job["id"] for job in jobs]
    selected = st.session_state.get("selected_job")
    if selected not in job_ids:
        selected = job_ids[0]
    selected = st.sidebar.selectbox(
        "Analysis run",
        job_ids,
        index=job_ids.index(selected),
        format_func=lambda identifier: next(
            f"{job['name']} · {job['status']}" for job in jobs if job["id"] == identifier
        ),
    )
    st.session_state["selected_job"] = selected
    if st.sidebar.button("Refresh status", width="stretch"):
        st.rerun()
    try:
        current_job = api(f"/api/jobs/{selected}")
    except ApiError as exc:
        show_api_error(exc)
        st.stop()
else:
    current_job = None

if navigation == "Overview":
    render_overview(current_job)
elif navigation == "Upload":
    st.title("Upload reviews")
    render_upload()
elif navigation == "Weekly pulse":
    render_weekly_pulse(current_job)
elif navigation == "Reports":
    st.title("Reports & approval")
    if current_job:
        render_report(current_job)
    else:
        st.info("Upload reviews or start the synthetic demo first.")
elif navigation == "Sources":
    render_knowledge()
else:
    render_activity(settings)
