import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { inwardApi } from '@/api/inward'
import type { BoxClose, BoxCreate, ScanCreate } from '@/types'

export function useBox(boxId: string | null) {
  return useQuery({
    queryKey: ['box', boxId],
    queryFn: () => inwardApi.getBox(boxId!),
    enabled: boxId !== null && boxId.length > 0,
  })
}

export function useCreateBox() {
  return useMutation({
    mutationFn: (data: BoxCreate) => inwardApi.createBox(data),
  })
}

export function useCloseBox() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ boxId, data }: { boxId: string; data: BoxClose }) =>
      inwardApi.closeBox(boxId, data),
    onSuccess: (_, { boxId }) =>
      qc.invalidateQueries({ queryKey: ['box', boxId] }),
  })
}

export function useAddScan() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ boxId, data }: { boxId: string; data: ScanCreate }) =>
      inwardApi.addScan(boxId, data),
    onSuccess: (_, { boxId }) =>
      qc.invalidateQueries({ queryKey: ['box', boxId] }),
  })
}

export function useDeleteScan() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ scanId, boxId }: { scanId: number; boxId: string }) =>
      inwardApi.deleteScan(scanId).then((r) => ({ ...r, boxId })),
    onSuccess: (r) =>
      qc.invalidateQueries({ queryKey: ['box', r.boxId] }),
  })
}

export function useUploadPO() {
  return useMutation({
    mutationFn: (formData: FormData) => inwardApi.uploadPO(formData),
  })
}

export function useSubmitBox() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (boxId: string) => inwardApi.submitBox(boxId),
    onSuccess: (_, boxId) =>
      qc.invalidateQueries({ queryKey: ['box', boxId] }),
  })
}
