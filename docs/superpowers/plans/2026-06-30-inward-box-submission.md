# Inward Box Submission Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `POST /api/inward/boxes/{box_id}/submit` to transition a closed box from `pending_verification` to `completed`, generating its inscan number and writing inventory ledger entries — completing the inward workflow end-to-end.

**Architecture:** A new `submit_box()` service function follows the same commit-and-reload pattern as the existing `close_box()`. A thin router endpoint wraps it. The frontend `CloseBox` page gains one new render state for `pending_verification` that shows a confirm-and-submit button, before the existing success screen.

**Tech Stack:** FastAPI async, SQLAlchemy 2.0, PostgreSQL 16, React 18 + TypeScript, TanStack Query v5

## Global Constraints

- `asyncio_mode = "auto"` — never add `@pytest.mark.asyncio` to any test
- Ruff `line-length=100`, `target-version=py312`
- Services commit; routers never commit
- `write_audit_log` never commits — it runs inside the caller's transaction
- `expire_on_commit=False` is set on `async_sessionmaker` — do not call `await db.refresh(box)` between commit and the reload select
- `organisation_id` always comes from `current_user.organisation_id` (JWT `org` claim), never from request body
- `password_hash` must never appear in any API response or audit log
- Local imports inside service functions (`from app.models.customer import Customer`, etc.) follow the established pattern in `inward_service.py`
- TypeScript: `BoxResponse` is already in `@/types`; do not redefine it

---

## File Map

| File | Change |
|------|--------|
| `app/services/inward_service.py` | Add `submit_box()` function after `close_box()` |
| `app/routers/inward.py` | Add `POST /boxes/{box_id}/submit` endpoint |
| `tests/integration/test_inward_boxes.py` | Add 7 new test functions |
| `frontend/src/api/inward.ts` | Add `submitBox()` to `inwardApi` |
| `frontend/src/hooks/useInward.ts` | Add `useSubmitBox()` hook |
| `frontend/src/pages/CloseBox.tsx` | Add `pending_verification` state + submit handler |

No schema changes. No migration. `inscan_number`, `submitted_at`, and `submitted_by` columns already exist in `inward_boxes` from migration `c0b6c98a6f2d`. `BoxResponse` already includes `inscan_number: str | None`.

---

## Task 1 — Backend: service + endpoint (TDD)

**Files:**
- Modify: `app/services/inward_service.py`
- Modify: `app/routers/inward.py`
- Test: `tests/integration/test_inward_boxes.py`

**Interfaces:**
- Consumes: existing `_next_counter()`, `selectinload(InwardBox.scans)`, `write_audit_log()`, `_box_to_response()`, `require_packer`
- Produces: `submit_box(db, *, box_id, organisation_id, submitted_by, ip_address) -> InwardBox`; `POST /api/inward/boxes/{box_id}/submit` → `BoxResponse`

---

- [ ] **Step 1.1: Write the 7 failing tests**

Append these test functions to `tests/integration/test_inward_boxes.py`. They require no new imports beyond what is already at the top of that file, plus:

```python
from app.models.inward import InwardScan, InventoryLedgerEntry
from app.models.enums import InwardCodeTypeEnum, LedgerSourceTypeEnum
from sqlalchemy import select
```

Add those imports at the top of the file alongside the existing imports.

```python
# ── Submit box ────────────────────────────────────────────────────────────────

async def test_submit_box_success(client, packer_user, org, closed_box):
    """pending_verification → completed; inscan_number is set; is_read_only becomes True."""
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        f"/api/inward/boxes/{closed_box.box_id}/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["inscan_number"] is not None
    assert body["inscan_number"].startswith("INS-TST-")
    assert body["is_read_only"] is True


async def test_submit_box_inscan_number_format(client, packer_user, org, closed_box):
    """Inscan number matches INS-<CUSTCODE>-<YYYYMMDD>-<XXXX>."""
    import re
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        f"/api/inward/boxes/{closed_box.box_id}/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    inscan = resp.json()["inscan_number"]
    assert re.match(r"^INS-[A-Z]+-\d{8}-\d{4}$", inscan), f"Bad format: {inscan}"


async def test_submit_box_wrong_status_scanning(client, packer_user, org, open_box):
    """Box in 'scanning' status cannot be submitted — must close first."""
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        f"/api/inward/boxes/{open_box.box_id}/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "pending_verification" in resp.json()["detail"].lower()


async def test_submit_box_wrong_status_completed(client, packer_user, org, db, customer):
    """Already-completed box cannot be submitted again."""
    box = InwardBox(
        box_id="B-TST-000001",
        organisation_id=org.id,
        customer_id=customer.id,
        status=InwardBoxStatusEnum.completed,
        scanned_qty=2,
        inscan_number="INS-TST-20260630-0001",
    )
    db.add(box)
    await db.flush()

    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        f"/api/inward/boxes/{box.box_id}/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "pending_verification" in resp.json()["detail"].lower()


async def test_submit_box_not_found(client, packer_user, org):
    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        "/api/inward/boxes/B-XXX-999999/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


async def test_submit_box_requires_auth(client, closed_box):
    resp = await client.post(f"/api/inward/boxes/{closed_box.box_id}/submit")
    assert resp.status_code == 401


async def test_submit_box_writes_ledger_entries(client, packer_user, org, db, customer):
    """One +1 InventoryLedgerEntry per active scan; deleted scans are skipped."""
    box = InwardBox(
        box_id="B-TST-000097",
        organisation_id=org.id,
        customer_id=customer.id,
        status=InwardBoxStatusEnum.pending_verification,
        scanned_qty=2,
        physical_qty=2,
    )
    db.add(box)
    await db.flush()

    scan_a = InwardScan(
        organisation_id=org.id,
        inward_box_id=box.id,
        ean="1111111111111",
        code_type=InwardCodeTypeEnum.ean,
        is_deleted=False,
    )
    scan_b = InwardScan(
        organisation_id=org.id,
        inward_box_id=box.id,
        ean="2222222222222",
        code_type=InwardCodeTypeEnum.ean,
        is_deleted=False,
    )
    scan_del = InwardScan(
        organisation_id=org.id,
        inward_box_id=box.id,
        ean="3333333333333",
        code_type=InwardCodeTypeEnum.ean,
        is_deleted=True,
    )
    db.add_all([scan_a, scan_b, scan_del])
    await db.flush()

    token = await _login(client, "packer@test.com", "PackerPass1!", org.id)
    resp = await client.post(
        f"/api/inward/boxes/{box.box_id}/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    entries = (
        await db.execute(
            select(InventoryLedgerEntry).where(
                InventoryLedgerEntry.organisation_id == org.id,
                InventoryLedgerEntry.source_type == LedgerSourceTypeEnum.inward_submission,
            )
        )
    ).scalars().all()

    assert len(entries) == 2
    assert {e.ean for e in entries} == {"1111111111111", "2222222222222"}
    assert all(e.quantity_change == 1 for e in entries)


async def test_submit_box_sequential_inscan_numbers(client, admin_user, org, db, customer):
    """Counter increments per customer per day; second submission gets next number."""
    box1 = InwardBox(
        box_id="B-TST-000095",
        organisation_id=org.id,
        customer_id=customer.id,
        status=InwardBoxStatusEnum.pending_verification,
        scanned_qty=0,
        physical_qty=0,
    )
    box2 = InwardBox(
        box_id="B-TST-000096",
        organisation_id=org.id,
        customer_id=customer.id,
        status=InwardBoxStatusEnum.pending_verification,
        scanned_qty=0,
        physical_qty=0,
    )
    db.add_all([box1, box2])
    await db.flush()

    token = await _login(client, "admin@test.com", "AdminPass1!", org.id)
    r1 = await client.post(
        f"/api/inward/boxes/{box1.box_id}/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    r2 = await client.post(
        f"/api/inward/boxes/{box2.box_id}/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r1.status_code == 200
    assert r2.status_code == 200
    n1 = int(r1.json()["inscan_number"].split("-")[-1])
    n2 = int(r2.json()["inscan_number"].split("-")[-1])
    assert n2 == n1 + 1
```

- [ ] **Step 1.2: Run tests — confirm all 8 new tests fail**

```
pytest tests/integration/test_inward_boxes.py -k "submit" -v
```

Expected: `FAILED` on all 8 with `404 Not Found` (route doesn't exist yet). If any pass, something is wrong — stop and investigate.

- [ ] **Step 1.3: Add `submit_box()` to `app/services/inward_service.py`**

Insert this block immediately after the `close_box()` function (after line 360):

```python
# ── Box: submit ───────────────────────────────────────────────────────────────

async def submit_box(
    db: AsyncSession,
    *,
    box_id: str,
    organisation_id: int,
    submitted_by: int,
    ip_address: str | None,
) -> InwardBox:
    """Transition pending_verification → completed. Generates inscan_number,
    writes one +1 InventoryLedgerEntry per active scan, and writes an audit row.
    """
    from zoneinfo import ZoneInfo
    from app.config import settings
    from app.models.customer import Customer

    result = await db.execute(
        select(InwardBox)
        .options(selectinload(InwardBox.scans))
        .where(
            InwardBox.box_id == box_id,
            InwardBox.organisation_id == organisation_id,
        )
    )
    box = result.scalar_one_or_none()
    if box is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Box not found")

    if box.status != InwardBoxStatusEnum.pending_verification:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Box must be in 'pending_verification' status to submit "
                f"(current: {box.status.value})"
            ),
        )

    # Load customer code for inscan_number format: INS-<CUSTCODE>-<YYYYMMDD>-<XXXX>
    cust_result = await db.execute(
        select(Customer).where(Customer.id == box.customer_id)
    )
    customer = cust_result.scalar_one()

    tz = ZoneInfo(settings.APP_TIMEZONE)
    today = datetime.now(tz).strftime("%Y%m%d")
    n = await _next_counter(
        db,
        counter_type=CounterTypeEnum.inscan_number,
        organisation_id=organisation_id,
        customer_code=customer.code,
        date_key=today,
    )
    inscan_number = f"INS-{customer.code}-{today}-{n:04d}"

    # One +1 ledger entry per active scan
    active_scans = [s for s in box.scans if not s.is_deleted]
    for scan in active_scans:
        db.add(InventoryLedgerEntry(
            organisation_id=organisation_id,
            customer_id=box.customer_id,
            ean=scan.ean,
            quantity_change=1,
            source_type=LedgerSourceTypeEnum.inward_submission,
            source_id=scan.id,
        ))

    box.status = InwardBoxStatusEnum.completed
    box.inscan_number = inscan_number
    box.submitted_at = datetime.now(timezone.utc)
    box.submitted_by = submitted_by

    await write_audit_log(
        db,
        module=AuditModuleEnum.inward,
        action="box_submitted",
        resource_type="inward_box",
        resource_id=box.id,
        user_id=submitted_by,
        organisation_id=organisation_id,
        before_data={"status": "pending_verification"},
        after_data={
            "status": "completed",
            "inscan_number": inscan_number,
            "ledger_entries": len(active_scans),
        },
        ip_address=ip_address,
    )

    await db.commit()

    # Reload with scans eager-loaded (lazy="raise" blocks post-commit attribute access)
    reloaded = await db.execute(
        select(InwardBox)
        .options(selectinload(InwardBox.scans))
        .where(InwardBox.id == box.id)
    )
    return reloaded.scalar_one()
```

- [ ] **Step 1.4: Add the submit endpoint to `app/routers/inward.py`**

Insert this block immediately after the `close_box_endpoint` function (after line 119):

```python
@router.post("/boxes/{box_id}/submit", response_model=BoxResponse)
async def submit_box_endpoint(
    box_id: str,
    request: Request,
    current_user=Depends(require_packer),
    db: AsyncSession = Depends(get_db),
) -> BoxResponse:
    box = await inward_service.submit_box(
        db,
        box_id=box_id,
        organisation_id=current_user.organisation_id,
        submitted_by=current_user.user_id,
        ip_address=get_client_ip(request),
    )
    return _box_to_response(box)
```

No import changes needed — `inward_service`, `require_packer`, `get_client_ip`, `Request`, `get_db`, `AsyncSession`, `Depends`, and `BoxResponse` are all already imported in the router.

- [ ] **Step 1.5: Run the 8 submit tests — confirm all pass**

```
pytest tests/integration/test_inward_boxes.py -k "submit" -v
```

Expected output:
```
test_submit_box_success PASSED
test_submit_box_inscan_number_format PASSED
test_submit_box_wrong_status_scanning PASSED
test_submit_box_wrong_status_completed PASSED
test_submit_box_not_found PASSED
test_submit_box_requires_auth PASSED
test_submit_box_writes_ledger_entries PASSED
test_submit_box_sequential_inscan_numbers PASSED
```

- [ ] **Step 1.6: Run the full inward_boxes suite — confirm no regressions**

```
pytest tests/integration/test_inward_boxes.py -v
```

Expected: all 17 tests pass (9 existing + 8 new).

- [ ] **Step 1.7: Run the full integration suite — confirm no regressions**

```
pytest tests/integration/ -v
```

Expected: all 163 existing tests plus 8 new = 171 tests pass, 0 failures.

- [ ] **Step 1.8: Lint**

```
ruff check app/services/inward_service.py app/routers/inward.py
```

Expected: no output (no errors).

- [ ] **Step 1.9: Commit**

```
git add app/services/inward_service.py app/routers/inward.py tests/integration/test_inward_boxes.py
git commit -m "feat(inward): add box submission endpoint — pending_verification to completed with inscan number and ledger entries"
```

---

## Task 2 — Frontend: submission UI

Depends on Task 1 being committed (the backend endpoint must exist for manual verification to work).

**Files:**
- Modify: `frontend/src/api/inward.ts`
- Modify: `frontend/src/hooks/useInward.ts`
- Modify: `frontend/src/pages/CloseBox.tsx`

**Interfaces:**
- Consumes: `POST /api/inward/boxes/{box_id}/submit` → `BoxResponse`
- Produces: `inwardApi.submitBox(boxId)`, `useSubmitBox()` hook, updated `CloseBox` page that handles `pending_verification` state

---

- [ ] **Step 2.1: Add `submitBox` to `frontend/src/api/inward.ts`**

Add one entry to the `inwardApi` object, after `closeBox`:

```typescript
  submitBox: (boxId: string) =>
    apiClient.post<BoxResponse>(`/inward/boxes/${boxId}/submit`).then((r) => r.data),
```

The full file after the change:

```typescript
import { apiClient } from './client'
import type {
  BoxClose,
  BoxCreate,
  BoxResponse,
  POResponse,
  ScanCreate,
  ScanCreateResponse,
  ScanResponse,
} from '@/types'

export const inwardApi = {
  uploadPO: (formData: FormData) =>
    apiClient
      .post<POResponse>('/inward/pos', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      .then((r) => r.data),

  createBox: (data: BoxCreate) =>
    apiClient.post<BoxResponse>('/inward/boxes', data).then((r) => r.data),

  getBox: (boxId: string) =>
    apiClient.get<BoxResponse>(`/inward/boxes/${boxId}`).then((r) => r.data),

  closeBox: (boxId: string, data: BoxClose) =>
    apiClient.post<BoxResponse>(`/inward/boxes/${boxId}/close`, data).then((r) => r.data),

  submitBox: (boxId: string) =>
    apiClient.post<BoxResponse>(`/inward/boxes/${boxId}/submit`).then((r) => r.data),

  addScan: (boxId: string, data: ScanCreate) =>
    apiClient
      .post<ScanCreateResponse>(`/inward/boxes/${boxId}/scans`, data)
      .then((r) => r.data),

  deleteScan: (scanId: number) =>
    apiClient.delete<ScanResponse>(`/inward/scans/${scanId}`).then((r) => r.data),
}
```

- [ ] **Step 2.2: Add `useSubmitBox` to `frontend/src/hooks/useInward.ts`**

Append this function at the end of the file:

```typescript
export function useSubmitBox() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (boxId: string) => inwardApi.submitBox(boxId),
    onSuccess: (_, boxId) =>
      qc.invalidateQueries({ queryKey: ['box', boxId] }),
  })
}
```

- [ ] **Step 2.3: Update `frontend/src/pages/CloseBox.tsx`**

Three changes:

**a) Add `useSubmitBox` to the import line** (line 4):

```typescript
import { useBox, useCloseBox, useSubmitBox } from '@/hooks/useInward'
```

**b) Add the hook call and submit handler** inside `CloseBoxPage`, immediately after the `closeBox` declaration:

```typescript
  const submitBox = useSubmitBox()

  const handleSubmit = async () => {
    if (!activeBoxId) return
    setError('')
    try {
      await submitBox.mutateAsync(activeBoxId)
    } catch (err) {
      setError(extractErrorMessage(err))
    }
  }
```

**c) Add the `pending_verification` render state** between the loading spinner block and the existing `completed` block. Insert this block immediately before `if (box?.status === 'completed')`:

```tsx
  // Pending-verification state — box is closed, awaiting final submission
  if (box?.status === 'pending_verification') {
    return (
      <div className="max-w-md mx-auto">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <CheckSquare className="h-5 w-5 text-blue-600" />
              <CardTitle>Confirm Submission</CardTitle>
            </div>
          </CardHeader>

          <div className="bg-blue-50 border border-blue-100 rounded-lg p-4 mb-5">
            <p className="text-sm text-blue-800">
              Box <span className="font-mono font-bold">{box.box_id}</span> is verified.
              Submitting will generate the Inscan Number and record inventory.
            </p>
          </div>

          <div className="bg-gray-50 rounded-lg p-4 space-y-3 mb-5">
            <div className="flex justify-between text-sm">
              <span className="text-gray-500">Scanned Qty</span>
              <span className="font-medium">{box.scanned_qty}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-gray-500">Physical Qty</span>
              <span className="font-medium">{box.physical_qty}</span>
            </div>
          </div>

          {error && (
            <Alert variant="error" className="mb-5">
              {error}
            </Alert>
          )}

          <Button
            loading={submitBox.isPending}
            className="w-full"
            onClick={() => void handleSubmit()}
          >
            <CheckCircle className="h-4 w-4" />
            Confirm &amp; Submit
          </Button>
        </Card>
      </div>
    )
  }
```

- [ ] **Step 2.4: Type-check**

```
cd frontend && npx tsc --noEmit
```

Expected: `0 errors`. Fix any type errors before proceeding.

- [ ] **Step 2.5: Manual verification**

Start the backend:
```
uvicorn app.main:app --reload
```

Start the frontend:
```
cd frontend && npm run dev
```

Walk through the complete inward workflow as a packer:
1. Login at `/login`
2. Create a box at `/create-box` (select a customer)
3. Scan items at `/scan?box=<box_id>` (scan at least one EAN)
4. Go to `/close-box?box=<box_id>`, enter the physical count matching the scanned count, click "Submit & Close Box"
5. **Expected:** Page transitions to the "Confirm Submission" state showing scanned qty and physical qty
6. Click "Confirm & Submit"
7. **Expected:** Success screen appears showing the box_id and the generated Inscan Number in `INS-<CODE>-<YYYYMMDD>-<XXXX>` format

Also verify the error path: try submitting a box that is still in `scanning` status — **Expected:** 400 error message displayed.

- [ ] **Step 2.6: Commit**

```
git add frontend/src/api/inward.ts frontend/src/hooks/useInward.ts frontend/src/pages/CloseBox.tsx
git commit -m "feat(frontend): add submit step to CloseBox — pending_verification confirm state and inscan number display"
```

---

## Why this is P0

The inward module is the furthest-along module (6 of 7 endpoints working, 31 integration tests) and the one that warehouse operators will use on day one. Without `submit_box`, the workflow has no exit state:

- `CloseBox.tsx` renders its success screen only on `status === 'completed'`
- The backend's `/close` endpoint only reaches `pending_verification`
- `inscan_number` is never generated or shown
- Inventory ledger entries are never written — the "no recorded inward stock" note will fire on every subsequent scan of any EAN from a submitted box

This is a one-task, low-risk backend addition: the counter UPSERT pattern already exists (`_next_counter`), the ledger model already exists (`InventoryLedgerEntry`), the response schema already includes `inscan_number`. The migration is already done.
