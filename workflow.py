from langgraph.graph import StateGraph, END
from graph import MeetingState
from processing import (
    transcribe_audio,
    extract_tasks,
    detect_risks,
    extract_summary,
    save_to_database,
    file_sha256,
    update_processing_job
)
from report import create_report
from agents import run_all_agents
import state

def transcribe_agent(state):
    print("TRANSCRIBE START")

    audio_path = state["audio_path"]
    update_processing_job(state.get("job_id"), "Transcription", 10, message="Transcribing audio")
    state["file_hash"] = file_sha256(audio_path)

    result = transcribe_audio(audio_path)

    state["transcript"] = result["transcript"]
    if not state["transcript"].strip():
        raise ValueError("Transcript is empty. Please upload a clearer audio file.")
    state["segments"] = result["segments"]
    state["language"] = result["language"]
    print("TRANSCRIBE DONE")

    return state
def summary_agent(state):
    print("SUMMARY START")
    update_processing_job(state.get("job_id"), "Summary", 35, message="Generating summary")

    transcript = state["transcript"]

    summary = extract_summary(transcript)

    state["summary"] = summary
    print("SUMMARY DONE")

    return state
def task_agent(state):
    print("TASK START")
    update_processing_job(state.get("job_id"), "Tasks", 55, message="Extracting action items")

    transcript = state["transcript"]

    tasks = extract_tasks(transcript)

    print("\nTASKS EXTRACTED:")
    print(tasks)
    print()

    state["tasks"] = tasks
    print("TASK DONE")

    return state
def risk_agent(state):
    print("RISK START")
    update_processing_job(state.get("job_id"), "Risk Analysis", 70, message="Detecting risks and blockers")

    transcript = state["transcript"]

    risk = detect_risks(transcript)

    state["risk_text"] = risk
    print("RISK DONE")

    return state
def save_agent(state):
    print("SAVE START")
    update_processing_job(state.get("job_id"), "Saving", 90, message="Saving meeting and tasks")
    print("TASKS FOUND:", state["tasks"])
    meeting_id = save_to_database(
        state["audio_path"],
        state["transcript"],
        state["summary"],
        state["risk_text"],
        state["tasks"],
        state.get("file_hash", "")
    )
    print("SAVE DONE")

    return {
    **state,
    "meeting_id": meeting_id
}

def report_agent(state):
    print("REPORT START")
    update_processing_job(state.get("job_id"), "Report", 92, message="Generating report")

    create_report(
        state["audio_path"],
        state["summary"],
        state["risk_text"]
    )
    print("REPORT DONE")

    return state

def execution_agent(state):
    print("EXECUTION AGENTS START")
    update_processing_job(state.get("job_id"), "Execution Agents", 97, message="Running reminders and escalation checks")
    state["agent_results"] = run_all_agents()
    update_processing_job(
        state.get("job_id"),
        "Completed",
        100,
        status="Completed",
        message="Processing complete",
        meeting_id=state.get("meeting_id")
    )
    print("EXECUTION AGENTS DONE")
    return state

workflow = StateGraph(MeetingState)
workflow.add_node(
    "transcribe",
    transcribe_agent
)
workflow.add_node(
    "summary",
    summary_agent
)

workflow.add_node(
    "tasks",
    task_agent
)

workflow.add_node(
    "risk",
    risk_agent
)

workflow.add_node(
    "save",
    save_agent
)
workflow.add_node(
    "report",
    report_agent
)
workflow.add_node(
    "execution",
    execution_agent
)

workflow.set_entry_point(
    "transcribe"
)

workflow.add_edge(
    "transcribe",
    "summary"
)

workflow.add_edge(
    "summary",
    "tasks"
)

workflow.add_edge(
    "tasks",
    "risk"
)

workflow.add_edge(
    "risk",
    "save"
)

workflow.add_edge(
    "save",
    "report"
)

workflow.add_edge(
    "report",
    "execution"
)

workflow.add_edge(
    "execution",
    END
)
meeting_graph = workflow.compile()
