import { Link } from 'react-router-dom'
import { Package, ScanLine, BarChart2 } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { HexalogLogo } from '@/components/brand/HexalogLogo'

const features = [
  { icon: Package,   label: 'Inward Management',  desc: 'Track POs, create boxes, scan items with full audit trail.' },
  { icon: ScanLine,  label: 'Real-Time Scanning',  desc: 'EAN barcode scanning with automatic FIFO PO allocation.'  },
  { icon: BarChart2, label: 'Reports & Ledger',    desc: 'Inventory ledger entries and submission summaries.'       },
]

export function LandingPage() {
  return (
    <div className="min-h-screen bg-slate-900 flex flex-col">
      <div className="flex-1 flex flex-col items-center justify-center px-4 text-center">
        <div className="mb-6">
          <HexalogLogo width={64} height={71} />
        </div>
        <h1 className="text-4xl font-bold text-white mb-3">Hexalog Warehouse Tool</h1>
        <p className="text-slate-400 text-lg mb-10 max-w-lg">
          End-to-end inward &amp; outward logistics management — built for warehouse teams.
        </p>

        <Link to="/login">
          <Button size="lg">Sign in to your workspace</Button>
        </Link>

        <div className="mt-16 grid grid-cols-1 sm:grid-cols-3 gap-6 max-w-3xl w-full">
          {features.map(({ icon: Icon, label, desc }) => (
            <div key={label} className="bg-slate-800 rounded-lg p-5 text-left">
              <Icon className="h-6 w-6 text-blue-400 mb-3" />
              <p className="text-white font-semibold text-sm mb-1">{label}</p>
              <p className="text-slate-400 text-xs leading-relaxed">{desc}</p>
            </div>
          ))}
        </div>
      </div>

      <footer className="py-6 text-center text-slate-600 text-xs">
        Hexalog Warehouse Tool v1.0 — Warehouse management for modern operations
      </footer>
    </div>
  )
}
