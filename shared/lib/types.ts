/**
 * Types mirroring api-contract-v1.yaml.
 *
 * These are hand-written to stay readable. If they drift from the contract,
 * the contract wins. Once the real FastAPI server is up you can generate this
 * file from its /openapi.json instead.
 */

export type PhotoStatus = 'pending' | 'uploaded' | 'processing' | 'done' | 'failed'
export type EventStatus = 'draft' | 'active' | 'ended'
export type TierCode = 'basic' | 'pro' | 'premium'
export type BrandingMode = 'platform' | 'studio'
export type Role = 'owner' | 'photographer' | 'platform_admin'

export interface ApiError {
  error: {
    /** Stable identifier. Switch on this, never on `message`. */
    code: string
    /** Safe to show a user as-is. */
    message: string
    details?: Record<string, unknown>
  }
}

export interface Me {
  user_id: string
  username: string
  /** Optional. Many small studios have no email address. */
  email: string | null
  name: string | null
  role: Role
  /**
   * True while the account still carries the password whoever created it chose.
   * The app blocks everything else until it is changed.
   */
  must_change_password: boolean
  /**
   * Null for a platform admin, who has no studio at all.
   *
   * That null is not a placeholder. It is what the server's row-level security
   * keys on: an account with no studio matches no tenant policy, so admins are
   * served on a different connection entirely. Always optional-chain this.
   */
  studio: {
    id: string
    name: string
    /** Confirms identity during a manual password reset. Not used for OTP. */
    phone: string | null
    brand_color: string | null
    brand_logo_url: string | null
  } | null
}

export interface Event {
  id: string
  name: string
  event_date: string
  status: EventStatus
  /** Snapshot of what was purchased, not a live lookup. */
  tier_code: TierCode
  branding_mode: BrandingMode
  photo_retention_days: number
  /**
   * Snapshotted from the studio's negotiated setting when the event was
   * created. Changing that setting later does not touch this event.
   */
  face_retention_days: number
  qr_url: string
  created_at: string
  /** When the payment webhook promoted this from draft. Null while draft. */
  activated_at: string | null
  ended_at: string | null
}

export interface EventStats {
  pending: number
  uploaded: number
  processing: number
  done: number
  failed: number
  guests_registered: number
  /**
   * Age of the oldest unprocessed job. Climbing means the queue is falling
   * behind, which is the earliest visible sign of trouble during an event.
   */
  oldest_pending_seconds: number | null
}

export interface Photo {
  id: string
  status: PhotoStatus
  thumbnail_url: string | null
  face_count: number | null
  /** Present only when status is 'failed'. */
  error_code: string | null
  created_at: string
  processed_at: string | null
  /** Local-only, not in the contract. Used by the uploader UI. */
  filename?: string
  size_bytes?: number
  round?: number
}

export interface Page<T> {
  items: T[]
  next_cursor: string | null
}

export interface UploadSlot {
  client_ref: string
  photo_id: string
  upload_url: string
  expires_at: string
}

/** Maps a failed photo's error_code to what the photographer should see. */
export const PHOTO_ERROR_LABELS: Record<string, string> = {
  TOO_LARGE: 'Too large',
  NO_FACES: 'No faces',
  UPLOAD_INCOMPLETE: 'Upload cut off',
  CORRUPT_IMAGE: 'Unreadable file',
  PROCESSING_FAILED: 'Processing failed',
}

/** Maps a selfie rejection to the retake instruction a guest sees. */
export const SELFIE_ERROR_MESSAGES: Record<string, string> = {
  NO_FACE_DETECTED: 'We could not find a face. Hold the camera closer and make sure your face is lit from the front.',
  MULTIPLE_FACES: 'Please take a selfie with just yourself in frame.',
  FACE_TOO_SMALL: 'Please hold the camera a little closer.',
  FACE_TOO_BLURRY: 'That photo was too blurry. Hold still and try again.',
}

/* ── guest ─────────────────────────────────────────────────────────── */

/** Everything safe to show an unauthenticated stranger holding a QR token. */
export interface GuestEventInfo {
  event_name: string
  event_date: string
  /** False once the event has ended. */
  accepting_guests: boolean
  /**
   * Shown BEFORE consent, so a guest knows what they are agreeing to before
   * the camera opens rather than after. Negotiated per studio, so never
   * hardcode this number in the copy.
   */
  face_retention_days: number
  branding: {
    mode: BrandingMode
    studio_name: string | null
    logo_url: string | null
    brand_color: string | null
  }
}

/** What consenting returns. The session token is the guest's whole identity. */
export interface GuestSession {
  session_token: string
  expires_at: string | null
  face_retention_days: number
  /**
   * The actual calendar date this guest's selfie is deleted, frozen at the
   * moment they consented. Show this rather than a duration: a date is a
   * promise somebody can check, and it is the one sentence in the product that
   * has to be true.
   */
  face_deletion_date: string
}

export interface GuestPhoto {
  photo_id: string
  /** Presigned GET, roughly one hour. */
  thumbnail_url: string
  full_url: string
  width?: number
  height?: number
  matched_at: string
}

/* ── studio members ────────────────────────────────────────────────── */

export interface StudioMember {
  user_id: string
  username: string
  email: string | null
  name: string | null
  role: 'owner' | 'photographer'
  /**
   * Whether the account still works at all. Removing someone disables them
   * rather than deleting the row, so the audit trail and the record of who
   * uploaded what survive.
   */
  status: 'active' | 'disabled'
  /**
   * True while they still hold the password whoever created the account chose.
   * Combined with `last_active_at` this is what tells you someone was set up
   * and has not signed in yet.
   */
  must_change_password: boolean
  /** Across all of this studio's events. Shows who is actually working. */
  photos_uploaded: number
  last_active_at: string | null
  created_at: string
}

/** Returned once, on creation. The password is never retrievable again. */
export interface CreatedMember extends StudioMember {
  initial_password: string
}

export interface TokenPair {
  access_token: string
  refresh_token: string
  /** Seconds until the access token expires. */
  expires_in: number
}

/* ── platform admin ────────────────────────────────────────────────── */

export type StudioStatus = 'active' | 'suspended'

export interface AdminStudio {
  id: string
  name: string
  slug: string
  city: string | null
  phone: string | null
  status: StudioStatus
  brand_color: string | null
  /**
   * This studio's negotiated biometric retention. Never a tier feature, and
   * capped server-side by a limit that needs a restart to change.
   */
  default_face_retention_days: number
  owner_username: string
  events_total: number
  events_this_month: number
  photos_total: number
  revenue_paise: number
  created_at: string
  last_event_at: string | null
}

/** Returned once, on creation or reset. Never retrievable again. */
export interface CreatedStudio extends AdminStudio {
  initial_password: string
}

export interface AdminOverview {
  studios_active: number
  studios_suspended: number
  events_this_month: number
  events_live_now: number
  revenue_this_month_paise: number
  revenue_all_time_paise: number
  photos_this_month: number
}

/* ── billing ───────────────────────────────────────────────────────── */

export interface Tier {
  code: TierCode
  name: string
  price_paise: number
  branding_mode: BrandingMode
  photo_retention_days: number
  /**
   * Identical on every tier, because price cannot buy longer biometric
   * retention. It is negotiated per studio by the platform and capped
   * server-side, so this is the calling studio's number, not the tier's.
   */
  face_retention_days: number
  custom_domain: boolean
}

export interface Checkout {
  razorpay_order_id: string
  amount_paise: number
  currency: 'INR'
  /** Public Razorpay key. The secret never leaves the server. */
  key_id: string
  event_id: string
  prefill_contact: string | null
  prefill_name: string | null
}

export type PaymentStatus = 'created' | 'captured' | 'failed' | 'refunded'

export interface Payment {
  id: string
  event_id: string
  event_name: string
  tier_code: TierCode
  amount_paise: number
  status: PaymentStatus
  method: string | null
  razorpay_payment_id: string | null
  created_at: string
}
