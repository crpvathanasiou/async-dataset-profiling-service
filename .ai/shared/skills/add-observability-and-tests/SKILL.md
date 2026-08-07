# Skill: add-observability-and-tests

## Description

Use this skill when finalizing or improving a feature.

## When to use

- after implementing a feature
- when hardening a change for review

## Requirements

### Observability

- add or preserve container-friendly logging to stdout/stderr where useful
- do not invent tracing infrastructure without a requirement

### Testing

- add tests that prove the intended behavior
- cover happy path and relevant failure/invalid-input cases

### Boundaries

- do not modify unrelated code
- focus only on current feature scope

## Deliverables

- logging improvements when needed
- test files
- summary of what the tests prove
