from langgraph.graph import StateGraph, START, END

from reimbursement.schema import State
from reimbursement.agent.nodes import extract_fields, validate, apply_policies, analysis, apply_agent_decision

def run():
    graph = StateGraph(State)

    graph.add_node("extract_fields", extract_fields)
    graph.add_node("validate", validate)
    graph.add_node("apply_policies", apply_policies)
    graph.add_node("analysis", analysis)
    graph.add_node("apply_agent_decision", apply_agent_decision)
