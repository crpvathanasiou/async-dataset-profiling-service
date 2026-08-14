A. **Upload and Validate**

1. **Terminal 1 — ξεκίνα το FastAPI**

```powershell
poetry run uvicorn app.main:app --reload
```

Τρέχει το API στο `http://127.0.0.1:8000`.

2. **Terminal 2 — ξεκίνα τον worker**

```powershell
poetry run python -m app.worker.main
```

Κάνει long-poll στο SQS και περιμένει jobs.

3. **Terminal 3 — δημιούργησε test CSV**

```powershell
"customer_id,amount,date`n1,120.5,2026-08-01`n2,75.0,2026-08-02" | Set-Content test-data.csv
```

Φτιάχνει ένα valid CSV.

4. **Ζήτα presigned upload URL**

```powershell
$response = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/intake/uploads" -ContentType "application/json" -Body '{"filename":"test-data.csv"}'
```

Δημιουργεί `job_id`, `s3_key`, presigned URL.

5. **Δες την απάντηση**

```powershell
$response
```

6. **Ανέβασε το αρχείο απευθείας στο S3**

```powershell
Invoke-WebRequest -Method Put -Uri $response.upload_url -InFile ".\test-data.csv" -UseBasicParsing
```

7. **Ζήτα validation**

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/intake/jobs/$($response.job_id)/validate"
```

Κάνει S3 validation και, αν είναι valid, publish στο SQS.

8. **Κοίτα το Terminal 2**
   Πρέπει να δεις `processing_start → processing_success → message_delete`.

Note:
Terminal 1 → FastAPI
Terminal 2 → Worker
Terminal 3 → όλες οι manual client εντολές:
           - δημιουργία CSV
           - request για presigned URL
           - upload στο S3
           - /validate







B. **Upload - Validate - SQS**

Πάμε με **happy-path SQS manual test**, ώστε να δεις το μήνυμα πραγματικά μέσα στην queue πριν το πάρει ο worker.

1. **Terminal 1 — FastAPI**

```powershell
poetry run uvicorn app.main:app --reload
```

2. **ΜΗΝ ανοίξεις ακόμα τον Worker.** Θέλουμε το message να μείνει visible στο SQS.

3. **Terminal 3 — φτιάξε valid CSV**

```powershell
"customer_id,amount,date`n1,120.5,2026-08-01`n2,75.0,2026-08-02" | Set-Content test-data.csv
```

4. **Δημιούργησε job + presigned URL**

```powershell
$response = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/intake/uploads" -ContentType "application/json" -Body '{"filename":"test-data.csv"}'
```

5. **Upload στο S3**

```powershell
Invoke-WebRequest -Method Put -Uri $response.upload_url -InFile ".\test-data.csv" -UseBasicParsing
```

6. **Κάνε validation — εδώ θα γίνει το SQS publish**

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/intake/jobs/$($response.job_id)/validate"
```

Τώρα **σταμάτα εδώ**. Μην ανοίξεις worker.

Στο AWS Console πήγαινε:

```text
SQS
→ async-dataset-profiling-dev-jobs
→ Send and receive messages
→ Poll for messages
```

Η AWS Console μπορεί να κάνει `ReceiveMessage` και να σου δείξει `Message ID`, receive count και το body. ([AWS Documentation][1])

Θα πρέπει να δεις body περίπου:

```json
{
  "message_id": "...",
  "message_type": "PROCESS_DATASET",
  "schema_version": 1,
  "created_at": "...",
  "job_id": "...",
  "payload": {
    "s3_bucket": "...",
    "s3_key": "incoming/.../test-data.csv"
  }
}
```

**Προσοχή:** το `Poll for messages` της Console είναι και αυτό πραγματικό receive. Άρα το message γίνεται προσωρινά **in-flight / invisible** για το visibility timeout. Αν δεν το διαγράψεις, θα ξαναγίνει visible όταν λήξει το timeout. ([AWS Documentation][2])

Μετά, όταν ξαναγίνει visible, άνοιξε **Terminal 2**:

```powershell
poetry run python -m app.worker.main
```

Θα δεις:

```text
processing_start
↓
5 sec simulated processing
↓
processing_success
↓
message_delete
```

Το `DeleteMessage` είναι το acknowledgement: μετά από αυτό το συγκεκριμένο message δεν πρέπει πλέον να επιστρέψει στην queue. ([AWS Documentation][2])

Αυτό το test θα σου δείξει πρακτικά όλο το SQS lifecycle:

```text
Publish
→ Visible
→ Receive
→ In-flight / Invisible
→ Process
→ Delete
```

και είναι το καλύτερο test να κάνουμε **πριν το DynamoDB**.

[1]: https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/step-receive-delete-message.html?utm_source=chatgpt.com "Receiving and deleting a message in Amazon SQS"
[2]: https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html?utm_source=chatgpt.com "Amazon SQS visibility timeout"













