import { apiClient } from './client'
import type {
  LabelGenerateRequest,
  LabelGenerateResponse,
  OutwardBoxCreate,
  OutwardBoxResponse,
  OutwardPOPreviewResponse,
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

  previewPO: (formData: FormData) =>
    apiClient
      .post<OutwardPOPreviewResponse>('/outward/pos/preview', formData, {
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

  generateLabels: (data: LabelGenerateRequest) =>
    apiClient
      .post<LabelGenerateResponse>('/outward/labels/generate', data)
      .then((r) => r.data),

  downloadLabelsPdf: (boxIds: string[]) =>
    apiClient
      .get('/outward/labels/pdf', {
        params: { box_ids: boxIds },
        paramsSerializer: { indexes: null },
        responseType: 'blob',
      })
      .then((r) => r.data as Blob),

  reprintLabel: (boxId: string) =>
    apiClient
      .post(`/outward/boxes/${boxId}/reprint`, undefined, { responseType: 'blob' })
      .then((r) => r.data as Blob),
}
