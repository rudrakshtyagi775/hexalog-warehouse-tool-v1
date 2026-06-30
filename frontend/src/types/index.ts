// ── Auth ─────────────────────────────────────────────────────────────────────

export type Role = 'admin' | 'inward_operator' | 'packer'

export interface UserInfo {
  id: number
  full_name: string
  email: string
}

export interface OrganisationInfo {
  id: number
  name: string
}

export interface LoginRequest {
  email: string
  password: string
  organisation_id: number
}

export interface LoginResponse {
  access_token: string
  token_type: string
  expires_at: string
  user: UserInfo
  roles: Role[]
  organisation: OrganisationInfo
}

export interface RefreshResponse {
  access_token: string
  token_type: string
  expires_at: string
}

export interface MeResponse {
  user_id: number
  email: string
  full_name: string
  is_active: boolean
  organisation_id: number
  roles: Role[]
}

export interface SwitchOrganisationRequest {
  organisation_id: number
}

export interface SwitchOrganisationResponse {
  access_token: string
  token_type: string
  expires_at: string
  organisation: OrganisationInfo
  roles: Role[]
}

// ── Customers ────────────────────────────────────────────────────────────────

export type CustomerStatus = 'active' | 'inactive'

export interface CustomerListItem {
  id: number
  name: string
  code: string
  status: CustomerStatus
}

export interface CustomerResponse {
  id: number
  organisation_id: number
  name: string
  code: string
  status: CustomerStatus
  created_by: number | null
  created_at: string
  updated_at: string
}

export interface CustomerCreate {
  name: string
  code: string
}

export interface CustomerUpdate {
  name?: string
  status?: CustomerStatus
}

// ── Inward — PO ──────────────────────────────────────────────────────────────

export type InwardReferenceStatus = 'open' | 'partial' | 'complete'

export interface POLineResponse {
  id: number
  ean: string
  description: string | null
  ordered_qty: number
  packed_qty: number
}

export interface POResponse {
  id: number
  po_number: string
  customer_id: number
  status: InwardReferenceStatus
  uploaded_at: string
  lines: POLineResponse[]
}

// ── Inward — Box ─────────────────────────────────────────────────────────────

export type InwardBoxStatus = 'scanning' | 'pending_verification' | 'completed'

export interface ScanResponse {
  id: number
  ean: string
  code_type: string
  is_deleted: boolean
  created_at: string
}

export interface ScanCreateResponse extends ScanResponse {
  note: string | null
}

export interface BoxResponse {
  id: number
  box_id: string
  customer_id: number
  status: InwardBoxStatus
  physical_qty: number | null
  scanned_qty: number
  inscan_number: string | null
  scans: ScanResponse[]
  created_at: string
  is_read_only: boolean
}

// ── Forms ────────────────────────────────────────────────────────────────────

export interface BoxCreate {
  customer_id: number
}

export interface BoxClose {
  physical_qty: number
}

export interface ScanCreate {
  ean: string
  code_type?: string
}

// ── API error ────────────────────────────────────────────────────────────────

export interface ApiError {
  detail: string
}

// ── Outward — PO ─────────────────────────────────────────────────────────────

export interface OutwardPOLineResponse {
  id: number
  ean: string
  description: string | null
  ordered_qty: number
  packed_qty: number
}

export interface OutwardPOResponse {
  id: number
  po_number: string
  customer_id: number
  status: 'open' | 'closed'
  uploaded_at: string
  lines: OutwardPOLineResponse[]
}

// ── Outward — Box ─────────────────────────────────────────────────────────────

export type OutwardBoxStatus = 'open' | 'in_use' | 'closed'
export type OutwardScanResult = 'accepted' | 'rejected' | 'deleted'

export interface OutwardScanResponse {
  id: number
  ean: string
  scan_result: OutwardScanResult
  created_at: string
}

export interface OutwardScanCreateResponse extends OutwardScanResponse {
  note: string | null
}

export interface OutwardBoxResponse {
  id: number
  box_id: string
  customer_id: number
  status: OutwardBoxStatus
  scans: OutwardScanResponse[]
  created_at: string
  is_read_only: boolean
}

export interface OutwardBoxCreate {
  customer_id: number
}

export interface OutwardScanCreate {
  ean: string
}

// ── Admin ─────────────────────────────────────────────────────────────────────

export interface StatsResponse {
  inward_boxes_completed_this_month: number
  total_items_scanned: number
  total_pos_uploaded: number
  active_customers: number
}

export interface RecentSubmission {
  inscan_number: string
  customer_name: string
  box_id: string
  scanned_qty: number
  submitted_at: string | null
}

export interface RecentSubmissionsResponse {
  items: RecentSubmission[]
}

export interface AuditLogEntry {
  id: number
  module: string
  action: string
  resource_type: string
  resource_id: number | null
  user_id: number | null
  ip_address: string | null
  created_at: string
}

export interface AuditLogListResponse {
  items: AuditLogEntry[]
  total: number
  page: number
  page_size: number
}

export interface UserRoleEntry {
  role: Role
  created_at: string
}

export interface AdminUserResponse {
  id: number
  email: string
  full_name: string
  is_active: boolean
  roles: UserRoleEntry[]
  created_at: string
}

export interface AdminUserCreate {
  email: string
  full_name: string
  password: string
  roles: Role[]
}

export interface OrgResponse {
  id: number
  name: string
  is_active: boolean
  created_at: string
}
