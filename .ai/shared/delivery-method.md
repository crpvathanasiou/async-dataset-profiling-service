# Delivery Method

## Purpose

Standard delivery method for AI-assisted engineering in this repository.

## 1. Core delivery philosophy

1. Build in controlled increments
2. Clarify scope before implementation
3. Prefer contracts before complex logic
4. Extend existing foundations instead of redesigning them

## 2. Standard delivery sequence

### Step 1: Read context
Read relevant shared and project documents before non-trivial work.

### Step 2: Restate scope
Confirm what is in scope and what is not.

### Step 3: Propose a plan
Provide:
- short implementation plan
- files to create or modify
- assumptions
- out-of-scope items

### Step 4: Implement only the requested scope
Do not expand into adjacent features.

### Step 5: Verify
Provide:
- implementation
- tests
- exact test/quality commands
- what passing checks prove

### Step 6: Stop on ambiguity
Ask for clarification instead of guessing critical requirements.

## 3. Scope discipline

- implement only the requested milestone or task
- list discovered adjacent work as out of scope
- do not redesign the baseline without instruction

## 4. Definition of done

Work is done when:

- requested behavior exists
- tests/quality checks pass
- scope was respected
- no speculative infrastructure was added

## Final principle

> Small, verifiable steps beat large speculative designs.
