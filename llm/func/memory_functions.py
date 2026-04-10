"""
Memory Management Functions
============================

Tool functions for managing agent memory.
"""

import os
from typing import Dict, List
from google.genai import types

from func.agent_memory import get_memory


def memory_save_file_purpose(working_directory: str, filepath: str, purpose: str) -> str:
    """
    Save a file's purpose to memory.
    
    Args:
        working_directory: Current working directory
        filepath: Path to the file
        purpose: What this file does
    
    Returns:
        Confirmation message
    """
    os.chdir(working_directory)
    memory = get_memory(working_directory)
    
    memory.update_codebase_map({filepath: purpose})
    
    return f"Saved purpose for {filepath}: {purpose}"


def memory_add_pattern(working_directory: str, pattern: str) -> str:
    """
    Add a code pattern to memory.
    
    Args:
        working_directory: Current working directory
        pattern: Description of the code pattern
    
    Returns:
        Confirmation message
    """
    os.chdir(working_directory)
    memory = get_memory(working_directory)
    
    memory.add_pattern(pattern)
    
    return f"Pattern added to memory: {pattern}"


def memory_add_gotcha(working_directory: str, gotcha: str) -> str:
    """
    Add a gotcha/pitfall to memory.
    
    Args:
        working_directory: Current working directory
        gotcha: Description of the pitfall to avoid
    
    Returns:
        Confirmation message
    """
    os.chdir(working_directory)
    memory = get_memory(working_directory)
    
    memory.add_gotcha(gotcha)
    
    return f"Gotcha added to memory: {gotcha}"


def memory_get_context(working_directory: str) -> str:
    """
    Get full memory context.
    
    Args:
        working_directory: Current working directory
    
    Returns:
        Formatted memory context
    """
    os.chdir(working_directory)
    memory = get_memory(working_directory)
    
    context = memory.get_full_context()
    
    if not context:
        return "No memory context available yet."
    
    return context


def memory_get_stats(working_directory: str) -> str:
    """
    Get memory statistics.
    
    Args:
        working_directory: Current working directory
    
    Returns:
        Formatted statistics
    """
    os.chdir(working_directory)
    memory = get_memory(working_directory)
    
    stats = memory.get_memory_stats()
    
    result = "Memory Statistics:\n"
    result += f"  Conversations: {stats['conversations']}\n"
    result += f"  Files Mapped: {stats['files_mapped']}\n"
    result += f"  Patterns: {stats['patterns']}\n"
    result += f"  Gotchas: {stats['gotchas']}\n"
    
    return result


def memory_clear(working_directory: str) -> str:
    """
    Clear all memory (use with caution!).
    
    Args:
        working_directory: Current working directory
    
    Returns:
        Confirmation message
    """
    os.chdir(working_directory)
    memory = get_memory(working_directory)
    
    memory.clear_all()
    
    return "All memory cleared!"


# ============================================================================
# FUNCTION SCHEMAS FOR AI AGENT
# ============================================================================

schema_memory_save_file_purpose = types.FunctionDeclaration(
    name="memory_save_file_purpose",
    description="""Save a file's purpose to persistent memory.
    
    Use this when you understand what a file does, so you don't have to
    re-read it in future conversations.""",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "working_directory": types.Schema(
                type=types.Type.STRING,
                description="Current working directory",
            ),
            "filepath": types.Schema(
                type=types.Type.STRING,
                description="Path to the file (relative to working directory)",
            ),
            "purpose": types.Schema(
                type=types.Type.STRING,
                description="What this file does (concise description)",
            ),
        },
        required=["working_directory", "filepath", "purpose"],
    ),
)

schema_memory_add_pattern = types.FunctionDeclaration(
    name="memory_add_pattern",
    description="""Save a code pattern to memory.
    
    Use this when you discover a pattern in the codebase that should be
    followed in future work (e.g., "All API responses use {success, data, error}").""",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "working_directory": types.Schema(
                type=types.Type.STRING,
                description="Current working directory",
            ),
            "pattern": types.Schema(
                type=types.Type.STRING,
                description="Description of the code pattern",
            ),
        },
        required=["working_directory", "pattern"],
    ),
)

schema_memory_add_gotcha = types.FunctionDeclaration(
    name="memory_add_gotcha",
    description="""Save a gotcha/pitfall to memory.
    
    Use this when you discover a mistake or pitfall that should be avoided
    (e.g., "Database connections must be closed manually in workers").""",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "working_directory": types.Schema(
                type=types.Type.STRING,
                description="Current working directory",
            ),
            "gotcha": types.Schema(
                type=types.Type.STRING,
                description="Description of the pitfall to avoid",
            ),
        },
        required=["working_directory", "gotcha"],
    ),
)

schema_memory_get_context = types.FunctionDeclaration(
    name="memory_get_context",
    description="""Get full memory context including past conversations, 
    file purposes, patterns, and gotchas.
    
    Use this at the start of complex tasks to recall what you already know.""",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "working_directory": types.Schema(
                type=types.Type.STRING,
                description="Current working directory",
            ),
        },
        required=["working_directory"],
    ),
)

schema_memory_get_stats = types.FunctionDeclaration(
    name="memory_get_stats",
    description="Get statistics about stored memory (conversation count, files mapped, etc.).",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "working_directory": types.Schema(
                type=types.Type.STRING,
                description="Current working directory",
            ),
        },
        required=["working_directory"],
    ),
)

schema_memory_clear = types.FunctionDeclaration(
    name="memory_clear",
    description="Clear all memory. Use only when explicitly requested by user.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "working_directory": types.Schema(
                type=types.Type.STRING,
                description="Current working directory",
            ),
        },
        required=["working_directory"],
    ),
)