import { AlertCircle, CheckCircle, Info, XCircle } from 'lucide-react'
import { cn } from '@/lib/utils'

type AlertVariant = 'info' | 'success' | 'warning' | 'error'

interface AlertProps {
  variant?: AlertVariant
  title?: string
  children: React.ReactNode
  className?: string
}

const config: Record<AlertVariant, { icon: React.ElementType; style: string }> = {
  info:    { icon: Info,        style: 'bg-blue-50   border-blue-200   text-blue-800'   },
  success: { icon: CheckCircle, style: 'bg-green-50  border-green-200  text-green-800'  },
  warning: { icon: AlertCircle, style: 'bg-yellow-50 border-yellow-200 text-yellow-800' },
  error:   { icon: XCircle,     style: 'bg-red-50    border-red-200    text-red-800'    },
}

export function Alert({ variant = 'info', title, children, className }: AlertProps) {
  const { icon: Icon, style } = config[variant]
  return (
    <div className={cn('flex gap-3 p-4 rounded-md border text-sm', style, className)}>
      <Icon className="h-5 w-5 flex-shrink-0 mt-0.5" />
      <div>
        {title && <p className="font-semibold mb-0.5">{title}</p>}
        <div>{children}</div>
      </div>
    </div>
  )
}
