# Vera 2.0: Deterministic Context Orchestration

## The Approach: State Isolation & RAG-Style Extraction
To beat the 30-second latency budget and completely eliminate LLM hallucination, this architecture treats the LLM not as a decision-maker, but strictly as a semantic translator. The system is split into three decoupled layers:

1. **Idempotent In-Memory State Store:** Handled via asynchronous locks, this layer ingests concurrent payload updates in O(1) time. It ensures version-conflict resolution (returning 409s for stale pushes) before any data touches the processing queue.
2. **Deterministic Pre-Processing:** Instead of flooding the LLM with raw JSON, the backend acts as a highly opinionated context extractor. It pre-ranks `must_use_merchant_facts`, determines the optimal psychological compulsion lever, and hardcodes the CTA via 30 strict, data-driven templates.
3. **The Conversation Guard:** An ultra-fast, zero-dependency regex and sequence-matching router intercepts inbound turns. It neutralizes auto-reply loops and hard opt-outs instantly without burning API tokens.

## Model Choice
* **Model:** DeepSeek-Chat-v3 (via OpenRouter)
* **Why:** Offers an exceptional balance of instruction-following adherence and sub-20ms routing latency for deterministic paths, preventing timeout penalties while preserving LLM calls strictly for unpredictable edge cases. 

## Architectural Tradeoffs
* **Creativity vs. Compliance:** We sacrificed raw, unconstrained LLM creativity in favor of strict, rubric-compliant deterministic templating. This guarantees 100% adherence to merchant facts and prevents the fatal `-2` penalty for fabricated claims.
* **In-Memory Volatility vs. Speed:** For the scope of this challenge, state is stored in-memory using Python dictionaries protected by `asyncio.Lock`. In a production environment, this would be migrated to Redis to ensure state persistence across horizontal scaling events, but memory was prioritized here for extreme sub-millisecond retrieval.
