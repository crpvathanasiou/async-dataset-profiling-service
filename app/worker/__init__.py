"""
Separate long-running SQS worker process (not FastAPI).

    main.py     process entrypoint: configuration, logging, signal handling
    service.py  the receive/process/delete loop and the job processor

This package runs as its own OS process, next to (not inside) the API process.
The two never call each other; the SQS queue is the only link between them:

    API process --SendMessage--> SQS --ReceiveMessage--> worker process

Nothing here serves HTTP, and nothing here imports the FastAPI application.
"""
