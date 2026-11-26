# Scalability, Security, and Cost Model
## Scale to 100,000 registered users
Registered user count alone does not size voice infrastructure. Size capacity against peak simultaneous calls, call duration, STT/TTS throughput, LLM requests per minute, and network bandwidth.
| MVP component | Scale-up approach |
|---|---|
| FastAPI | Stateless replicas behind load balancer/autoscaling |
| Sessions | Redis or durable session store |
| Database | SQLite to PostgreSQL, backups, indexes, replicas where appropriate |
| Background tasks | Durable queue with worker autoscaling |
| RAG | Managed/distributed vector DB or partitioned retrieval; cache FAQs |
| Speech | Separate STT/TTS workers; capacity and concurrency quotas |
| Observability | Central metrics, logs, traces, alerting |
## Security
- Store API keys in secrets manager/environment; never commit `.env`.
- Treat caller input and retrieved documents as untrusted; don't let text change system instructions.
- Validate booking IDs, dates, and ownership; application code performs booking.
- Add authentication, authorization, tenant separation, rate limits, TLS, encryption, retention controls, and audit logs before production.
- Obtain recording/transcription consent; disclose retention and delete data according to policy.
## Cost estimation
This MVP does not assert current provider prices. Estimate monthly cost as:
`hosting + LLM input/output tokens + STT audio minutes + TTS characters/audio minutes + DB/storage + network + monitoring`
Track average call duration, tokens per call, audio minutes, concurrency, retries, and retention. Build low/medium/high usage scenarios using current vendor rates.
## Commercial feasibility
Potential value includes 24/7 lead response, fewer missed inquiries, automatic qualification, consistent FAQ answers, appointment scheduling, call summaries, and lead prioritization. Pilot and measure conversion, response latency, qualified lead rate, escalation rate, cost per call, and customer satisfaction.
