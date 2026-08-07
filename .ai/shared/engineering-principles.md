# Engineering Principles

## Purpose

Core engineering principles for this repository. They are reusable and enforceable through code, structure, and review.

## 1. System design philosophy

### Build for clarity, not cleverness
- Prefer explicit design over implicit behavior
- Avoid hidden coupling
- Avoid "magic" abstractions that obscure behavior

### Design for change
- Assume requirements will evolve
- Keep modules loosely coupled
- Avoid premature optimization and rigid abstractions

### Favor composition over monolith logic
- Prefer focused components over one large generalized component
- Add structure only when complexity justifies it

## 2. Separation of concerns

Keep transport, configuration, and domain logic separable as the system grows.

For the current baseline:
- FastAPI routes handle HTTP transport
- settings load configuration from the environment
- do not invent service/repository/controller layers without a concrete need

## 3. Contracts first

- Prefer Pydantic models for request/response and configuration contracts
- Make inputs and outputs explicit
- Avoid implicit assumptions about data shape

## 4. Production discipline

- Configuration via environment variables
- Container-friendly logging (stdout/stderr)
- Honest health endpoints
- Automated quality checks (Ruff, Pyright, Pytest)
- Prefer small, reviewable changes

## 5. No overengineering

Do not introduce:
- abstractions for imagined future requirements
- infrastructure not required by the current task
- dependency-injection frameworks, event buses, or domain layers without need

## 6. Verification

- Every feature ships with a test that proves behavior
- Prefer the smallest correct verification layer
- A feature without verification is incomplete

## Final principle

> Prefer the simplest design that is correct, observable, and easy to change.
