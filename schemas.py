from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class FlexibleModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class Scope(str, Enum):
    category = "category"
    merchant = "merchant"
    customer = "customer"
    trigger = "trigger"


class Voice(FlexibleModel):
    tone: Optional[str] = None
    register_: Optional[str] = Field(default=None, alias="register")
    code_mix: Optional[str] = None
    vocab_allowed: list[str] = Field(default_factory=list)
    vocab_taboo: list[str] = Field(default_factory=list)
    taboos: list[str] = Field(default_factory=list)
    salutation_examples: list[str] = Field(default_factory=list)
    tone_examples: list[str] = Field(default_factory=list)


class CategoryOffer(FlexibleModel):
    id: Optional[str] = None
    title: str
    value: Optional[str] = None
    audience: Optional[str] = None
    type: Optional[str] = None


class PeerStats(FlexibleModel):
    scope: Optional[str] = None
    avg_rating: Optional[float] = None
    avg_reviews: Optional[int] = None
    avg_review_count: Optional[int] = None
    avg_views_30d: Optional[int] = None
    avg_calls_30d: Optional[int] = None
    avg_directions_30d: Optional[int] = None
    avg_ctr: Optional[float] = None
    avg_photos: Optional[int] = None
    avg_post_freq_days: Optional[int] = None
    retention_3mo_pct: Optional[float] = None
    retention_6mo_pct: Optional[float] = None
    retention_30d_pct: Optional[float] = None
    monthly_churn_pct: Optional[float] = None
    trial_to_paid_pct: Optional[float] = None
    repeat_customer_pct: Optional[float] = None
    delivery_share_pct: Optional[float] = None


class DigestItem(FlexibleModel):
    id: str
    kind: str
    title: str
    source: Optional[str] = None
    trial_n: Optional[int] = None
    patient_segment: Optional[str] = None
    summary: Optional[str] = None
    actionable: Optional[str] = None
    date: Optional[str] = None
    credits: Optional[int] = None


class PatientContent(FlexibleModel):
    id: str
    title: str
    channel: Optional[str] = None
    length_seconds: Optional[int] = None
    body: Optional[str] = None


class SeasonalBeat(FlexibleModel):
    month_range: str
    note: str


class TrendSignal(FlexibleModel):
    query: str
    delta_yoy: Optional[float] = None
    segment_age: Optional[str] = None
    skew: Optional[str] = None


class CategoryContext(FlexibleModel):
    slug: str
    display_name: Optional[str] = None
    offer_catalog: list[CategoryOffer] = Field(default_factory=list)
    voice: Optional[Voice] = None
    peer_stats: Optional[PeerStats] = None
    digest: list[DigestItem] = Field(default_factory=list)
    patient_content_library: list[PatientContent] = Field(default_factory=list)
    seasonal_beats: list[SeasonalBeat] = Field(default_factory=list)
    trend_signals: list[TrendSignal] = Field(default_factory=list)
    regulatory_authorities: list[str] = Field(default_factory=list)
    professional_journals: list[str] = Field(default_factory=list)


class MerchantIdentity(FlexibleModel):
    name: str
    city: Optional[str] = None
    locality: Optional[str] = None
    place_id: Optional[str] = None
    verified: Optional[bool] = None
    languages: list[str] = Field(default_factory=list)
    owner_first_name: Optional[str] = None
    established_year: Optional[int] = None


class Subscription(FlexibleModel):
    status: str
    plan: Optional[str] = None
    days_remaining: Optional[int] = None
    days_since_expiry: Optional[int] = None
    renewed_at: Optional[str] = None


class PerformanceDelta7d(FlexibleModel):
    views_pct: Optional[float] = None
    calls_pct: Optional[float] = None
    ctr_pct: Optional[float] = None


class Performance(FlexibleModel):
    window_days: Optional[int] = None
    views: Optional[int] = None
    calls: Optional[int] = None
    directions: Optional[int] = None
    ctr: Optional[float] = None
    leads: Optional[int] = None
    delta_7d: Optional[PerformanceDelta7d] = None


class MerchantOffer(FlexibleModel):
    id: str
    title: str
    status: str
    started: Optional[str] = None
    ended: Optional[str] = None


class ConversationHistoryItem(FlexibleModel):
    ts: str
    from_: str = Field(alias="from")
    body: str
    engagement: Optional[str] = None


class CustomerAggregate(FlexibleModel):
    total_unique_ytd: Optional[int] = None
    lapsed_180d_plus: Optional[int] = None
    lapsed_90d_plus: Optional[int] = None
    retention_6mo_pct: Optional[float] = None
    retention_3mo_pct: Optional[float] = None
    high_risk_adult_count: Optional[int] = None
    delivery_orders_30d: Optional[int] = None
    dine_in_orders_30d: Optional[int] = None
    total_active_members: Optional[int] = None
    monthly_churn_pct: Optional[float] = None
    trial_to_paid_pct: Optional[float] = None
    repeat_customer_pct: Optional[float] = None
    delivery_share_pct: Optional[float] = None
    chronic_rx_count: Optional[int] = None


class ReviewTheme(FlexibleModel):
    theme: str
    sentiment: str
    occurrences_30d: Optional[int] = None
    common_quote: Optional[str] = None


class MerchantContext(FlexibleModel):
    merchant_id: str
    category_slug: str
    identity: MerchantIdentity
    subscription: Optional[Subscription] = None
    performance: Optional[Performance] = None
    offers: list[MerchantOffer] = Field(default_factory=list)
    conversation_history: list[ConversationHistoryItem] = Field(default_factory=list)
    customer_aggregate: Optional[CustomerAggregate] = None
    signals: list[str] = Field(default_factory=list)
    review_themes: list[ReviewTheme] = Field(default_factory=list)


class CustomerIdentity(FlexibleModel):
    name: str
    phone_redacted: Optional[str] = None
    language_pref: Optional[str] = None
    age_band: Optional[str] = None
    senior_citizen: Optional[bool] = None


class CustomerRelationship(FlexibleModel):
    first_visit: Optional[str] = None
    last_visit: Optional[str] = None
    visits_total: Optional[int] = None
    services_received: list[str] = Field(default_factory=list)
    lifetime_value: Optional[int] = None
    favourite_dish: Optional[str] = None
    chronic_conditions: list[str] = Field(default_factory=list)


class CustomerPreferences(FlexibleModel):
    preferred_slots: Optional[str] = None
    channel: Optional[str] = None
    reminder_opt_in: Optional[bool] = None
    preferred_stylist: Optional[str] = None
    wedding_date: Optional[str] = None
    office_nearby: Optional[bool] = None
    family_size: Optional[int] = None
    training_focus: Optional[str] = None
    health_focus: Optional[str] = None
    delivery_address: Optional[str] = None
    household_size: Optional[int] = None


class CustomerConsent(FlexibleModel):
    opted_in_at: Optional[str] = None
    scope: list[str] = Field(default_factory=list)


class CustomerContext(FlexibleModel):
    customer_id: str
    merchant_id: str
    identity: CustomerIdentity
    relationship: Optional[CustomerRelationship] = None
    state: Optional[str] = None
    preferences: Optional[CustomerPreferences] = None
    consent: Optional[CustomerConsent] = None


class TimeOption(FlexibleModel):
    iso: str
    label: str


class TriggerPayload(FlexibleModel):
    category: Optional[str] = None
    top_item_id: Optional[str] = None
    deadline_iso: Optional[str] = None
    service_due: Optional[str] = None
    last_service_date: Optional[str] = None
    due_date: Optional[str] = None
    available_slots: list[TimeOption] = Field(default_factory=list)
    metric: Optional[str] = None
    delta_pct: Optional[float] = None
    window: Optional[str] = None
    vs_baseline: Optional[int] = None
    days_remaining: Optional[int] = None
    plan: Optional[str] = None
    renewal_amount: Optional[int] = None
    festival: Optional[str] = None
    date: Optional[str] = None
    days_until: Optional[int] = None
    category_relevance: list[str] = Field(default_factory=list)
    wedding_date: Optional[str] = None
    trial_completed: Optional[str] = None
    days_to_wedding: Optional[int] = None
    next_step_window_open: Optional[str] = None
    ask_template: Optional[str] = None
    last_ask_at: Optional[str] = None
    days_since_expiry: Optional[int] = None
    perf_dip_pct: Optional[float] = None
    lapsed_customers_added_since_expiry: Optional[int] = None
    match: Optional[str] = None
    venue: Optional[str] = None
    city: Optional[str] = None
    match_time_iso: Optional[str] = None
    is_weeknight: Optional[bool] = None
    theme: Optional[str] = None
    occurrences_30d: Optional[int] = None
    trend: Optional[str] = None
    common_quote: Optional[str] = None
    value_now: Optional[int] = None
    milestone_value: Optional[int] = None
    is_imminent: Optional[bool] = None
    intent_topic: Optional[str] = None
    merchant_last_message: Optional[str] = None
    is_expected_seasonal: Optional[bool] = None
    season_note: Optional[str] = None
    days_since_last_visit: Optional[int] = None
    previous_focus: Optional[str] = None
    previous_membership_months: Optional[int] = None
    trial_date: Optional[str] = None
    next_session_options: list[TimeOption] = Field(default_factory=list)
    alert_id: Optional[str] = None
    molecule: Optional[str] = None
    affected_batches: list[str] = Field(default_factory=list)
    manufacturer: Optional[str] = None
    molecule_list: list[str] = Field(default_factory=list)
    last_refill: Optional[str] = None
    stock_runs_out_iso: Optional[str] = None
    delivery_address_saved: Optional[bool] = None
    season: Optional[str] = None
    trends: list[str] = Field(default_factory=list)
    shelf_action_recommended: Optional[bool] = None
    verified: Optional[bool] = None
    verification_path: Optional[str] = None
    estimated_uplift_pct: Optional[float] = None
    digest_item_id: Optional[str] = None
    credits: Optional[int] = None
    fee: Optional[str] = None
    competitor_name: Optional[str] = None
    distance_km: Optional[float] = None
    their_offer: Optional[str] = None
    opened_date: Optional[str] = None
    likely_driver: Optional[str] = None
    days_since_last_merchant_message: Optional[int] = None
    last_topic: Optional[str] = None


class TriggerContext(FlexibleModel):
    id: str
    scope: Literal["merchant", "customer"]
    kind: str
    source: Optional[str] = None
    merchant_id: str
    customer_id: Optional[str] = None
    payload: TriggerPayload = Field(default_factory=TriggerPayload)
    urgency: Optional[int] = None
    suppression_key: Optional[str] = None
    expires_at: Optional[str] = None


PAYLOAD_MODELS: dict[Scope, type[BaseModel]] = {
    Scope.category: CategoryContext,
    Scope.merchant: MerchantContext,
    Scope.customer: CustomerContext,
    Scope.trigger: TriggerContext,
}


class ContextPush(BaseModel):
    scope: str
    context_id: str
    version: int = Field(ge=0)
    payload: dict[str, Any]
    delivered_at: datetime


class TickRequest(BaseModel):
    now: datetime
    available_triggers: list[str] = Field(default_factory=list)


class ReplyRequest(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str
    message: str
    received_at: datetime
    turn_number: int = Field(ge=0)
