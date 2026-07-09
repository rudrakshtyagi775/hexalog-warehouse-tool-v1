import { cn } from '@/lib/utils'

export function Table({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div className="overflow-x-auto">
      <table className={cn('min-w-full divide-y divide-gray-200 text-sm', className)}>
        {children}
      </table>
    </div>
  )
}

export function Thead({ children }: { children: React.ReactNode }) {
  return <thead className="bg-gray-50">{children}</thead>
}

export function Th({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <th
      className={cn(
        'px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider',
        className,
      )}
    >
      {children}
    </th>
  )
}

export function Tbody({ children }: { children: React.ReactNode }) {
  return <tbody className="bg-white divide-y divide-gray-100">{children}</tbody>
}

export function Tr({
  children,
  className,
  onClick,
}: {
  children: React.ReactNode
  className?: string
  onClick?: () => void
}) {
  return (
    <tr
      onClick={onClick}
      className={cn(onClick && 'cursor-pointer hover:bg-gray-50 transition-colors', className)}
    >
      {children}
    </tr>
  )
}

export function Td({ children, className }: { children: React.ReactNode; className?: string }) {
  return <td className={cn('px-4 py-3 text-gray-700', className)}>{children}</td>
}

export function EmptyRow({ cols, message = 'No data found.' }: { cols: number; message?: string }) {
  return (
    <tr>
      <td colSpan={cols} className="px-4 py-12 text-center text-sm text-gray-400">
        {message}
      </td>
    </tr>
  )
}
