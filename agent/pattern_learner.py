# Kept for backward compatibility — logic moved to brain.py
from agent.brain import (
    detect_all_patterns as detect_patterns,
    learn_from_trade,
    get_reliable_patterns_list as get_reliable_patterns,
    load_patterns,
    save_patterns,
)

__all__ = [
    "detect_patterns", "learn_from_trade",
    "get_reliable_patterns", "load_patterns", "save_patterns",
]
