"""
Infrastructure adapter layer.

Every module in this package wraps one external system (currently AWS S3 and
AWS SQS) behind a small application-facing interface. boto3 imports and
boto3 exception types are expected to appear only here.

Architectural rule:
    routers      -> HTTP concerns only
    services     -> decide WHAT should happen and in which order
    adapters     -> know HOW to talk to a specific external system

Consequences of keeping that rule:
- service and worker code can be unit tested with fake adapters, without AWS
- AWS SDK error text never leaks into API responses; adapters translate
  vendor errors into a few application-level exception types
- swapping or upgrading an SDK stays local to this package

Business rules, job state, and orchestration do not belong in this package.
"""
