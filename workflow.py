from langgraph.graph import StateGraph, END
from graph import MeetingState
from processing import (
    transcribe_audio,
    extract_tasks,
    detect_risks,
    extract_summary,
    save_to_database
)
from report import create_report
import state

def transcribe_agent(state):
    print("TRANSCRIBE START")

    audio_path = state["audio_path"]

    result = transcribe_audio(audio_path)

    state["transcript"] = result["transcript"]
    state["segments"] = result["segments"]
    state["language"] = result["language"]
    print("TRANSCRIBE DONE")

    return state
def summary_agent(state):
    print("SUMMARY START")

    transcript = state["transcript"]

    summary = extract_summary(transcript)

    state["summary"] = summary
    print("SUMMARY DONE")

    return state
def task_agent(state):
    print("TASK START")

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

    transcript = state["transcript"]

    risk = detect_risks(transcript)

    state["risk_text"] = risk
    print("RISK DONE")

    return state
def save_agent(state):
    print("SAVE START")
    print("TASKS FOUND:", state["tasks"])
    meeting_id = save_to_database(
        state["audio_path"],
        state["transcript"],
        state["summary"],
        state["risk_text"],
        state["tasks"]
    )
    print("SAVE DONE")

    return {
    **state,
    "meeting_id": meeting_id
}

def report_agent(state):
    print("REPORT START")

    create_report(
        state["audio_path"],
        state["summary"],
        state["risk_text"]
    )
    print("REPORT DONE")

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
    END
)
meeting_graph = workflow.compile()