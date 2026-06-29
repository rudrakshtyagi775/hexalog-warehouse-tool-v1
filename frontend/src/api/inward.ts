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

  addScan: (boxId: string, data: ScanCreate) =>
    apiClient
      .post<ScanCreateResponse>(`/inward/boxes/${boxId}/scans`, data)
      .then((r) => r.data),

  deleteScan: (scanId: number) =>
    apiClient.delete<ScanResponse>(`/inward/scans/${scanId}`).then((r) => r.data),
}
