import { forwardRef } from 'react'
import { cn } from '@/lib/utils'

interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  error?: string
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ className, error, ...props }, ref) => (
    <div className="w-full">
      <input
        ref={ref}
        className={cn(
          'w-full px-3 py-2 text-sm border rounded-md bg-white placeholder-gray-400',
          'focus:outline-none focus:ring-2 focus:ring-[#8F62DF] focus:border-transparent',
          'disabled:bg-gray-50 disabled:text-gray-500',
          error ? 'border-red-400' : 'border-gray-300',
          className,
        )}
        {...props}
      />
      {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
    </div>
  ),
)
Input.displayName = 'Input'
