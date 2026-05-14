"""
PuriPy - Day 1: Exploring Python's sys.settrace

GOAL: Understand how Python's tracing API works.
Run this file and watch what happens.

This is NOT the final code. It's a learning exercise.
By the end of Day 1, you should understand:
  1. How sys.settrace() works
  2. What a "frame" object is
  3. The 4 event types: 'call', 'line', 'return', 'exception'
  4. How to access local variables at any point in execution
"""

import sys


# =============================================================================
# EXPERIMENT 1: The simplest possible tracer
# =============================================================================

def simple_tracer(frame, event, arg):
    """
    This function is called by Python BEFORE every line of code executes.
    
    Parameters:
      frame: A frame object containing all info about the current execution
      event: A string - one of 'call', 'line', 'return', 'exception'
      arg:   Extra info depending on event (return value, exception, etc.)
    
    Returns:
      Either this function itself (to keep tracing) or None (to stop)
    """
    print(f"  [TRACE] event={event:10} line={frame.f_lineno:3} func={frame.f_code.co_name}")
    return simple_tracer  # Keep tracing


def example_function():
    """A simple function we'll trace."""
    x = 10
    y = 20
    z = x + y
    return z


def run_experiment_1():
    print("=" * 60)
    print("EXPERIMENT 1: Basic tracing")
    print("=" * 60)
    print("Watch every line execute:\n")
    
    sys.settrace(simple_tracer)  # Start tracing
    result = example_function()
    sys.settrace(None)           # Stop tracing
    
    print(f"\nResult: {result}\n")


# =============================================================================
# EXPERIMENT 2: Inspecting local variables
# =============================================================================

def variable_tracer(frame, event, arg):
    """
    Now let's look at the VALUES of variables at each line.
    This is the core of what PuriPy will record.
    """
    if event == 'line':
        # frame.f_locals is a dict of all local variables at this moment
        locals_snapshot = dict(frame.f_locals)
        print(f"  [LINE {frame.f_lineno}] locals = {locals_snapshot}")
    return variable_tracer


def function_with_vars():
    """We'll watch variables change as this runs."""
    name = "Abishek"
    age = 19
    skills = []
    skills.append("Python")
    skills.append("FastAPI")
    skills.append("React")
    return f"{name}, age {age}, knows {skills}"


def run_experiment_2():
    print("=" * 60)
    print("EXPERIMENT 2: Capturing variable values")
    print("=" * 60)
    print("Watch variables appear and change:\n")
    
    sys.settrace(variable_tracer)
    result = function_with_vars()
    sys.settrace(None)
    
    print(f"\nResult: {result}\n")


# =============================================================================
# EXPERIMENT 3: Detecting variable CHANGES (this is the key insight)
# =============================================================================

class DeltaTracer:
    """
    Track what CHANGED between lines, not full state every line.
    This is the basis for our delta-compression strategy in Week 3.
    """
    
    def __init__(self):
        self.previous_locals = {}
    
    def trace(self, frame, event, arg):
        if event == 'line':
            current = dict(frame.f_locals)
            changes = self._diff(self.previous_locals, current)
            
            if changes:
                print(f"  [LINE {frame.f_lineno}] changed: {changes}")
            else:
                print(f"  [LINE {frame.f_lineno}] (no changes)")
            
            self.previous_locals = current
        
        return self.trace
    
    def _diff(self, old, new):
        """Return only the keys that changed between old and new."""
        changes = {}
        for key, value in new.items():
            if key not in old:
                changes[key] = ('NEW', value)
            elif old[key] != value:
                changes[key] = ('CHANGED', old[key], '->', value)
        return changes


def function_with_changes():
    """Variables get created and modified."""
    counter = 0
    counter = counter + 1
    counter = counter + 1
    message = "Hello"
    counter = counter + 10
    return counter, message


def run_experiment_3():
    print("=" * 60)
    print("EXPERIMENT 3: Detecting changes (delta tracking)")
    print("=" * 60)
    print("Only show what changed each line:\n")
    
    tracer = DeltaTracer()
    sys.settrace(tracer.trace)
    result = function_with_changes()
    sys.settrace(None)
    
    print(f"\nResult: {result}\n")


# =============================================================================
# EXPERIMENT 4: Filtering - don't trace standard library
# =============================================================================

def filtered_tracer(frame, event, arg):
    """
    Only trace user code, not Python's built-in libraries.
    Without this filter, you'd see thousands of irrelevant events.
    """
    filename = frame.f_code.co_filename
    
    # Skip standard library and site-packages
    if 'lib/python' in filename or 'site-packages' in filename:
        return None  # Don't trace this frame at all
    
    if event == 'line':
        print(f"  [USER CODE] {filename}:{frame.f_lineno}")
    
    return filtered_tracer


def run_experiment_4():
    print("=" * 60)
    print("EXPERIMENT 4: Filtering out standard library")
    print("=" * 60)
    print("Only show user code, not Python internals:\n")
    
    sys.settrace(filtered_tracer)
    
    # Even though we use json (stdlib), it won't be traced
    import json
    data = {"name": "Abishek", "project": "PuriPy"}
    serialized = json.dumps(data)
    parsed = json.loads(serialized)
    
    sys.settrace(None)
    
    print(f"\nResult: {parsed}\n")


# =============================================================================
# RUN ALL EXPERIMENTS
# =============================================================================

if __name__ == "__main__":
    run_experiment_1()
    run_experiment_2()
    run_experiment_3()
    run_experiment_4()
    
    print("=" * 60)
    print("DAY 1 COMPLETE!")
    print("=" * 60)
    print("""
What you should now understand:
  ✓ sys.settrace() installs a tracer function
  ✓ The tracer is called before every line, function call, and return
  ✓ frame.f_locals gives you all variables in scope
  ✓ frame.f_lineno gives you the current line number
  ✓ frame.f_code.co_filename gives you the file path
  ✓ You can filter out frames you don't care about

Next steps (Day 2):
  - Read the official Python docs on sys.settrace
  - Try modifying the tracers above to capture different things
  - Think about: how would you save this trace to a file?
""")
