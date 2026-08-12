"""
Versioned asynchronous message contracts shared by the API and the worker.

This package is deliberately separate from both `app/intake` (publisher) and
`app/worker` (consumer): the envelope is the explicit application-level contract
shared by publisher and consumer, so it must not live inside either of them.

    intake service --serialize--> SQS --deserialize--> worker service
                          both sides use app/messaging/schemas.py

Transport details (boto3, queue URLs) belong to `app/infrastructure`, and
processing behavior belongs to `app/worker`; this package only defines shape
and meaning of the messages.
"""
