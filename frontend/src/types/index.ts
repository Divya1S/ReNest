// ─── Auth ────────────────────────────────────────────────────────────────────

export interface User {
  id: number;
  email: string;
  display_name: string;
  campus_name: string | null;
  campus?: Campus;
  is_staff: boolean;
  is_campus_manager?: boolean;
  email_verified?: boolean;
  show_on_leaderboard?: boolean;
  milestone?: string | null;
  completion_rate?: number | null;
  referral_code?: string;
  onboarding_step?: number;
}

// ─── Campus ──────────────────────────────────────────────────────────────────

export interface Campus {
  id: number;
  name: string;
  slug: string;
  subscription_tier?: "free" | "standard" | "premium";
  move_out_start?: string | null;
  move_out_end?: string | null;
  theme?: CampusTheme | null;
}

export interface CampusTheme {
  primary_color?: string;
  logo_url?: string;
  font_family?: string;
  hero_message?: string;
}

// ─── Listings ────────────────────────────────────────────────────────────────

export type ListingStatus = "available" | "reserved" | "donated" | "cancelled" | "expired" | "on_hold";
export type PriceType = "free" | "low_cost";
export type ListingCondition = "new" | "like_new" | "good" | "fair" | "poor";

export interface OwnerSummary {
  id: number;
  display_name: string;
}

export interface OwnerTrustSummary {
  average_rating: number;
  total_feedback_received: number;
  trust_badge: string;
  completed_rescues: number;
  successful_handoffs: number;
  active_listings: number;
}

export interface ListingGalleryImage {
  id: number;
  image_url: string;
  position: number;
}

export interface Listing {
  id: number;
  title: string;
  description: string;
  category: string;
  condition: ListingCondition;
  status: ListingStatus;
  price_type: PriceType;
  price?: number | null;
  price_amount?: string | number | null;
  pickup_zone: string;
  building?: string | null;
  image?: string | null;
  image_url?: string | null;
  thumb_url?: string | null;
  gallery?: ListingGalleryImage[];
  owner?: OwnerSummary | number | null;
  owner_trust_summary?: OwnerTrustSummary | null;
  campus?: Campus;
  created_at: string;
  updated_at: string;
  available_until?: string | null;
  estimated_retail_value?: number | string | null;
  estimated_student_savings?: number | string | null;
  completeness_score?: number;
  moderation_status?: string;
  view_count?: number;
  saved_count?: number;
  reservation_count?: number;
  is_saved?: boolean;
  is_urgent?: boolean;
  boosted_until?: string | null;
  condition_verified?: boolean;
  quality_hints?: Array<{ suggestion: string; field: string }>;
  can_reserve?: boolean;
  can_report?: boolean;
  has_reported?: boolean;
  can_post_update?: boolean;
  time_left_label?: string | null;
  source_scan_name?: string | null;
  source_scan_session?: string | null;
}

export interface PaginatedListings {
  results: Listing[];
  count: number;
  next: string | null;
  previous: string | null;
}

// ─── Reservations ────────────────────────────────────────────────────────────

export type ReservationStatus =
  | "requested"
  | "confirmed"
  | "completed"
  | "cancelled"
  | "expired_unresolved";

export interface Reservation {
  id: number;
  listing: Listing;
  claimant: User;
  owner?: User;
  status: ReservationStatus;
  created_at: string;
  updated_at: string;
  pickup_time_window?: string | null;
  confirmed_slot?: string | null;
  handoff_pin_set?: boolean;
  notes?: string;
}

// ─── Notifications ───────────────────────────────────────────────────────────

export type NotificationChannel = "both" | "push" | "email" | "off";

export interface Notification {
  id: number;
  notification_type: string;
  title: string;
  body: string;
  url?: string | null;
  is_read: boolean;
  created_at: string;
}

export interface NotificationPreference {
  notification_type: string;
  channel: NotificationChannel;
  quiet_hours_start?: string | null;
  quiet_hours_end?: string | null;
}

// ─── Saved Searches ──────────────────────────────────────────────────────────

export interface SavedSearch {
  id: number;
  label?: string;
  keyword?: string;
  category?: string;
  price_type?: PriceType;
  last_notified_at?: string | null;
}

// ─── Hubs ────────────────────────────────────────────────────────────────────

export interface Hub {
  id: number;
  name: string;
  building: string;
  campus?: Campus;
  capacity: number;
  open_instructions?: string;
  is_accepting?: boolean;
}

export interface CollectionEvent {
  id: number;
  hub: number | Hub;
  title: string;
  status: "scheduled" | "in_progress" | "completed" | "cancelled";
  scheduled_at: string;
  holds_count: number;
}

// ─── Partner / API Keys ──────────────────────────────────────────────────────

export interface PartnerUsage {
  campus: string;
  listing_count: number;
  reservation_count: number;
  webhook_endpoints: number;
  rate_limit_per_day: number;
  scopes: string[];
}

export interface WebhookEndpoint {
  id: number;
  url: string;
  events: string[];
  secret?: string;
}

// ─── Leaderboard ─────────────────────────────────────────────────────────────

export interface LeaderboardEntry {
  rank: number;
  display_name: string;
  is_me: boolean;
  milestone?: string | null;
  item_count: number;
  total_value: number;
  completion_rate?: number | null;
}

export interface BuildingRow {
  building: string;
  item_count: number;
  total_value: number;
  contributors: number;
}

// ─── API response wrappers ───────────────────────────────────────────────────

export interface PaginatedResponse<T> {
  results: T[];
  count: number;
  next: string | null;
  previous: string | null;
}

export interface AuthMeResponse {
  user: User | null;
}

export interface AuthActionResponse {
  user: User;
  /** DEBUG-only: verification link surfaced in the UI when emails go to the console backend. */
  dev_verify_url?: string;
}

// ─── Browse ───────────────────────────────────────────────────────────────────

export interface NlChip {
  field: string;
  label: string;
}

export interface NlParseResult {
  search: string | null;
  category?: string;
  price_type?: string;
  chips?: NlChip[];
}

export interface PricingHint {
  suggestion: string;
  sample_size: number;
  median_price?: number;
}

// ─── Dashboard ────────────────────────────────────────────────────────────────

interface DashboardStats {
  incoming_pickup_actions: number;
  tasks_due_today: number;
  ready_to_publish_now: number;
  scan_savings_total: number;
}

interface PickupAction {
  reservation_id: number;
  listing_title: string;
  claimant_name: string;
  pickup_zone?: string;
  status: string;
}

interface HandoffWaitingFeedback {
  reservation_id: number;
  listing_title: string;
  counterparty_name: string;
}

interface FulfillOpportunity {
  listing: { id: number; title: string; category: string };
  match_count: number;
}

interface RequestRecommendation {
  request: { id: number; title: string; category: string };
  match_count: number;
}

interface ReadyToPublishItem {
  id: number;
  title: string;
  image_url?: string | null;
  notes?: string;
  category: string;
  scan_session: string;
}

interface MoveOutTask {
  id: string | number;
  [key: string]: unknown;
}

interface ScanSessionSummary {
  ready_to_publish_count: number;
  missing_info_count: number;
}

interface ScanSession {
  id: string;
  name: string;
  status: string;
  room_type: string;
  room_label: string;
  progress_percent: number;
  summary: ScanSessionSummary;
}

interface ChartEntry {
  name: string;
  value: number;
}

export interface NotificationPreview {
  id: number;
  priority: string;
  title: string;
  body: string;
  created_at: string;
  link_path?: string;
}

export interface DashboardData {
  stats: DashboardStats;
  notification_preview: NotificationPreview[];
  action_queues: {
    ready_to_publish_now: ReadyToPublishItem[];
    incoming_pickup_actions: PickupAction[];
    move_out_tasks: MoveOutTask[];
    handoffs_waiting_for_feedback?: HandoffWaitingFeedback[];
    fulfill_opportunities?: FulfillOpportunity[];
    request_recommendations?: RequestRecommendation[];
  };
  category_breakdown: ChartEntry[];
  status_breakdown: ChartEntry[];
  scan_overview: ScanSession[];
}

// ─── Handoff Hub ──────────────────────────────────────────────────────────────

export interface ChatMessage {
  id: number;
  body: string;
  is_mine: boolean;
  sender_name: string;
  created_at: string;
}

export interface FeedbackEntry {
  id: number;
  reviewer: { display_name: string };
  rating: number;
  tags?: string[];
  note?: string;
  created_at: string;
}

export interface ReservationDetail {
  id: number;
  status: ReservationStatus | "expired_unresolved";
  listing_detail: { id: number; title: string };
  counterparty_name: string;
  pickup_time_window?: string | null;
  pickup_zone?: string;
  move_out_deadline?: string | null;
  handoff_code: string;
  time_pressure: "urgent" | "soon" | "normal";
  next_step?: string;
  relationship: "owner" | "claimant";
  allowed_actions: string[];
  handoff_checklist: string[];
  confirmed_slot?: string | null;
  pickup_slots?: string[];
  handoff_pin_display?: string;
  can_leave_feedback?: boolean;
  feedback_summary?: { average_rating: number; count: number };
  my_feedback?: { rating: number; note?: string };
  feedback_entries?: FeedbackEntry[];
}
