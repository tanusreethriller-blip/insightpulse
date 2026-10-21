from __future__ import annotations

import os

import httpx
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Review Pulse", page_icon=":material/monitoring:", layout="wide")

API_URL = os.environ.get("REVIEW_PULSE_API_URL", "http://127.0.0.1:8000").rstrip("/")


class ApiError(RuntimeError):
    pass


def api(path: str, method: str = "GET", **kwargs):
    try:
        response = httpx.request(method, API_URL + path, timeout=30, **kwargs)
    except httpx.RequestError as exc:
        raise ApiError(
            f"Cannot reach the Review Pulse API at {API_URL}. Start it with "
            "`uvicorn reviewpulse.api:create_app --factory --host 127.0.0.1 --port 8000`."
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
    settings = api("/api/me")
    jobs = api("/api/jobs")["jobs"]
except ApiError as exc:
    show_api_error(exc)
    st.stop()

st.sidebar.title("Review Pulse")
st.sidebar.caption("Customer feedback analysis")
navigation = st.sidebar.radio("Navigate", ["Overview", "Upload reviews", "Reports & approval", "Knowledge base", "Activity & n8n"])
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
elif navigation == "Upload reviews":
    st.title("Upload reviews")
    render_upload()
elif navigation == "Reports & approval":
    st.title("Reports & approval")
    if current_job:
        render_report(current_job)
    else:
        st.info("Upload reviews or start the synthetic demo first.")
elif navigation == "Knowledge base":
    render_knowledge()
else:
    render_activity(settings)
