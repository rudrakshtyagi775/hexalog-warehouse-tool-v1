import { apiClient } from './client'
import type {
  OutwardBoxCreate,
  OutwardBoxResponse,
  OutwardPOResponse,
  OutwardScanCreate,
  OutwardScanCreateResponse,
  OutwardScanResponse,
} from '@/types'

export const outwardApi = {
  uploadPO: (formData: FormData) =>
    apiClient
      .post<OutwardPOResponse>('/outward/pos', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      .then((r) => r.data),

  createBox: (data: OutwardBoxCreate) =>
    apiClient.post<OutwardBoxResponse>('/outward/boxes', data).then((r) => r.data),

  getBox: (boxId: string) =>
    apiClient.get<OutwardBoxResponse>(`/outward/boxes/${boxId}`).then((r) => r.data),

  closeBox: (boxId: string) =>
    apiClient.post<OutwardBoxResponse>(`/outward/boxes/${boxId}/close`).then((r) => r.data),

  addScan: (boxId: string, data: OutwardScanCreate) =>
    apiClient
      .post<OutwardScanCreateResponse>(`/outward/boxes/${boxId}/scans`, data)
      .then((r) => r.data),

  deleteScan: (scanId: number) =>
    apiClient.delete<OutwardScanResponse>(`/outward/scans/${scanId}`).then((r) => r.data),
}
