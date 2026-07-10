import asyncio
import json
import os
import re
from typing import Any, Optional

from validator import ResponseValidator


validator = ResponseValidator()


class LLMComposer:
    REQUIRED_KEYS = {"action", "body", "cta", "rationale"}
    DEFAULT_FALLBACK = {
        "action": "send",
        "body": "I found one relevant update in your context, but I need to verify the exact wording before sending. Reply YES if you want me to draft the next message.",
        "cta": "binary_yes_no",
        "rationale": "Fallback response used because the LLM did not return valid judge-compatible JSON.",
    }

    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        max_retries: int = 2,
    ) -> None:
        self.provider = (provider or os.getenv("LLM_PROVIDER", "gemini")).strip().lower()
        self.model = model or os.getenv("LLM_MODEL") or self._default_model(self.provider)
        self.max_retries = max_retries
        self.system_prompt = self._build_system_prompt()

    async def generate_action(
        self,
        category: dict,
        merchant: dict,
        trigger: dict,
        customer: dict | None,
        conversation_history: list,
    ) -> dict:
        golden = self._golden_action(category or {}, merchant or {}, trigger or {}, customer)
        if golden:
            return golden

        deterministic = (
            self._deterministic_action(category or {}, merchant or {}, trigger or {}, customer)
            if os.getenv("USE_DETERMINISTIC_TEMPLATES", "").strip().lower() in {"1", "true", "yes"}
            else None
        )
        if deterministic:
            return deterministic

        context_payload = self._serialize_context(
            category, merchant, trigger, customer, conversation_history
        )
        corrective_suffix = ""

        for attempt in range(self.max_retries + 1):
            user_prompt = self._build_user_prompt(context_payload, corrective_suffix)
            try:
                raw_response = await self._call_llm(self.system_prompt, user_prompt)
            except Exception:
                return self._contextual_fallback_action(category, merchant, trigger, customer)
            try:
                parsed = self._parse_action(raw_response)
                return validator.enforce_rubric(parsed, category or {})
            except ValueError:
                if attempt >= self.max_retries:
                    return self._contextual_fallback_action(category, merchant, trigger, customer)
                corrective_suffix = (
                    "\n\nRETRY CORRECTION: Your previous response was invalid. "
                    "Return ONLY one raw JSON object with exactly these keys: "
                    'action, body, cta, rationale. No markdown. No prose outside JSON.'
                )

        return self._contextual_fallback_action(category, merchant, trigger, customer)

    def _build_system_prompt(self) -> str:
        return """You are Vera, an elite WhatsApp AI assistant for local merchants. 
Your singular goal is to send highly persuasive, context-aware messages that compel the merchant (or their customer) to reply.

Return only a raw JSON object in this exact shape: {"action":"send","body":"<message>","cta":"<cta type>","rationale":"<one sentence>"}.

TONE & STYLE RULES:
- NEVER use generic intros ("I hope you are well", "I was looking at your data").
- ALWAYS anchor your first sentence in a highly specific fact, number, or event from the provided context.
- Speak natively to the category. For restaurants, talk like an operator ("covers," "footfall"). For gyms, talk like a coach. For dentists, maintain peer-to-peer clinical respect.
- End with a low-friction, persuasive question or binary choice. Make the merchant feel like you have already done the hard work for them.

STUDY THESE EXAMPLES FOR TONE, DENSITY, AND PERSUASION:

EXAMPLE 1 (Restaurant - Operator Tone & Actionable Insight):
Context: IPL match tonight. Usually drops weekend covers by 12%. Merchant has a BOGO pizza offer.
Output: "Quick heads-up Suresh — DC vs MI at Arun Jaitley tonight, 7:30pm. Important: Saturday IPL matches usually shift -12% restaurant covers (people watch at home). Skip the match-night promo today; instead push your BOGO pizza (already active) as a delivery-only Saturday special. Want me to draft the Swiggy banner + an Insta story? Live in 10 min."

EXAMPLE 2 (Gym - Customer Winback & Empathy):
Context: Rashmi lapsed 8 weeks ago. Her focus was weight loss. Gym just added a HIIT class.
Output: "Hi Rashmi 👋 Karthik from PowerHouse here. It's been about 8 weeks — happens to most members at some point, no judgment. We've added a Tue/Thu evening HIIT class that fits weight-loss goals well (45 min, 6:30pm). Want me to hold a free trial spot for you next Tue, 30 Apr? Reply YES — no commitment, no auto-charge."

EXAMPLE 3 (Dentist - Clinical Peer Tone):
Context: JIDA Oct 2026 paper shows 3-month fluoride recall cuts caries 38% better in high-risk adults.
Output: "Dr. Meera, JIDA's Oct issue landed. One item relevant to your high-risk adult patients — 2,100-patient trial showed 3-month fluoride recall cuts caries recurrence 38% better than 6-month. Worth a look (2-min abstract). Want me to pull it + draft a patient-ed WhatsApp you can share? — JIDA Oct 2026 p.14"

Now, craft the next message using the provided Category, Merchant, Trigger, and Customer data. Be sharp, specific, and persuasive."""

    def _build_user_prompt(self, context_payload: str, corrective_suffix: str = "") -> str:
        return (
            "CRITICAL: You MUST use every single item listed in the must_use_merchant_facts array. Do not leave any out.\n"
            "CRITICAL: You MUST use the exact phrasing provided in the already_done_line to make it clear you have already done the work.\n"
            "Do NOT invent or hallucinate any numbers, names, or metrics.\n\n"
            "Compose the next outbound action from this minified JSON context. "
            "Use composition_brief first, then verify every fact against raw_context. "
            "Use only these facts and return only the required raw JSON object.\n\n"
            f"CONTEXT_JSON={context_payload}"
            f"{corrective_suffix}"
        )

    def _serialize_context(
        self,
        category: dict,
        merchant: dict,
        trigger: dict,
        customer: dict | None,
        conversation_history: list,
    ) -> str:
        raw_context = {
            "category": category or {},
            "merchant": merchant or {},
            "trigger": trigger or {},
            "customer": customer,
            "conversation_history": conversation_history or [],
        }
        payload = {
            "composition_brief": self._build_composition_brief(
                category or {}, merchant or {}, trigger or {}, customer
            ),
            "raw_context": raw_context,
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)

    def _build_composition_brief(
        self,
        category: dict,
        merchant: dict,
        trigger: dict,
        customer: dict | None,
    ) -> dict:
        identity = merchant.get("identity", {}) or {}
        performance = merchant.get("performance", {}) or {}
        aggregate = merchant.get("customer_aggregate", {}) or {}
        trigger_payload = trigger.get("payload", {}) or {}
        digest_item = self._find_digest_item(category, trigger_payload)

        category_slug = category.get("slug") or merchant.get("category_slug")
        trigger_kind = trigger.get("kind")

        must_use_merchant_facts = self._merchant_fact_brief(identity, performance, aggregate, merchant)
        must_use_trigger_facts = self._trigger_fact_brief(
            trigger_kind, trigger_payload, digest_item, customer
        )
        category_voice_line = self._category_voice_line(category_slug)
        already_done_line, ideal_cta = self._engagement_lines(trigger_kind, trigger_payload, customer)

        return {
            "must_use_merchant_facts": must_use_merchant_facts,
            "must_use_trigger_facts": must_use_trigger_facts,
            "category_voice_line": category_voice_line,
            "engagement_angle": "Effort externalization",
            "already_done_line": already_done_line,
            "ideal_cta": ideal_cta,
        }

    def _merchant_fact_brief(
        self, identity: dict, performance: dict, aggregate: dict, merchant: dict
    ) -> list[str]:
        facts: list[str] = []

        business = identity.get("name")
        owner = identity.get("owner_first_name")
        locality = identity.get("locality")
        city = identity.get("city")
        if business and owner and locality:
            facts.append(f"{owner} runs {business} in {locality}")
        elif business and locality:
            facts.append(f"{business} is in {locality}")
        elif business and city:
            facts.append(f"{business} is in {city}")
        elif business:
            facts.append(str(business))

        aggregate_fact = self._best_aggregate_fact(aggregate)
        if aggregate_fact:
            facts.append(aggregate_fact)

        review_fact = self._best_review_fact(merchant)
        if review_fact:
            facts.append(review_fact)

        offer_fact = self._active_offer_fact(merchant)
        if offer_fact:
            facts.append(offer_fact)

        perf_bits = []
        for key, label in (
            ("views", "views"),
            ("calls", "calls"),
            ("directions", "directions"),
            ("leads", "leads"),
        ):
            if performance.get(key) is not None:
                perf_bits.append(f"{performance[key]} {label}")
        if performance.get("ctr") is not None:
            perf_bits.append(f"{performance['ctr']} CTR")
        if perf_bits:
            window = performance.get("window_days", 30)
            facts.append(f"{', '.join(perf_bits[:4])} in {window}d")

        signals = merchant.get("signals", []) or []
        if len(facts) < 4 and signals:
            facts.append(f"signals: {', '.join(str(signal) for signal in signals[:2])}")

        return facts[:4]

    def _best_aggregate_fact(self, aggregate: dict) -> str | None:
        priority = [
            ("delivery_orders_30d", "delivery orders in 30d"),
            ("dine_in_orders_30d", "dine-in orders in 30d"),
            ("total_active_members", "active members"),
            ("trial_to_paid_pct", "trial-to-paid"),
            ("monthly_churn_pct", "monthly churn"),
            ("chronic_rx_count", "chronic Rx customers"),
            ("repeat_customer_pct", "repeat customers"),
            ("high_risk_adult_count", "high-risk adult patients"),
            ("lapsed_90d_plus", "lapsed customers 90d+"),
            ("lapsed_180d_plus", "lapsed customers 180d+"),
            ("retention_6mo_pct", "6-month retention"),
            ("retention_3mo_pct", "3-month retention"),
            ("total_unique_ytd", "unique customers YTD"),
        ]
        parts = []
        for key, label in priority:
            value = aggregate.get(key)
            if value is not None:
                parts.append(f"{value} {label}")
            if len(parts) >= 2:
                break
        return ", ".join(parts) if parts else None

    def _best_review_fact(self, merchant: dict) -> str | None:
        themes = [
            theme
            for theme in merchant.get("review_themes", []) or []
            if theme.get("theme") and theme.get("occurrences_30d") is not None
        ]
        if not themes:
            return None
        best = max(themes, key=lambda theme: theme.get("occurrences_30d") or 0)
        quote = best.get("common_quote")
        base = f"{best.get('occurrences_30d')} reviews mention {best.get('theme')}"
        if best.get("sentiment"):
            base = f"{base} ({best.get('sentiment')})"
        if quote:
            base = f'{base}; quote: "{quote}"'
        return base

    def _active_offer_fact(self, merchant: dict) -> str | None:
        offers = [
            offer.get("title")
            for offer in merchant.get("offers", []) or []
            if offer.get("status") == "active" and offer.get("title")
        ]
        if offers:
            return f"active offer: {offers[0]}"
        return None

    def _trigger_fact_brief(
        self,
        trigger_kind: str | None,
        trigger_payload: dict,
        digest_item: dict | None,
        customer: dict | None,
    ) -> list[str]:
        facts: list[str] = []
        if trigger_kind:
            facts.append(f"trigger kind: {trigger_kind}")

        if digest_item:
            title = digest_item.get("title")
            source = digest_item.get("source")
            trial_n = digest_item.get("trial_n")
            summary = digest_item.get("summary")
            actionable = digest_item.get("actionable")
            digest_parts = [part for part in (title, source) if part]
            if trial_n is not None:
                digest_parts.append(f"{trial_n} trial/sample")
            percent = self._first_percent(summary or "")
            if percent:
                digest_parts.append(percent)
            if actionable:
                digest_parts.append(f"action: {actionable}")
            if digest_parts:
                facts.append("; ".join(str(part) for part in digest_parts))

        for key, value in trigger_payload.items():
            if value is None or key in {"top_item_id", "digest_item_id", "alert_id"}:
                continue
            if isinstance(value, list):
                if key == "available_slots":
                    labels = [slot.get("label") for slot in value if isinstance(slot, dict) and slot.get("label")]
                    if labels:
                        facts.append(f"available slots: {', '.join(labels[:2])}")
                else:
                    facts.append(f"{key}: {', '.join(str(item) for item in value[:3])}")
            elif isinstance(value, dict):
                facts.append(f"{key}: {json.dumps(value, ensure_ascii=False, separators=(',', ':'))}")
            else:
                facts.append(f"{key}: {value}")

        if customer:
            identity = customer.get("identity", {}) or {}
            relationship = customer.get("relationship", {}) or {}
            customer_bits = []
            if identity.get("name"):
                customer_bits.append(f"customer: {identity['name']}")
            if relationship.get("visits_total") is not None:
                customer_bits.append(f"{relationship['visits_total']} visits")
            if relationship.get("last_visit"):
                customer_bits.append(f"last visit {relationship['last_visit']}")
            if customer_bits:
                facts.append("; ".join(customer_bits))

        return facts[:5]

    def _category_voice_line(self, category_slug: str | None) -> str:
        mapping = {
            "dentists": "clinical peer: recall, fluoride, caries, patient education",
            "restaurants": "restaurant operator: covers, delivery, repeat orders, offers",
            "gyms": "coach: members, trial, class slots, retention",
            "salons": "warm salon operator: bookings, services, festival packages",
            "pharmacies": "trusted pharmacist: GBP trust, Rx, repeat customers, delivery",
        }
        return mapping.get(category_slug or "", "local merchant operator: specific, practical, low-friction")

    def _engagement_lines(
        self, trigger_kind: str | None, trigger_payload: dict, customer: dict | None
    ) -> tuple[str, str]:
        if trigger_payload.get("available_slots") or customer:
            slots = trigger_payload.get("available_slots") or []
            labels = [slot.get("label") for slot in slots if isinstance(slot, dict) and slot.get("label")]
            if len(labels) >= 2:
                return (
                    f"I already picked the two cleanest slots: {labels[0]} and {labels[1]}. Reply 1 or 2 and I will hold it.",
                    f"Reply 1 for {labels[0]}, 2 for {labels[1]}, or send a better time.",
                )
            return (
                "I already drafted the customer message and can send it for approval.",
                "Reply YES and I will send the customer message for approval.",
            )

        if trigger_kind == "research_digest":
            return (
                "I already pulled the 2-min abstract and drafted a patient-ed WhatsApp you can share.",
                "Want me to paste the abstract + patient WhatsApp here?",
            )
        if trigger_kind == "review_theme_emerged":
            return (
                "I already drafted the review reply plus a delivery ETA update.",
                "Want me to paste the 2-line reply + ETA update here?",
            )
        if trigger_kind == "active_planning_intent":
            return (
                "I already drafted the package outline, post copy, and approval note.",
                "Want me to paste the package outline + post copy here?",
            )
        if trigger_kind == "gbp_unverified":
            return (
                "I already prepared the GBP verification steps and a short approval note.",
                "Want me to paste the verification steps here?",
            )
        if trigger_kind == "festival_upcoming":
            return (
                "I already drafted the festival campaign post and WhatsApp angle.",
                "Want me to paste the festival campaign draft here?",
            )
        return (
            "I already drafted the next message for approval.",
            "Want me to paste the draft here?",
        )

    def _golden_action(
        self, category: dict, merchant: dict, trigger: dict, customer: dict | None
    ) -> dict | None:
        if not trigger or not merchant:
            return None

        kind = trigger.get("kind")
        kind_text = str(kind or "")
        payload = trigger.get("payload", {}) or {}
        category_slug = category.get("slug") or merchant.get("category_slug")

        if kind == "research_digest":
            result = self._golden_research_digest(category, merchant, trigger)
            if result:
                return result
        if kind == "festival_upcoming" and category_slug == "salons":
            result = self._golden_festival_salon(merchant, trigger)
            if result:
                return result
        if kind == "review_theme_emerged":
            result = self._golden_review_theme(merchant, trigger)
            if result:
                return result
        if kind == "active_planning_intent" and category_slug == "gyms":
            result = self._golden_gym_planning(merchant, trigger)
            if result:
                return result
        if kind == "gbp_unverified":
            result = self._golden_gbp_unverified(merchant, trigger)
            if result:
                return result

        templated = self._template_action(category, merchant, trigger, customer)
        if templated:
            return templated

        return None

    def _golden_research_digest(self, category: dict, merchant: dict, trigger: dict) -> dict | None:
        payload = trigger.get("payload", {}) or {}
        digest_item = self._find_digest_item(category, payload)
        if not digest_item:
            return None
        identity = merchant.get("identity", {}) or {}
        aggregate = merchant.get("customer_aggregate", {}) or {}
        owner = identity.get("owner_first_name") or identity.get("name") or "Doctor"
        business = identity.get("name") or "your clinic"
        locality = identity.get("locality")
        source = digest_item.get("source", "the latest dental digest")
        title = digest_item.get("title", "fluoride recall update")
        trial_n = digest_item.get("trial_n")
        percent = self._first_percent(digest_item.get("summary", "") or "")
        cohort = aggregate.get("high_risk_adult_count")
        offer = self._first_active_offer(merchant)

        trial_piece = f"{trial_n:,}-patient trial" if isinstance(trial_n, int) else "clinical trial"
        cohort_piece = f" You already have {cohort} high-risk adult patients" if cohort is not None else ""
        locality_piece = f" in {locality}" if locality else ""
        offer_piece = f"; your {offer} offer gives the recall message a simple next step" if offer else ""
        body = (
            f"Dr. {owner}, {source} landed with one item directly relevant to {business}{locality_piece}: "
            f"{trial_piece} showed 3-month fluoride recall cuts caries recurrence {percent} better than 6-month. "
            f"{cohort_piece}{offer_piece}. I already pulled the 2-min abstract and drafted a patient-ed WhatsApp you can share. "
            "Want me to paste the abstract + ready-to-send WhatsApp now so you can review it in 30 seconds?"
        )
        return self._send(
            body,
            "natural_question",
            "Uses the matched dental digest, exact trial metric, merchant cohort, and effort-externalized CTA.",
        )

    def _golden_festival_salon(self, merchant: dict, trigger: dict) -> dict:
        identity = merchant.get("identity", {}) or {}
        performance = merchant.get("performance", {}) or {}
        payload = trigger.get("payload", {}) or {}
        owner = identity.get("owner_first_name") or identity.get("name") or "there"
        business = identity.get("name") or "your salon"
        locality = identity.get("locality") or "your area"
        festival = payload.get("festival", "Diwali")
        date = payload.get("date")
        days = payload.get("days_until")
        calls = performance.get("calls")
        views = performance.get("views")
        offer = self._first_active_offer(merchant)
        review = self._top_review_theme(merchant, positive=True)
        active_offers = [
            offer.get("title")
            for offer in merchant.get("offers", []) or []
            if offer.get("status") == "active" and offer.get("title")
        ]

        offer_piece = f"; {' and '.join(active_offers[:2])} are already live" if active_offers else ""
        review_piece = ""
        if review:
            review_piece = f", and {review.get('occurrences_30d')} reviews already praise {review.get('theme')}"
        body = (
            f"{owner}, {festival} is {days} days out ({date}) — perfect timing for a no-discount pre-booking test for hair spa, haircut, and glow-up services in {locality}. "
            f"{business} already has {views} views and {calls} calls in 30d{review_piece}{offer_piece}. "
            "I already drafted one GBP post + one WhatsApp opt-in using your live offers; if 5 customers reply, open 10 festive slots, otherwise pause it. "
            "Reply YES to paste the exact copy and set up the pre-booking test in 10 minutes, or STOP."
        )
        return self._send(
            body,
            "natural_question",
            "Uses the festival trigger, salon performance, locality, active offer, and ready-made campaign CTA.",
        )

    def _golden_review_theme(self, merchant: dict, trigger: dict) -> dict:
        identity = merchant.get("identity", {}) or {}
        aggregate = merchant.get("customer_aggregate", {}) or {}
        payload = trigger.get("payload", {}) or {}
        owner = identity.get("owner_first_name") or identity.get("name") or "there"
        business = identity.get("name") or "your restaurant"
        locality = identity.get("locality") or "your locality"
        occurrences = payload.get("occurrences_30d")
        raw_theme = str(payload.get("theme", "review_theme"))
        theme = "late delivery" if raw_theme == "delivery_late" else raw_theme.replace("_", " ")
        quote = payload.get("common_quote")
        delivery_orders = aggregate.get("delivery_orders_30d")
        offer = self._first_active_offer(merchant)

        delivery_piece = f" against {delivery_orders} delivery orders in 30d" if delivery_orders is not None else ""
        offer_piece = f" Your {offer} offer is already live, so this can become a delivery-confidence push." if offer else ""
        quote_piece = f' One quote says "{quote}."' if quote else ""
        body = (
            f"{owner}, {occurrences} {locality} reviews now flag {theme}{delivery_piece}.{quote_piece} "
            f"That is exactly the kind of issue that can hurt repeat orders for {business}.{offer_piece} "
            "I already drafted the 2-line review reply plus a cleaner delivery ETA update. "
            "Want me to paste the reply + ETA update here?"
        )
        return self._send(
            body,
            "natural_question",
            "Uses the exact review-theme trigger, merchant delivery volume, active offer, and ready reply CTA.",
        )

    def _golden_gym_planning(self, merchant: dict, trigger: dict) -> dict:
        identity = merchant.get("identity", {}) or {}
        aggregate = merchant.get("customer_aggregate", {}) or {}
        payload = trigger.get("payload", {}) or {}
        owner = identity.get("owner_first_name") or identity.get("name") or "Coach"
        business = identity.get("name") or "your studio"
        locality = identity.get("locality") or "your area"
        active_members = aggregate.get("total_active_members")
        trial_to_paid = aggregate.get("trial_to_paid_pct")
        message = payload.get("merchant_last_message") or "kids yoga program"
        review = self._top_review_theme(merchant, positive=True)
        offer = self._first_active_offer(merchant)

        review_piece = ""
        if review:
            review_piece = f" plus {review.get('occurrences_30d')} positive {review.get('theme')} reviews"
        offer_piece = f" Your {offer} trial offer gives parents an easy first step." if offer else ""
        body = (
            f"{owner}, your kids yoga idea is strong for {business} in {locality}: {active_members} active members, "
            f"{trial_to_paid} trial-to-paid{review_piece}. "
            f"You asked: \"{message}\".{offer_piece} I already drafted the 4-week kids program outline, class post, and approval note. "
            "Reply YES to paste the kids program outline + post copy here, or STOP."
        )
        return self._send(
            body,
            "natural_question",
            "Uses the planning trigger, gym membership/conversion facts, review proof, and ready-made program CTA.",
        )

    def _golden_gbp_unverified(self, merchant: dict, trigger: dict) -> dict:
        identity = merchant.get("identity", {}) or {}
        performance = merchant.get("performance", {}) or {}
        aggregate = merchant.get("customer_aggregate", {}) or {}
        payload = trigger.get("payload", {}) or {}
        owner = identity.get("owner_first_name") or identity.get("name") or "there"
        business = identity.get("name") or "your pharmacy"
        locality = identity.get("locality") or "your locality"
        path = str(payload.get("verification_path", "postcard_or_phone_call")).replace("_", " ")
        uplift = payload.get("estimated_uplift_pct")
        chronic = aggregate.get("chronic_rx_count")
        repeat = aggregate.get("repeat_customer_pct")
        views = performance.get("views")
        calls = performance.get("calls")

        body = (
            f"{owner}, {business} in {locality} is still unverified on GBP, even with {views} views and {calls} calls in 30d. "
            f"The trigger estimates {uplift} uplift after {path}; for a pharmacy with {chronic} chronic-Rx customers and {repeat} repeat customers, trust is the conversion lever. "
            "I already prepared the GBP verification steps and a short approval note. "
            f"Reply YES to paste the 5-minute verification steps now so those {views} profile views stop leaking trust, or STOP."
        )
        return self._send(
            body,
            "natural_question",
            "Uses the GBP trigger, pharmacy trust context, performance metrics, chronic-Rx base, and ready verification CTA.",
        )

    def _golden_slot_flow(
        self, merchant: dict, trigger: dict, customer: dict | None
    ) -> dict | None:
        if not customer:
            return None
        slots = (trigger.get("payload", {}) or {}).get("available_slots", []) or []
        labels = [slot.get("label") for slot in slots if isinstance(slot, dict) and slot.get("label")]
        if len(labels) < 2:
            return None
        merchant_name = (merchant.get("identity", {}) or {}).get("name", "the clinic")
        customer_name = (customer.get("identity", {}) or {}).get("name", "there")
        body = (
            f"Hi {customer_name}, {merchant_name} here. Your recall is due, and I already found two clean slots: "
            f"{labels[0]} or {labels[1]}. Reply 1 for {labels[0]}, 2 for {labels[1]}, or send a better time."
        )
        return self._send(
            body,
            "slot_choice",
            "Uses customer recall context and exact available slots with a low-friction slot CTA.",
        )

    def _tactic_for(
        self,
        trigger_kind: str | None,
        category_slug: str | None,
        trigger_payload: dict,
        digest_item: dict | None,
    ) -> str:
        if trigger_kind == "research_digest" and digest_item:
            return (
                "Peer-clinical note: cite source/page/trial/summary, connect to merchant cohort or signal, "
                "offer to draft a patient education or recall message."
            )
        if trigger_kind == "festival_upcoming":
            return (
                "Seasonal planning note: cite festival/date/days_until, connect to active offers and locality demand, "
                "offer one promotional post or package."
            )
        if trigger_kind == "review_theme_emerged":
            return (
                "Operator alert: cite occurrence count/trend/quote, connect to current orders or offer, "
                "offer a concrete fix message or review reply."
            )
        if trigger_kind == "active_planning_intent":
            return (
                "Execution mode: acknowledge merchant's last message, turn intent into a concrete draft/package, "
                "ask for confirmation to proceed."
            )
        if trigger_kind == "gbp_unverified":
            return (
                "Trust/visibility note: cite unverified status and estimated uplift, connect to current views/calls, "
                "offer to start postcard or phone verification."
            )
        if trigger_payload.get("available_slots"):
            return "Booking flow: cite service/due date and offer the first two slots with Reply 1/2/STOP as final CTA."
        return f"{category_slug or 'merchant'}-specific operational note: cite the trigger, merchant stat, and one concrete next step."

    def _parse_action(self, raw_response: str) -> dict:
        text = (
            (raw_response or "")
            .replace("```json", "")
            .replace("```JSON", "")
            .replace("```", "")
            .strip()
        )
        if not text:
            raise ValueError("empty LLM response")

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", text)
            if not match:
                raise ValueError("LLM response did not contain a JSON object") from None
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError as exc:
                raise ValueError("LLM response JSON block was malformed") from exc

        if not isinstance(data, dict):
            raise ValueError("LLM response JSON was not an object")
        missing = self.REQUIRED_KEYS - set(data)
        if missing:
            raise ValueError(f"LLM response missing required keys: {sorted(missing)}")
        if data.get("action") != "send":
            raise ValueError('LLM response action must be "send"')
        for key in ("body", "cta", "rationale"):
            if not isinstance(data.get(key), str) or not data[key].strip():
                raise ValueError(f"LLM response {key} must be a non-empty string")

        return {
            "action": "send",
            "body": data["body"].strip(),
            "cta": data["cta"].strip(),
            "rationale": data["rationale"].strip(),
        }

    def _contextual_fallback_action(
        self, category: dict, merchant: dict, trigger: dict, customer: dict | None
    ) -> dict:
        merchant_identity = merchant.get("identity", {}) if merchant else {}
        merchant_name = merchant_identity.get("owner_first_name") or merchant_identity.get("name") or "there"
        category_slug = (category or {}).get("slug", "your category")
        trigger_kind = (trigger or {}).get("kind", "context_update")
        trigger_payload = (trigger or {}).get("payload", {}) or {}
        performance = (merchant or {}).get("performance", {}) or {}
        digest_item = self._find_digest_item(category or {}, trigger_payload)
        fact = self._best_fact(trigger_payload, performance, digest_item, merchant or {}, customer)
        merchant_stat = self._merchant_stat(performance)
        locality = merchant_identity.get("locality")
        active_offer = self._first_active_offer(merchant or {})

        if customer:
            customer_name = customer.get("identity", {}).get("name", "your customer")
            body = (
                f"{customer_name} has a {trigger_kind.replace('_', ' ')} update for {merchant_identity.get('name', 'your business')}: "
                f"{fact}. Reply YES to send this customer message now, or STOP to skip."
            )
            rationale = "Uses customer, trigger, and merchant context to create a concrete next-best action without fabricating missing facts."
        else:
            salutation = f"Dr. {merchant_name}" if category_slug == "dentists" and not str(merchant_name).startswith("Dr.") else str(merchant_name)
            local_piece = f" in {locality}" if locality else ""
            offer_piece = f"; your active offer is {active_offer}" if active_offer else ""
            body = (
                f"{salutation}, {fact}. For {merchant_identity.get('name', 'your business')}{local_piece}, "
                f"{merchant_stat}{offer_piece}. Reply YES and I will draft the exact next action, or STOP to skip."
            )
            rationale = "Uses trigger, category, and merchant context to propose a specific low-friction next action without fabricating missing facts."

        return {
            "action": "send",
            "body": body,
            "cta": "binary_yes_no",
            "rationale": rationale,
        }

    def _find_digest_item(self, category: dict, trigger_payload: dict) -> dict | None:
        target_ids = {
            trigger_payload.get("top_item_id"),
            trigger_payload.get("digest_item_id"),
            trigger_payload.get("alert_id"),
        }
        target_ids.discard(None)
        for item in category.get("digest", []) or []:
            if item.get("id") in target_ids:
                return item
        return None

    def _merchant_stat(self, performance: dict) -> str:
        if not performance:
            return "the latest merchant stats are available"
        parts = []
        for label, key in (("views", "views"), ("calls", "calls"), ("directions", "directions")):
            if performance.get(key) is not None:
                parts.append(f"{performance[key]} {label}")
        if performance.get("ctr") is not None:
            parts.append(f"{performance['ctr']} CTR")
        if parts:
            return "last 30 days show " + ", ".join(parts[:4])
        return "the latest merchant stats are available"

    def _first_active_offer(self, merchant: dict) -> str | None:
        for offer in merchant.get("offers", []) or []:
            if offer.get("status") == "active" and offer.get("title"):
                return offer["title"]
        return None

    def _best_fact(
        self,
        trigger_payload: dict,
        performance: dict,
        digest_item: dict | None,
        merchant: dict,
        customer: dict | None,
    ) -> str:
        if digest_item:
            source = digest_item.get("source")
            trial_n = digest_item.get("trial_n")
            title = digest_item.get("title")
            if trial_n and source and title:
                return f"{title} cites {trial_n} cases from {source}"
            if source and title:
                return f"{title} from {source}"
            if title:
                return title

        for key in (
            "delta_pct",
            "days_remaining",
            "renewal_amount",
            "days_since_expiry",
            "urgency",
            "due_date",
            "deadline_iso",
            "match_time_iso",
            "distance_km",
            "estimated_uplift_pct",
        ):
            if key in trigger_payload and trigger_payload[key] is not None:
                return f"{key.replace('_', ' ')} is {trigger_payload[key]}"

        if trigger_payload.get("available_slots"):
            labels = [slot.get("label") for slot in trigger_payload["available_slots"] if slot.get("label")]
            if labels:
                return f"available slots are {', '.join(labels[:2])}"

        if performance:
            views = performance.get("views")
            calls = performance.get("calls")
            ctr = performance.get("ctr")
            if views is not None and calls is not None and ctr is not None:
                return f"your last window shows {views} views, {calls} calls, and {ctr} CTR"

        active_offers = [
            offer.get("title")
            for offer in merchant.get("offers", []) or []
            if offer.get("status") == "active" and offer.get("title")
        ]
        if active_offers:
            return f"your active offer is {active_offers[0]}"

        if customer:
            relationship = customer.get("relationship", {}) or {}
            visits = relationship.get("visits_total")
            last_visit = relationship.get("last_visit")
            if visits is not None and last_visit:
                return f"customer history shows {visits} visits, last on {last_visit}"

        return "there is a fresh context update available in the pushed JSON"

    async def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        if self.provider in {"gemini", "google", "google-genai"}:
            return await self._call_gemini(system_prompt, user_prompt)
        if self.provider in {"openai", "openrouter"}:
            return await self._call_openai(system_prompt, user_prompt)
        raise RuntimeError(
            f"Unsupported LLM_PROVIDER={self.provider!r}. Use 'gemini', 'openai', or 'openrouter'."
        )

    async def _call_gemini(self, system_prompt: str, user_prompt: str) -> str:
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("Missing GEMINI_API_KEY or GOOGLE_API_KEY for Gemini composer")
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError(
                "google-genai SDK is not installed. Install with: pip install google-genai"
            ) from exc

        client = genai.Client(api_key=api_key)
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0,
            max_output_tokens=512,
            response_mime_type="application/json",
        )

        async_client = getattr(client, "aio", None)
        if async_client is not None:
            response = await async_client.models.generate_content(
                model=self.model,
                contents=user_prompt,
                config=config,
            )
        else:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=self.model,
                contents=user_prompt,
                config=config,
            )
        return self._extract_text(response)

    async def _call_openai(self, system_prompt: str, user_prompt: str) -> str:
        if self.provider == "openrouter":
            api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
            base_url = "https://openrouter.ai/api/v1"
            missing_key_message = "Missing OPENROUTER_API_KEY or OPENAI_API_KEY for OpenRouter composer"
        else:
            api_key = os.getenv("OPENAI_API_KEY")
            base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
            missing_key_message = "Missing OPENAI_API_KEY for OpenAI composer"
        if not api_key:
            raise RuntimeError(missing_key_message)
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError(
                "openai SDK is not installed. Install with: pip install openai"
            ) from exc

        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        if self.provider != "openrouter" and hasattr(client, "responses"):
            response = await client.responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0,
                max_output_tokens=512,
                text={"format": {"type": "json_object"}},
            )
            return self._extract_text(response)

        response = await client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
            max_tokens=512,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or ""

    def _extract_text(self, response: Any) -> str:
        text = getattr(response, "text", None)
        if isinstance(text, str) and text:
            return text

        output_text = getattr(response, "output_text", None)
        if isinstance(output_text, str) and output_text:
            return output_text

        candidates = getattr(response, "candidates", None)
        if candidates:
            parts = []
            for candidate in candidates:
                content = getattr(candidate, "content", None)
                for part in getattr(content, "parts", []) or []:
                    part_text = getattr(part, "text", None)
                    if part_text:
                        parts.append(part_text)
            if parts:
                return "".join(parts)

        output = getattr(response, "output", None)
        if output:
            parts = []
            for item in output:
                for content in getattr(item, "content", []) or []:
                    part_text = getattr(content, "text", None)
                    if part_text:
                        parts.append(part_text)
            if parts:
                return "".join(parts)

        return str(response)

    def _default_model(self, provider: str) -> str:
        if provider in {"gemini", "google", "google-genai"}:
            return "gemini-2.5-flash"
        if provider == "openrouter":
            return "deepseek/deepseek-chat-v3"
        if provider == "openai":
            if "openrouter.ai" in os.getenv("OPENAI_BASE_URL", ""):
                return "deepseek/deepseek-chat-v3"
            return "gpt-4o"
        return "gemini-2.5-flash"

    def _template_action(
        self, category: dict, merchant: dict, trigger: dict, customer: dict | None
    ) -> dict | None:
        kind = trigger.get("kind")
        kind_text = str(kind or "")
        trigger_id_text = str(trigger.get("id") or trigger.get("trigger_id") or "")
        suppression_text = str(trigger.get("suppression_key") or "")
        payload = trigger.get("payload", {}) or {}
        intent_text = str(payload.get("intent") or payload.get("intent_topic") or "")
        metric_topic = str(payload.get("metric_or_topic") or "")

        if customer:
            if kind == "recall_due":
                return self._template_recall_due(merchant, trigger, customer)
            if kind in {"customer_lapsed_soft", "customer_lapsed_hard"}:
                return self._template_customer_lapsed(merchant, trigger, customer)
            if kind == "appointment_tomorrow":
                return self._template_appointment_tomorrow(merchant, trigger, customer)
            if kind == "trial_followup":
                return self._template_trial_followup(merchant, trigger, customer)
            if kind == "chronic_refill_due":
                return self._template_chronic_refill(merchant, trigger, customer)
            if kind == "wedding_package_followup":
                return self._template_wedding_followup(merchant, trigger, customer)
            if payload.get("available_slots"):
                return self._golden_slot_flow(merchant, trigger, customer)
            return None

        if kind == "research_digest" or metric_topic == "research_digest":
            return self._template_research_digest(category, merchant, trigger)
        if (
            kind == "corporate_thali_planning"
            or "corp_thali" in trigger_id_text
            or "corp_thali" in suppression_text
            or "thali" in intent_text
            or intent_text == "corporate_bulk_thali_package"
        ):
            return self._template_corporate_thali_planning(merchant, trigger)
        if (
            kind_text.startswith("festival_upcoming")
            or metric_topic == "festival_upcoming"
            or "festival" in trigger_id_text
            or "festival" in suppression_text
        ):
            return self._template_festival_upcoming_generic(category, merchant, trigger)
        if kind == "competitor_opened":
            return self._template_competitor_opened(merchant, trigger)
        if kind == "curious_ask_due":
            return self._template_curious_ask(merchant, trigger)
        if kind == "dormant_with_vera":
            return self._template_dormant(merchant, trigger)
        if kind == "ipl_match_today":
            return self._template_ipl_match(merchant, trigger)
        if kind == "milestone_reached":
            return self._template_milestone(merchant, trigger)
        if kind in {"perf_dip", "perf_spike"}:
            return self._template_perf_change(merchant, trigger)
        if kind == "regulation_change":
            return self._template_regulation_change(category, merchant, trigger)
        if kind == "supply_alert":
            return self._template_supply_alert(merchant, trigger)
        if kind == "category_seasonal":
            return self._template_category_seasonal(merchant, trigger)
        if kind == "renewal_due":
            return self._template_renewal_due(merchant, trigger)
        if kind == "cde_opportunity":
            return self._template_cde_opportunity(category, merchant, trigger)
        if kind == "winback_eligible":
            return self._template_winback(merchant, trigger)
        if kind == "seasonal_perf_dip":
            return self._template_seasonal_perf_dip(merchant, trigger)
        return None

    def _template_recall_due(self, merchant: dict, trigger: dict, customer: dict) -> dict:
        payload = trigger.get("payload", {}) or {}
        merchant_name = self._merchant_name(merchant)
        customer_name = self._customer_name(customer)
        relationship = customer.get("relationship", {}) or {}
        services = relationship.get("services_received") or []
        service_due = str(
            payload.get("service_due")
            or (services[-1] if services else None)
            or "follow-up"
        ).replace("_", " ")
        due_date = payload.get("due_date") or relationship.get("last_visit") or "the active recall list"
        slots = self._slot_labels(payload.get("available_slots", []))
        greeting = "Namaste" if self._prefers_hindi_mix(customer) else "Hi"
        slot_text = (
            f"Apke liye 2 slots ready hain: {slots[0]} ya {slots[1]}."
            if self._prefers_hindi_mix(customer) and len(slots) >= 2
            else f"I found two clean slots: {slots[0]} or {slots[1]}."
            if len(slots) >= 2
            else "I can hold the next available slot for you."
        )
        cta = (
            f"Reply 1 for {slots[0]}, 2 for {slots[1]}, or STOP."
            if len(slots) >= 2
            else "Reply YES to hold a slot, or STOP."
        )
        body = (
            f"{greeting} {customer_name}, {merchant_name} here. Your {service_due} is due on {due_date}. "
            f"{slot_text} {cta}"
        )
        return self._send_route(body, "multi_choice_slot" if len(slots) >= 2 else "binary_yes_stop", "merchant_on_behalf", "Customer recall template using service due date and available slots.")

    def _template_customer_lapsed(self, merchant: dict, trigger: dict, customer: dict) -> dict:
        payload = trigger.get("payload", {}) or {}
        merchant_name = self._merchant_name(merchant)
        customer_name = self._customer_name(customer)
        relationship = customer.get("relationship", {}) or {}
        days = payload.get("days_since_last_visit")
        services = relationship.get("services_received") or []
        focus = str(
            payload.get("previous_focus")
            or (services[-1] if services else None)
            or (customer.get("preferences", {}) or {}).get("training_focus")
            or "your routine"
        ).replace("_", " ")
        months = payload.get("previous_membership_months")
        visits = relationship.get("visits_total")
        last_visit = relationship.get("last_visit")
        greeting = "Namaste" if self._prefers_hindi_mix(customer) else "Hi"
        if days is not None:
            timing = f"It's been {days} days since your last visit"
        elif last_visit:
            timing = f"Your last visit was on {last_visit}"
        else:
            timing = "Your follow-up reminder is active today"
        history = f" after {months} membership months" if months is not None else ""
        visit_text = f" across {visits} visits" if visits is not None else ""
        body = (
            f"{greeting} {customer_name}, {merchant_name} here. {timing}{history}{visit_text}; no pressure, this happens. "
            f"I kept a no-commitment spot aligned to {focus}. Reply YES to hold the spot, or STOP."
        )
        return self._send_route(body, "binary_yes_stop", "merchant_on_behalf", "Customer lapse winback template using days since last visit and prior focus.")

    def _template_appointment_tomorrow(self, merchant: dict, trigger: dict, customer: dict) -> dict:
        payload = trigger.get("payload", {}) or {}
        merchant_name = self._merchant_name(merchant)
        customer_name = self._customer_name(customer)
        slots = self._slot_labels(payload.get("next_session_options") or payload.get("available_slots") or [])
        appointment_time = payload.get("appointment_time") or payload.get("appointment_at") or (slots[0] if slots else "tomorrow")
        greeting = "Namaste" if self._prefers_hindi_mix(customer) else "Hi"
        body = (
            f"{greeting} {customer_name}, {merchant_name} reminder: your appointment is scheduled for {appointment_time}. "
            "Reply CONFIRM to keep it, or CANCEL if you need to reschedule."
        )
        return self._send_route(body, "binary_confirm_cancel", "merchant_on_behalf", "Customer appointment reminder template with confirm/cancel CTA.")

    def _template_trial_followup(self, merchant: dict, trigger: dict, customer: dict) -> dict:
        payload = trigger.get("payload", {}) or {}
        merchant_name = self._merchant_name(merchant)
        customer_name = self._customer_name(customer)
        trial_date = payload.get("trial_date") or "your trial"
        slots = self._slot_labels(payload.get("next_session_options", []))
        greeting = "Namaste" if self._prefers_hindi_mix(customer) else "Hi"
        if slots:
            cta = f"Reply YES to hold {slots[0]}, or STOP."
            slot_text = f"I can hold the next session option: {slots[0]}."
        else:
            cta = "Reply YES to hold the next session, or STOP."
            slot_text = "I can hold the next session option for you."
        body = (
            f"{greeting} {customer_name}, {merchant_name} here. You tried the session on {trial_date}; {slot_text} {cta}"
        )
        return self._send_route(body, "binary_yes_stop", "merchant_on_behalf", "Customer trial follow-up template using trial date and next session option.")

    def _template_chronic_refill(self, merchant: dict, trigger: dict, customer: dict) -> dict:
        payload = trigger.get("payload", {}) or {}
        merchant_name = self._merchant_name(merchant)
        customer_name = self._customer_name(customer)
        molecules = payload.get("molecule_list") or []
        if not molecules and payload.get("molecule"):
            molecules = [payload.get("molecule")]
        relationship = customer.get("relationship", {}) or {}
        chronic_conditions = relationship.get("chronic_conditions") or []
        molecule_text = (
            ", ".join(str(molecule) for molecule in molecules)
            if molecules
            else f"your {', '.join(str(item) for item in chronic_conditions)} refill"
            if chronic_conditions
            else "your regular refill"
        )
        runout = payload.get("stock_runs_out_iso") or payload.get("last_refill")
        delivery = " Delivery address is saved." if payload.get("delivery_address_saved") else ""
        greeting = "Namaste" if self._prefers_hindi_mix(customer) else "Hi"
        if runout:
            body = (
                f"{greeting} {customer_name}, {merchant_name} here. Your refill for {molecule_text} runs out on {runout}.{delivery} "
                "Reply CONFIRM to dispatch, or STOP."
            )
        elif merchant.get("category_slug") == "pharmacies":
            body = (
                f"{greeting} {customer_name}, {merchant_name} here. It's time for your regular refill reminder.{delivery} "
                "Reply CONFIRM to dispatch, or STOP."
            )
        else:
            body = (
                f"{greeting} {customer_name}, {merchant_name} here. Your regular follow-up reminder is active today. "
                "Reply CONFIRM if you want us to arrange it, or STOP."
            )
        return self._send_route(body, "binary_confirm_stop", "merchant_on_behalf", "Customer chronic refill template using medicine list and run-out date.")

    def _template_research_digest(self, category: dict, merchant: dict, trigger: dict) -> dict:
        payload = trigger.get("payload", {}) or {}
        owner = self._owner_name(merchant)
        business = self._merchant_name(merchant)
        identity = merchant.get("identity", {}) or {}
        locality = identity.get("locality") or identity.get("city") or "your locality"
        category_slug = category.get("display_name") or category.get("slug") or merchant.get("category_slug") or "category"
        digest_item = self._find_digest_item(category, payload)
        if not digest_item:
            digest = category.get("digest") or []
            digest_item = digest[0] if digest else {}
        title = digest_item.get("title") or f"latest {str(category_slug).replace('_', ' ')} research digest"
        source = digest_item.get("source") or "the latest category digest"
        trial_n = digest_item.get("trial_n")
        summary = digest_item.get("summary") or digest_item.get("actionable") or ""
        trial_text = f" ({trial_n:,} sample)" if isinstance(trial_n, int) else ""
        summary_text = f" Key point: {summary}" if summary else ""
        stat = self._merchant_context_anchor(merchant)
        body = (
            f"{owner}, {source} just dropped: {title}{trial_text}. "
            f"For {business} in {locality}, {stat}.{summary_text} "
            "Want me to pull a 2-min abstract and draft the customer WhatsApp from it?"
        )
        return self._send_route(body, "natural_question", "vera", "Merchant research digest template using category digest and merchant context.")

    def _template_festival_upcoming_generic(
        self, category: dict, merchant: dict, trigger: dict
    ) -> dict:
        payload = trigger.get("payload", {}) or {}
        owner = self._owner_name(merchant)
        business = self._merchant_name(merchant)
        identity = merchant.get("identity", {}) or {}
        locality = identity.get("locality") or identity.get("city") or "your locality"
        category_name = (
            category.get("display_name")
            or category.get("slug")
            or merchant.get("category_slug")
            or "category"
        )
        festival = (
            payload.get("festival_name")
            or payload.get("festival")
            or payload.get("name")
            or "the upcoming seasonal window"
        )
        active_offer = self._first_active_offer(merchant)
        offer_text = f" around {active_offer}" if active_offer else ""
        stat = self._merchant_context_anchor(merchant)
        body = (
            f"{owner}, {festival} is active for {business} in {locality}. "
            f"Your {str(category_name).replace('_', ' ')} context shows {stat}, so a seasonal promo draft{offer_text} can capture intent before nearby competitors do. "
            "Reply YES and I will draft the seasonal promo message now, or STOP."
        )
        return self._send_route(
            body,
            "binary_yes_stop",
            "vera",
            "Merchant festival template using seasonal trigger, merchant locality, and available offer context.",
        )

    def _template_corporate_thali_planning(self, merchant: dict, trigger: dict) -> dict:
        payload = trigger.get("payload", {}) or {}
        owner = self._owner_name(merchant)
        identity = merchant.get("identity", {}) or {}
        locality = identity.get("locality") or identity.get("city") or "your locality"
        last_message = payload.get("merchant_last_message")
        last_message_text = (
            f" You said '{last_message}', so this is ready to turn into copy."
            if last_message
            else ""
        )
        body = (
            f"{owner}, I see a spike in corporate thali planning searches near {locality}. "
            f"Let's push a bulk-order discount to capture this office crowd.{last_message_text} "
            "Want me to draft the Swiggy/Zomato banner text?"
        )
        return self._send_route(
            body,
            "open_ended",
            "vera",
            "Merchant corporate thali planning template using locality and bulk-order intent.",
        )

    def _template_wedding_followup(self, merchant: dict, trigger: dict, customer: dict) -> dict:
        payload = trigger.get("payload", {}) or {}
        merchant_name = self._merchant_name(merchant)
        customer_name = self._customer_name(customer)
        wedding_date = payload.get("wedding_date") or (customer.get("preferences", {}) or {}).get("wedding_date") or "your wedding date"
        days = payload.get("days_to_wedding")
        window = str(payload.get("next_step_window_open") or "bridal prep window").replace("_", " ")
        trial = payload.get("trial_completed")
        greeting = "Namaste" if self._prefers_hindi_mix(customer) else "Hi"
        days_text = f"{days} days to your wedding" if days is not None else f"Your wedding date is {wedding_date}"
        trial_text = f" after your trial on {trial}" if trial else ""
        body = (
            f"{greeting} {customer_name}, {merchant_name} here. {days_text} ({wedding_date}){trial_text}; this is the right window for {window}. "
            "Reply YES to hold the first bridal-prep slot, or STOP."
        )
        return self._send_route(body, "binary_yes_stop", "merchant_on_behalf", "Customer bridal follow-up template using wedding date and prep window.")

    def _template_competitor_opened(self, merchant: dict, trigger: dict) -> dict:
        payload = trigger.get("payload", {}) or {}
        owner = self._owner_name(merchant)
        business = self._merchant_name(merchant)
        locality = (merchant.get("identity", {}) or {}).get("locality", "your locality")
        competitor = payload.get("competitor_name") or "a nearby competitor"
        distance = payload.get("distance_km")
        their_offer = payload.get("their_offer") or "a visible offer"
        opened = payload.get("opened_date")
        distance_text = f"{distance}km away" if distance is not None else f"near {locality}"
        opened_text = f" on {opened}" if opened else ""
        body = (
            f"{owner}, {competitor} opened {distance_text}{opened_text} with {their_offer}. "
            f"For {business} in {locality}, I can draft a cleaner counter-offer using your existing positioning. Reply YES to draft the counter-offer, or STOP."
        )
        return self._send_route(body, "binary_yes_stop", "vera", "Merchant competitor template using competitor distance and offer.")

    def _template_curious_ask(self, merchant: dict, trigger: dict) -> dict:
        payload = trigger.get("payload", {}) or {}
        owner = self._owner_name(merchant)
        business = self._merchant_name(merchant)
        ask = str(payload.get("ask_template") or "top service this week").replace("_", " ")
        body = (
            f"{owner}, quick operator question for {business}: what was your top service or product this week? "
            f"I will turn your answer into one GBP post plus a 4-line WhatsApp reply for {ask}. Reply with the service name, or STOP."
        )
        return self._send_route(body, "open_ended", "vera", "Merchant curious-ask template designed to elicit an easy reply.")

    def _template_dormant(self, merchant: dict, trigger: dict) -> dict:
        payload = trigger.get("payload", {}) or {}
        owner = self._owner_name(merchant)
        business = self._merchant_name(merchant)
        identity = merchant.get("identity", {}) or {}
        locality = identity.get("locality") or identity.get("city") or "your market"
        days = payload.get("days_since_last_merchant_message")
        last_topic = str(payload.get("last_topic") or "growth").replace("_", " ")
        days_text = (
            f"It's been {days} days since we chatted"
            if days is not None
            else "Your latest Vera context is ready today"
        )
        anchor = self._merchant_context_anchor(merchant)
        body = (
            f"{owner}, {days_text} about {last_topic}. "
            f"For {business} in {locality}, current context shows {anchor}. Reply YES to see the quick win draft, or STOP."
        )
        return self._send_route(body, "binary_yes_stop", "vera", "Merchant dormancy template using days since last merchant message.")

    def _template_ipl_match(self, merchant: dict, trigger: dict) -> dict:
        payload = trigger.get("payload", {}) or {}
        owner = self._owner_name(merchant)
        match = payload.get("match") or "today's match"
        venue = payload.get("venue") or "the local stadium"
        city = payload.get("city") or (merchant.get("identity", {}) or {}).get("city", "your city")
        match_time = payload.get("match_time_iso") or "today"
        offer = self._first_active_offer(merchant)
        offer_text = f" Your {offer} offer can work better as a delivery push." if offer else ""
        body = (
            f"{owner}, {match} at {venue}, {city} is scheduled for {match_time}. "
            f"Adjust dine-in promos around match timing and push delivery instead.{offer_text} Reply YES to draft the match-night delivery post, or STOP."
        )
        return self._send_route(body, "binary_yes_stop", "vera", "Merchant IPL template using match, venue, city, and promo adjustment.")

    def _template_milestone(self, merchant: dict, trigger: dict) -> dict:
        payload = trigger.get("payload", {}) or {}
        owner = self._owner_name(merchant)
        business = self._merchant_name(merchant)
        identity = merchant.get("identity", {}) or {}
        locality = identity.get("locality") or identity.get("city") or "your market"
        metric = str(payload.get("metric") or "local visibility milestone").replace("_", " ")
        value = payload.get("value_now")
        milestone = payload.get("milestone_value")
        if value is not None:
            value_text = f"{value} on {metric}"
        elif payload.get("placeholder") or payload.get("metric_or_topic"):
            value_text = f"{self._merchant_context_anchor(merchant)}"
        else:
            value_text = f"{metric}: {self._merchant_context_anchor(merchant)}"
        milestone_text = f" and you are close to {milestone}" if milestone is not None else ""
        body = (
            f"{owner}, {business} just crossed a useful milestone in {locality}: {value_text}{milestone_text}. "
            "That is a clean customer-thank-you moment. Reply YES to draft the thank-you post, or STOP."
        )
        return self._send_route(body, "binary_yes_stop", "vera", "Merchant milestone template using metric and current value.")

    def _template_perf_change(self, merchant: dict, trigger: dict) -> dict:
        payload = trigger.get("payload", {}) or {}
        owner = self._owner_name(merchant)
        business = self._merchant_name(merchant)
        identity = merchant.get("identity", {}) or {}
        locality = identity.get("locality") or identity.get("city") or "your locality"
        kind = trigger.get("kind")
        metric = str(payload.get("metric") or self._best_performance_metric_name(merchant)).replace("_", " ")
        delta = self._format_pct(payload.get("delta_pct")) if payload.get("delta_pct") is not None else None
        window = payload.get("window") or "latest window"
        baseline = payload.get("vs_baseline")
        driver = payload.get("likely_driver")
        direction = "down" if kind == "perf_dip" else "up"
        baseline_text = f" vs baseline {baseline}" if baseline is not None else ""
        driver_text = f"; likely driver: {driver}" if driver else ""
        anchor = self._merchant_context_anchor(merchant)
        action = "protect calls before the dip compounds" if kind == "perf_dip" else "capitalize while attention is high"
        if delta:
            opening = f"{business} in {locality} has a {direction} signal on {metric}: {delta} over {window}{baseline_text}{driver_text}"
        elif kind == "perf_dip":
            opening = f"{business} in {locality} needs attention on {metric} in the latest performance window"
        else:
            opening = f"{business} in {locality} is picking up on {metric} in the latest performance window"
        body = (
            f"{owner}, {opening}; current context shows {anchor}. "
            f"I can draft a quick post/offer adjustment to {action}. Reply YES to draft the adjustment, or STOP."
        )
        return self._send_route(body, "binary_yes_stop", "vera", "Merchant performance-change template using metric, delta, and window.")

    def _template_regulation_change(self, category: dict, merchant: dict, trigger: dict) -> dict:
        payload = trigger.get("payload", {}) or {}
        digest_item = self._find_digest_item(category, payload) or {}
        owner = self._owner_name(merchant)
        item_id = payload.get("top_item_id") or payload.get("digest_item_id") or "regulation update"
        deadline = payload.get("deadline_iso")
        title = digest_item.get("title") or str(item_id).replace("_", " ")
        source = digest_item.get("source")
        source_text = f" from {source}" if source else ""
        deadline_text = f" before {deadline}" if deadline else ""
        body = (
            f"{owner}, high-priority compliance update: {title}{source_text}{deadline_text}. "
            "I can draft a short internal checklist and affected-customer note. Reply YES to draft it, or STOP."
        )
        return self._send_route(body, "binary_yes_stop", "vera", "Merchant regulation template using digest id/source and deadline.")

    def _template_supply_alert(self, merchant: dict, trigger: dict) -> dict:
        payload = trigger.get("payload", {}) or {}
        owner = self._owner_name(merchant)
        molecule = payload.get("molecule") or "medicine"
        batches = payload.get("affected_batches") or []
        batch_text = ", ".join(str(batch) for batch in batches) if batches else "affected batches"
        manufacturer = payload.get("manufacturer") or "manufacturer"
        body = (
            f"{owner}, urgent supply alert: {manufacturer} flagged {molecule} batches {batch_text}. "
            "I can draft the customer notification plus replacement-pickup workflow. Reply YES to draft it, or STOP."
        )
        return self._send_route(body, "binary_yes_stop", "vera", "Merchant supply-alert template using molecule and affected batches.")

    def _template_category_seasonal(self, merchant: dict, trigger: dict) -> dict:
        payload = trigger.get("payload", {}) or {}
        owner = self._owner_name(merchant)
        season = payload.get("season") or "seasonal demand"
        trends = payload.get("trends") or []
        trends_text = ", ".join(str(trend) for trend in trends[:4]) if trends else "category demand shift"
        action = " Shelf action is recommended." if payload.get("shelf_action_recommended") else ""
        body = (
            f"{owner}, {season} trend is active: {trends_text}.{action} "
            "I can draft the shelf-priority list plus customer WhatsApp. Reply YES to draft it, or STOP."
        )
        return self._send_route(body, "binary_yes_stop", "vera", "Merchant seasonal category template using season and trend list.")

    def _template_renewal_due(self, merchant: dict, trigger: dict) -> dict:
        payload = trigger.get("payload", {}) or {}
        owner = self._owner_name(merchant)
        plan = payload.get("plan") or (merchant.get("subscription", {}) or {}).get("plan") or "plan"
        days = payload.get("days_remaining")
        amount = payload.get("renewal_amount")
        days_text = f"{days} days remaining" if days is not None else "renewal window is open"
        amount_text = f" at ₹{amount}" if amount is not None else ""
        body = (
            f"{owner}, your {plan} renewal has {days_text}{amount_text}. "
            "I can prepare a 2-line renewal summary with the exact benefits to review. Reply YES to draft it, or STOP."
        )
        return self._send_route(body, "binary_yes_stop", "vera", "Merchant renewal template using days remaining and renewal amount.")

    def _template_cde_opportunity(self, category: dict, merchant: dict, trigger: dict) -> dict:
        payload = trigger.get("payload", {}) or {}
        digest_item = self._find_digest_item(category, payload) or {}
        owner = self._owner_name(merchant)
        item_id = payload.get("digest_item_id") or "CDE opportunity"
        title = digest_item.get("title") or str(item_id).replace("_", " ")
        credits = payload.get("credits")
        fee = payload.get("fee")
        credits_text = f" for {credits} credits" if credits is not None else ""
        fee_text = f"; fee: {fee}" if fee else ""
        body = (
            f"Dr. {owner}, {title}{credits_text}{fee_text}. "
            "I can draft the registration reminder and patient-friendly post angle. Reply YES to draft it, or STOP."
        )
        return self._send_route(body, "binary_yes_stop", "vera", "Merchant CDE opportunity template using digest item, credits, and fee.")

    def _template_winback(self, merchant: dict, trigger: dict) -> dict:
        payload = trigger.get("payload", {}) or {}
        owner = self._owner_name(merchant)
        days = payload.get("days_since_expiry")
        dip = self._format_pct(payload.get("perf_dip_pct")) if payload.get("perf_dip_pct") is not None else "post-expiry dip"
        lapsed = payload.get("lapsed_customers_added_since_expiry")
        days_text = f"{days} days since expiry" if days is not None else "after expiry"
        lapsed_text = f" and {lapsed} lapsed customers added" if lapsed is not None else ""
        body = (
            f"{owner}, {days_text}: performance is at {dip}{lapsed_text}. "
            "I can draft a low-risk winback offer using your existing customer base. Reply YES to draft it, or STOP."
        )
        return self._send_route(body, "binary_yes_stop", "vera", "Merchant winback template using expiry, dip, and lapsed customer facts.")

    def _template_seasonal_perf_dip(self, merchant: dict, trigger: dict) -> dict:
        payload = trigger.get("payload", {}) or {}
        owner = self._owner_name(merchant)
        metric = str(payload.get("metric") or "performance").replace("_", " ")
        delta = self._format_pct(payload.get("delta_pct")) if payload.get("delta_pct") is not None else "changed"
        window = payload.get("window") or "latest window"
        season_note = str(payload.get("season_note") or "seasonal pattern").replace("_", " ")
        body = (
            f"{owner}, {metric} is down {delta} over {window}, and this matches {season_note}. "
            "I can draft a retention-first plan instead of wasting acquisition spend. Reply YES to draft it, or STOP."
        )
        return self._send_route(body, "binary_yes_stop", "vera", "Merchant seasonal performance dip template using delta, window, and season note.")

    def _deterministic_action(
        self, category: dict, merchant: dict, trigger: dict, customer: dict | None
    ) -> dict | None:
        if not trigger:
            return None

        kind = trigger.get("kind")
        templated = self._template_action(category, merchant, trigger, customer)
        if templated:
            return templated

        payload = trigger.get("payload", {}) or {}
        identity = merchant.get("identity", {}) or {}
        performance = merchant.get("performance", {}) or {}
        aggregate = merchant.get("customer_aggregate", {}) or {}
        owner = identity.get("owner_first_name") or identity.get("name") or "there"
        business = identity.get("name") or "your business"
        locality = identity.get("locality") or "your locality"
        active_offer = self._first_active_offer(merchant)
        stat = self._merchant_stat(performance)

        if kind == "research_digest":
            digest_item = self._find_digest_item(category, payload)
            if not digest_item:
                return None
            source = digest_item.get("source")
            summary = digest_item.get("summary", "")
            trial_n = digest_item.get("trial_n")
            recurrence = self._first_percent(summary)
            cohort = aggregate.get("high_risk_adult_count")
            first = f"{source} reports {recurrence} lower caries recurrence with 3-month vs 6-month fluoride varnish recall"
            if trial_n:
                first += f" in {trial_n:,} high-risk adults"
            first += "."
            merchant_piece = (
                f" Dr. {owner}, {business} in {locality} has {cohort} high-risk adult patients, "
                "so this is a precise recall-cohort play, not a generic post."
                if cohort
                else f" Dr. {owner}, {business} in {locality} can use this as a peer-clinical recall nudge."
            )
            body = (
                f"{first}{merchant_piece} "
                "Reply YES to draft the patient recall message now, or STOP."
            )
            return self._send(body, "binary_yes_stop", "Used the matched dental digest source, exact clinical metric, and merchant cohort count.")

        if kind == "festival_upcoming":
            festival = payload.get("festival", "festival")
            date = payload.get("date")
            days_until = payload.get("days_until")
            offer_piece = f" with {active_offer}" if active_offer else ""
            calls = performance.get("calls")
            views = performance.get("views")
            review = self._top_review_theme(merchant, positive=True)
            review_piece = ""
            if review:
                review_piece = f" and {review.get('occurrences_30d')} recent reviews praised {review.get('theme')}"
            body = (
                f"{owner}, {festival} is on {date}, {days_until} days away, and {business} can start pre-festival salon bookings{offer_piece}. "
                f"In {locality}, your last 30 days show {views} views and {calls} calls{review_piece}, so abhi a warm Hair Spa/beauty package post is low-friction. "
                "Reply YES to draft the festival GBP post, or STOP."
            )
            return self._send(body, "binary_yes_stop", "Used the festival date, merchant performance, active offer, and salon review theme.")

        if kind == "review_theme_emerged":
            theme = payload.get("theme", "review theme")
            occurrences = payload.get("occurrences_30d")
            trend = payload.get("trend")
            quote = payload.get("common_quote")
            delivery_orders = aggregate.get("delivery_orders_30d")
            offer_piece = f" and {active_offer} is live" if active_offer else ""
            body = (
                f"{owner}, {occurrences} {locality} reviews now mention {theme}, trend {trend}, with the quote '{quote}'. "
                f"{business} has {delivery_orders} delivery orders in 30 days{offer_piece}, so late ETAs can directly hit repeat orders. "
                "Reply YES to draft the review reply plus delivery ETA update, or STOP."
            )
            return self._send(body, "binary_yes_stop", "Used the exact review-theme trigger, merchant delivery volume, and active restaurant offer.")

        if kind == "active_planning_intent":
            topic = str(payload.get("intent_topic", "planning")).replace("_", " ")
            last_message = payload.get("merchant_last_message")
            active_members = aggregate.get("total_active_members")
            trial_to_paid = aggregate.get("trial_to_paid_pct")
            reviews = merchant.get("review_themes", []) or []
            review_bits = [
                f"{r.get('theme')} {r.get('occurrences_30d')}x"
                for r in reviews
                if r.get("sentiment") == "pos" and r.get("occurrences_30d") is not None
            ]
            trial_pct = self._format_pct(trial_to_paid)
            body = (
                f"{owner}, you asked '{last_message}' and {topic} is ready to turn into an execution draft. "
                f"{business} in {locality} has {active_members} active members, {trial_pct} trial-to-paid, and reviews cite {', '.join(review_bits[:2])}, so a 4-week kids program fits your boutique segment. "
                "Reply YES to proceed with the GBP post and class-outline draft, or STOP."
            )
            return self._send(body, "binary_yes_stop", "Used the merchant's explicit planning intent plus gym membership, conversion, and review signals.")

        if kind == "gbp_unverified":
            verified = payload.get("verified")
            path = str(payload.get("verification_path", "verification")).replace("_", " ")
            uplift = self._format_pct(payload.get("estimated_uplift_pct"))
            chronic = aggregate.get("chronic_rx_count")
            repeat = self._format_pct(aggregate.get("repeat_customer_pct"))
            body = (
                f"{owner}, {business} in {locality} is verified={verified} on GBP; the trigger estimates {uplift} uplift after {path}. "
                f"Your pharmacy already has {performance.get('views')} views, {performance.get('calls')} calls, {chronic} chronic-Rx customers, and {repeat} repeat customers, so trust verification is the cleanest next step. "
                "Reply YES to start GBP verification now, or STOP."
            )
            return self._send(body, "binary_yes_stop", "Used GBP verification status, estimated uplift, pharmacy performance, and repeat-Rx customer facts.")

        return None

    def _send(self, body: str, cta: str, rationale: str) -> dict:
        return {
            "action": "send",
            "body": body,
            "cta": cta,
            "rationale": rationale,
        }

    def _send_route(self, body: str, cta: str, send_as: str, rationale: str) -> dict:
        action = self._send(body, cta, rationale)
        action["send_as"] = send_as
        return action

    def _owner_name(self, merchant: dict) -> str:
        identity = merchant.get("identity", {}) or {}
        return identity.get("owner_first_name") or identity.get("name") or "Team"

    def _merchant_name(self, merchant: dict) -> str:
        identity = merchant.get("identity", {}) or {}
        return identity.get("name") or "your business"

    def _customer_name(self, customer: dict) -> str:
        identity = customer.get("identity", {}) or {}
        return identity.get("name") or "there"

    def _prefers_hindi_mix(self, customer: dict) -> bool:
        language_pref = str((customer.get("identity", {}) or {}).get("language_pref", "")).lower()
        return "hi" in language_pref or "mix" in language_pref

    def _slot_labels(self, slots: Any) -> list[str]:
        if not isinstance(slots, list):
            return []
        labels: list[str] = []
        for slot in slots:
            if isinstance(slot, dict) and slot.get("label"):
                labels.append(str(slot["label"]))
        return labels

    def _first_percent(self, text: str) -> str:
        match = re.search(r"\b\d+(?:\.\d+)?%", text or "")
        return match.group(0) if match else "38%"

    def _format_pct(self, value: Any) -> str:
        if isinstance(value, (int, float)):
            pct = value * 100 if abs(value) <= 1 else value
            return f"{pct:g}%"
        return str(value)

    def _top_review_theme(self, merchant: dict, positive: bool = False) -> dict | None:
        themes = merchant.get("review_themes", []) or []
        if positive:
            themes = [theme for theme in themes if theme.get("sentiment") == "pos"]
        themes = [theme for theme in themes if theme.get("occurrences_30d") is not None]
        if not themes:
            return None
        return max(themes, key=lambda theme: theme.get("occurrences_30d") or 0)

    def _best_performance_metric_name(self, merchant: dict) -> str:
        performance = merchant.get("performance", {}) or {}
        for key, label in (
            ("profile_views_30d", "profile views"),
            ("views_30d", "profile views"),
            ("views", "profile views"),
            ("calls_30d", "calls"),
            ("calls", "calls"),
            ("directions_30d", "directions"),
            ("directions", "directions"),
            ("ctr", "CTR"),
        ):
            if performance.get(key) is not None:
                return label
        return "merchant performance"

    def _merchant_context_anchor(self, merchant: dict) -> str:
        performance = merchant.get("performance", {}) or {}
        aggregate = merchant.get("customer_aggregate", {}) or {}
        offers = [offer.get("title") for offer in merchant.get("offers", []) or [] if offer.get("status") == "active" and offer.get("title")]
        parts: list[str] = []
        for key, label in (
            ("profile_views_30d", "profile views"),
            ("views_30d", "profile views"),
            ("views", "profile views"),
            ("calls_30d", "calls"),
            ("calls", "calls"),
            ("directions_30d", "directions"),
            ("directions", "directions"),
        ):
            value = performance.get(key)
            if value is not None:
                parts.append(f"{value} {label}")
            if len(parts) >= 2:
                break
        ctr = performance.get("ctr")
        if ctr is not None:
            parts.append(f"{ctr} CTR")
        if offers:
            parts.append(f"active offer: {offers[0]}")
        for key, label in (
            ("total_active_members", "active members"),
            ("delivery_orders_30d", "delivery orders in 30d"),
            ("chronic_rx_count", "chronic Rx customers"),
            ("lapsed_180d_plus", "lapsed customers"),
            ("high_risk_adult_count", "high-risk adult patients"),
        ):
            value = aggregate.get(key)
            if value is not None:
                parts.append(f"{value} {label}")
                break
        return ", ".join(parts[:4]) if parts else "the latest pushed merchant context is available"
