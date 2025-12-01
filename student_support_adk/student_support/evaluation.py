"""
Agent evaluation utilities
FEATURE: Agent evaluation (automated rubric)
"""
from typing import Dict


def evaluate_agent_response(prompt: str, response: str) -> Dict[str, int]:
    """
    Very simple evaluation rubric for demo purposes.
    Scores:
        - Relevance: 0–50
        - Correctness: 0–30
        - Clarity: 0–20
    """
    score = {"relevance": 0, "correctness": 0, "clarity": 0}

    p = prompt.lower()
    r = response.lower()

    # Relevance heuristic: response contains some of the prompt's early tokens
    if any(tok in r for tok in p.split()[:3]):
        score["relevance"] = 40

    # Correctness heuristic (example rule)
    if "password" in p and "reset" in r:
        score["correctness"] = 25

    # Clarity heuristic: longer sentences get partial credit
    if len(response.split()) > 6:
        score["clarity"] = 15

    total = score["relevance"] + score["correctness"] + score["clarity"]
    return {"component_scores": score, "total": total}
