import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { useAuth } from '@/contexts/AuthContext'
import { extractErrorMessage } from '@/api/client'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Label } from '@/components/ui/Label'
import { Alert } from '@/components/ui/Alert'
import { HexalogLogo } from '@/components/brand/HexalogLogo'

const schema = z.object({
  email: z.string().email('Enter a valid email address'),
  password: z.string().min(1, 'Password is required'),
  organisation_id: z.coerce.number().int().min(1, 'Organisation ID must be a positive number'),
})

type FormValues = z.infer<typeof schema>

export function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [serverError, setServerError] = useState('')

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
  })

  const onSubmit = async (data: FormValues) => {
    setServerError('')
    try {
      await login(data.email, data.password, data.organisation_id)
      navigate('/dashboard')
    } catch (err) {
      setServerError(extractErrorMessage(err, 'Invalid credentials. Please try again.'))
    }
  }

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col items-center justify-center px-4">
      <div className="w-full max-w-sm">
        {/* Brand */}
        <div className="flex items-center justify-center gap-3 mb-8">
          <HexalogLogo width={40} height={44} />
          <div>
            <p className="text-xl font-bold text-gray-900 leading-tight">Hexalog</p>
            <p className="text-sm text-gray-500">Warehouse Tool</p>
          </div>
        </div>

        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-8">
          <h2 className="text-lg font-semibold text-gray-900 mb-1">Sign in</h2>
          <p className="text-sm text-gray-500 mb-6">Enter your workspace credentials to continue.</p>

          {serverError && (
            <Alert variant="error" className="mb-5">
              {serverError}
            </Alert>
          )}

          <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <div>
              <Label htmlFor="email" required>
                Email address
              </Label>
              <Input
                id="email"
                type="email"
                placeholder="you@company.com"
                autoComplete="email"
                autoFocus
                {...register('email')}
                error={errors.email?.message}
              />
            </div>

            <div>
              <Label htmlFor="password" required>
                Password
              </Label>
              <Input
                id="password"
                type="password"
                placeholder="••••••••"
                autoComplete="current-password"
                {...register('password')}
                error={errors.password?.message}
              />
            </div>

            <div>
              <Label htmlFor="organisation_id" required>
                Organisation ID
              </Label>
              <Input
                id="organisation_id"
                type="number"
                placeholder="1"
                min={1}
                {...register('organisation_id')}
                error={errors.organisation_id?.message}
              />
              <p className="mt-1 text-xs text-gray-400">Provided by your administrator.</p>
            </div>

            <Button type="submit" loading={isSubmitting} className="w-full mt-2">
              Sign in
            </Button>
          </form>
        </div>
      </div>
    </div>
  )
}
