const KEY = 'hxl_recent_boxes'
const MAX = 20

interface BoxEntry {
  boxId: string
  customerId: number
  addedAt: string // ISO
}

export const boxHistory = {
  getAll(): BoxEntry[] {
    try {
      return JSON.parse(localStorage.getItem(KEY) ?? '[]') as BoxEntry[]
    } catch {
      return []
    }
  },
  add(boxId: string, customerId: number) {
    const entries = boxHistory.getAll().filter((e) => e.boxId !== boxId)
    entries.unshift({ boxId, customerId, addedAt: new Date().toISOString() })
    localStorage.setItem(KEY, JSON.stringify(entries.slice(0, MAX)))
  },
  remove(boxId: string) {
    const entries = boxHistory.getAll().filter((e) => e.boxId !== boxId)
    localStorage.setItem(KEY, JSON.stringify(entries))
  },
}
