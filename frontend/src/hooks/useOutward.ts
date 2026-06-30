import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { outwardApi } from '@/api/outward'
import type { OutwardBoxCreate, OutwardScanCreate } from '@/types'

export function useOutwardBox(boxId: string | null) {
  return useQuery({
    queryKey: ['outward-box', boxId],
    queryFn: () => outwardApi.getBox(boxId!),
    enabled: boxId !== null && boxId.length > 0,
  })
}

export function useCreateOutwardBox() {
  return useMutation({
    mutationFn: (data: OutwardBoxCreate) => outwardApi.createBox(data),
  })
}

export function useCloseOutwardBox() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (boxId: string) => outwardApi.closeBox(boxId),
    onSuccess: (_, boxId) =>
      qc.invalidateQueries({ queryKey: ['outward-box', boxId] }),
  })
}

export function useAddOutwardScan() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ boxId, data }: { boxId: string; data: OutwardScanCreate }) =>
      outwardApi.addScan(boxId, data),
    onSuccess: (_, { boxId }) =>
      qc.invalidateQueries({ queryKey: ['outward-box', boxId] }),
  })
}

export function useDeleteOutwardScan() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ scanId, boxId }: { scanId: number; boxId: string }) =>
      outwardApi.deleteScan(scanId).then((r) => ({ ...r, boxId })),
    onSuccess: (r) =>
      qc.invalidateQueries({ queryKey: ['outward-box', r.boxId] }),
  })
}

export function useUploadOutwardPO() {
  return useMutation({
    mutationFn: (formData: FormData) => outwardApi.uploadPO(formData),
  })
}
