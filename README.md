\# SupportOps AI



An AI agent for customer support. It reads a complaint and decides what to do: solve it with real tools (order lookup, address and phone changes, cancellation), answer from a knowledge base with the evidence it used, or hand the case to a human agent together with the full conversation.



Everything runs locally on CPU with free, open tooling: Ollama (Qwen2.5-1.5B), ChromaDB, SQLite, FastAPI and Streamlit. No paid API and no GPU.



\## Screenshots



| Customer chat | Admin inbox |

|---|---|

| !\[Chat](docs/chat.png) | !\[Admin](docs/admin.png) |



!\[Agent internals](docs/internals.png)



\## How it works



```
Customer
   |
   v
Streamlit chat  <------------------------------ human reply ----+
   |                                                             |
   v                                                             |
FastAPI                                                          |
   |                                                             |
   v                                                             |
Agent                                                            |
   |                                                             |
   |-- order request --------> Order flow ------> Orders DB      |
   |                                                             |
   |-- general question -----> RAG (ChromaDB) --> Qwen2.5-1.5B   |
   |                           via Ollama                         |
   '-- no evidence / sensitive case / late order                 |
                 |                                               |
                 v                                               |
            Tickets DB ------> Admin inbox -----------------------+
```


| Technique | Where | What it does here |

|---|---|---|

| LLM | `app/llm.py`, `app/agent.py` | Triage as strict JSON (category, urgency, sentiment, summary) and a relevance judge for borderline retrieval results |

| RAG | `app/rag.py` | 1,998 customer questions embedded with `nomic-embed-text`, cosine search in ChromaDB, retrieved evidence shown with similarity scores, category-filtered retry when the first search misses |

| Generative AI | `app/llm.py` | Reply drafted from the retrieved answers only, then post-processed: template placeholders removed, unknown contact details dropped, step-by-step instructions rejected (stricter retry), length capped |

| Agentic AI | `app/agent.py`, `app/flow.py`, `app/orders.py` | Chooses between tools (knowledge-base search, classifier, order lookup / update / cancel, human escalation) following explicit policies |

| Python API | `app/api.py` | FastAPI service for chat, conversations, tickets and the admin inbox |

| Databases | SQLite | Conversations, tickets and per-conversation state; a 1,000-order dataset; an audit log of every order change |



\## Agent policy



| Situation | Decision |

|---|---|

| Question the knowledge base can answer | Answer, citing the evidence used |

| Order status, address, phone or cancellation request | Verify order number + email, then use the order tools |

| Change of address, phone or cancellation | Ask for explicit confirmation (yes / no) before writing anything |

| Order is 3 or more days late | Reply with the status and open a ticket with the order attached |

| Critical keyword (fraud, legal threat...) or explicit request for a human | Open a ticket immediately |

| No relevant evidence, or three failed verifications | Open a ticket |

| A human already owns the case | Add the message to the ticket, no model call |



Status rules: address changes are allowed before shipping, phone changes until delivery, cancellation before shipping.



\## Design decisions



\- \*\*The LLM never writes to a database.\*\* It can only trigger tool functions, and each tool re-checks identity, order status and input validity on its own.

\- \*\*Order facts come from code, not from the model.\*\* Status messages are built from templates over database fields, so dates and tracking numbers cannot be invented.

\- \*\*Defense in depth.\*\* The status rule is checked in Python and again inside the SQL `UPDATE ... WHERE status IN (...)`.

\- \*\*No order probing.\*\* An unknown order number and a wrong email return the same error, and three failed attempts open a ticket.

\- \*\*Everything is audited.\*\* Each change stores old value, new value, conversation and actor.

\- \*\*Small-model aware.\*\* A 1.5B model is not trusted for routing or writes, so those use rules. The model handles triage, grading and phrasing.



\## Data



\- \*\*Knowledge base:\*\* \[Bitext customer support dataset](https://huggingface.co/datasets/bitext/Bitext-customer-support-llm-chatbot-training-dataset) (synthetic, CDLA-Sharing-1.0), stratified sample of 1,998 rows across 27 intents.

\- \*\*Orders:\*\* 1,000 orders sampled from the Olist public e-commerce data (Hugging Face mirror `Zeyustun/orders`). Statuses and the gaps between steps are real. Dates are shifted so the data looks current. Emails, addresses, phones, carriers, tracking numbers and totals are synthetic. Check the original Olist terms before any commercial use.



\## Evaluation



Measured on a laptop with 8 GB RAM and no GPU.



| Check | Result |

|---|---|

| Retrieval on 150 held-out questions: intent hit@1 / hit@4 / category@1 | 98% / 99% / 100% |

| Average top-1 score, correct vs wrong match | 0.926 vs 0.817 |

| Knowledge-base path on 15 held-out questions (initial run): answers grounded in the right intent | 15 / 15 |

| LLM category classification (same run) | 10 / 15 |

| Average time per request on CPU | about 12 s |

| Out-of-scope messages escalated (4 independent, 1 used while tuning) | 5 / 5 |

| Automated tests (run without an LLM) | 72 |



Read these with care: the held-out questions come from the same generator as the indexed ones, so real-world accuracy will be lower, and the samples are small. LLM classification is the weakest component and is shown next to the category taken from the retrieved evidence.



Run it yourself: `python app/eval.py 15`



\## Run it locally



Requirements: Python 3.11+, \[Ollama](https://ollama.com), about 8 GB RAM.



```bash

git clone https://github.com/3BDO145/Ai-Support-Chat.git

cd Ai-Support-Chat

python -m venv venv

venv\\Scripts\\activate          # Linux/macOS: source venv/bin/activate

pip install -r requirements.txt



ollama pull qwen2.5:1.5b

ollama pull nomic-embed-text



copy .env.example .env         # Linux/macOS: cp .env.example .env, then set ADMIN\_PASSWORD

python app/rag.py build        # embeds the knowledge base, a few minutes on CPU

python app/prepare\_orders.py   # builds the demo orders database

```



Start the API and the UI in two terminals:



```bash

python -m uvicorn --app-dir app api:app --port 8000

streamlit run ui.py

```



Open http://localhost:8501. With `DEMO\_MODE=1` the sidebar lists demo orders (number and email) to try the order flows. The support inbox is the \*\*admin\*\* page and uses `ADMIN\_PASSWORD`. Interactive API docs: http://127.0.0.1:8000/docs



Tests: `python -m unittest discover -s tests`



The knowledge base CSV is included. To regenerate it, run `python app/prepare\_data.py`, then rebuild the index.



\## API



| Endpoint | Purpose |

|---|---|

| `POST /chat` | Send a customer message, returns the agent decision and reply |

| `GET /conversations/{id}` | Full conversation and ticket state |

| `GET /demo/orders` | Demo order credentials (only when `DEMO\_MODE=1`) |

| `GET /admin/tickets`, `GET /admin/tickets/{id}` | Inbox (password header) |

| `GET /admin/orders/{order\_no}` | Order record and change history (password header) |

| `POST /admin/tickets/{id}/reply`, `POST /admin/tickets/{id}/resolve` | Answer or close a ticket |



\## Project structure



```

app/

&#x20; agent.py          decision logic and escalation rules

&#x20; flow.py           order conversation: verify, confirm, execute

&#x20; orders.py         order tools, business rules, audit log

&#x20; rag.py            embeddings, ChromaDB index, retrieval

&#x20; llm.py            triage, reply generation, output guardrails

&#x20; api.py            FastAPI service

&#x20; db.py             conversations, tickets, state

&#x20; eval.py           evaluation on held-out questions

&#x20; prepare\_data.py   builds the knowledge base sample

&#x20; prepare\_orders.py builds the orders database

pages/admin.py      support inbox

ui.py               customer chat

tests/              72 unit tests

```



\## Limitations



\- English only. The data and the model are English.

\- Order routing uses keyword rules, not model judgment, by design for a 1.5B model.

\- The knowledge base is synthetic template text, so answers are generic.

\- Address validation checks format only, and cancellation changes the database only (no payment system).

\- The verification limit applies inside one conversation. A real deployment needs a global rate limit.

\- Replies take around 12 s on CPU.



\## License



MIT for the code. The datasets keep their own licenses.

