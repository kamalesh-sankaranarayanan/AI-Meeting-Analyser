"""
LangGraph State Definition for AI Meeting-to-Execution Agent
 
This module defines the state structure for the multi-agent workflow.
The graph pipeline:
  1. Transcription Agent: Converts audio to text
  2. Summary Agent: Generates meeting summary
  3. Task Extraction Agent: Extracts actionable items
  4. Risk Detection Agent: Identifies risks and blockers
  5. Execution Agent: Stores results and triggers actions
"""
 
from typing import TypedDict, Optional, List
from datetime import datetime
 
 
# ============================================================================
# STATE DEFINITIONS
# ============================================================================
 
class TaskItem(TypedDict, total=False):
    """Single extracted task"""
    task: str
    owner: str
    deadline: str
    priority: str  # 'High', 'Medium', 'Low'
    status: str
 
 
class RiskItem(TypedDict, total=False):
    """Risk or blocker identified"""
    risk_type: str  # 'Risk', 'Blocker', 'Delay'
    description: str
    severity: str  # 'High', 'Medium', 'Low'
 
 
from typing import TypedDict

class MeetingState(TypedDict):

    audio_path: str

    transcript: str

    segments: list

    language: str

    tasks: list

    summary: str

    risk_text: str

    meeting_id: int

    report_path: str
# ============================================================================
# POTENTIAL GRAPH STRUCTURE
# ============================================================================
# 
# The actual graph implementation would look like:
#
# from langgraph.graph import StateGraph
#
# def create_meeting_workflow():
#     workflow = StateGraph(MeetingState)
#     
#     # Add nodes
#     workflow.add_node("transcribe", transcribe_agent)
#     workflow.add_node("summarize", summary_agent)
#     workflow.add_node("extract_tasks", task_extraction_agent)
#     workflow.add_node("detect_risks", risk_detection_agent)
#     workflow.add_node("save_to_db", execution_agent)
#     
#     # Add edges (workflow sequence)
#     workflow.add_edge("START", "transcribe")
#     workflow.add_edge("transcribe", "summarize")
#     workflow.add_edge("summarize", "extract_tasks")
#     workflow.add_edge("extract_tasks", "detect_risks")
#     workflow.add_edge("detect_risks", "save_to_db")
#     workflow.add_edge("save_to_db", "END")
#     
#     return workflow.compile()
#
# ============================================================================
 
 
class AgentConfig(TypedDict, total=False):
    """Configuration for LLM agents"""
    model: str
    temperature: float
    max_tokens: int
    timeout: int
 
 
# ============================================================================
# CONVERSATION HISTORY (for multi-turn reasoning)
# ============================================================================
 
class Message(TypedDict, total=False):
    """Single message in conversation history"""
    role: str  # 'user', 'assistant', 'system'
    content: str
 
 
class ConversationHistory(TypedDict, total=False):
    """History of messages for context in multi-turn workflows"""
    messages: List[Message]
    model: str
    temperature: float
 
 
# ============================================================================
# VALIDATION
# ============================================================================
 
def validate_meeting_state(state: MeetingState) -> bool:
    """
    Validate that a meeting state has required fields
    
    Args:
        state: MeetingState to validate
        
    Returns:
        True if valid, raises ValueError otherwise
    """
    required = ['audio_path']
    missing = [field for field in required if field not in state]
    
    if missing:
        raise ValueError(f"Missing required fields: {missing}")
    
    return True
 
 
def validate_task(task: TaskItem) -> bool:
    """Validate a single task"""
    if not task.get('task'):
        raise ValueError("Task must have a 'task' field")
    
    priority = task.get('priority', 'Medium')
    if priority not in ['High', 'Medium', 'Low']:
        raise ValueError(f"Invalid priority: {priority}")
    
    return True
 
 
# ============================================================================
# ALTERNATIVE APPROACH: Using Pydantic for strict validation
# ============================================================================
#
# from pydantic import BaseModel, Field
# from typing import List
#
# class TaskModel(BaseModel):
#     task: str
#     owner: str = "Unassigned"
#     deadline: str = ""
#     priority: str = Field(default="Medium", pattern="^(High|Medium|Low)$")
#     status: str = Field(default="Pending")
#
# class MeetingStateModel(BaseModel):
#     audio_path: str
#     transcript: str = ""
#     summary: str = ""
#     tasks: List[TaskModel] = []
#     risks: List[str] = []
#     meeting_id: Optional[int] = None
#     created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
#
# This approach is stricter and better for interview-level code