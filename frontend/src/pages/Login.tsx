import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import axios from 'axios'
import { useAuth } from '@/contexts/AuthContext'
import { isOrganisationChoiceResponse } from '@/api/auth'
import { extractErrorMessage } from '@/api/client'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Label } from '@/components/ui/Label'
import { Alert } from '@/components/ui/Alert'
import { HexalogLogo } from '@/components/brand/HexalogLogo'
import { ShaderBackground } from '@/components/landing/ShaderBackground'
import { WarehouseMotifs } from '@/components/login/WarehouseMotifs'
import type { OrganisationInfo } from '@/types'
import './Login.css'

const schema = z.object({
  email: z.string().email('Enter a valid email address'),
  password: z.string().min(1, 'Password is required'),
})

type FormValues = z.infer<typeof schema>

const INVALID_CREDENTIALS_MESSAGE =
  'Unable to sign in. Please check your email and password, then try again.'

interface PendingOrganisationChoice {
  email: string
  password: string
  organisations: OrganisationInfo[]
}

export function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [serverError, setServerError] = useState('')
  const [orgChoice, setOrgChoice] = useState<PendingOrganisationChoice | null>(null)
  const [selectingOrgId, setSelectingOrgId] = useState<number | null>(null)

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
      const result = await login(data.email, data.password)
      if (isOrganisationChoiceResponse(result)) {
        setOrgChoice({
          email: data.email,
          password: data.password,
          organisations: result.organisations,
        })
        return
      }
      navigate('/dashboard')
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 401) {
        setServerError(INVALID_CREDENTIALS_MESSAGE)
      } else {
        setServerError(extractErrorMessage(err, 'Unable to sign in. Please try again.'))
      }
    }
  }

  const handleSelectOrganisation = async (organisationId: number) => {
    if (!orgChoice) return
    setServerError('')
    setSelectingOrgId(organisationId)
    try {
      const result = await login(orgChoice.email, orgChoice.password, organisationId)
      if (!isOrganisationChoiceResponse(result)) {
        navigate('/dashboard')
      }
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 401) {
        setServerError(INVALID_CREDENTIALS_MESSAGE)
      } else {
        setServerError(extractErrorMessage(err, 'Unable to sign in. Please try again.'))
      }
    } finally {
      setSelectingOrgId(null)
    }
  }

  return (
    <div className="login-hero min-h-screen flex flex-col items-center justify-center px-4">
      <ShaderBackground
        opacity={0.1}
        particles={false}
        glowIntensity={0.4}
        speed={0.85}
        purpleFirst
        richFlow
      />
      <WarehouseMotifs />

      <div className="login-content w-full max-w-sm">
        {/* Brand */}
        <div className="flex items-center justify-center gap-3 mb-8">
          <HexalogLogo width={40} height={44} />
          <div>
            <p className="text-xl font-bold text-gray-900 leading-tight">Hexalog</p>
            <p className="text-sm text-gray-500">Warehouse Tool</p>
          </div>
        </div>

        {orgChoice ? (
          <div className="login-card p-8">
            <h2 className="text-lg font-semibold text-gray-900 mb-1">Choose your organisation</h2>
            <p className="text-sm text-gray-500 mb-6">
              Your account belongs to more than one organisation. Select one to continue.
            </p>

            {serverError && (
              <Alert variant="error" className="mb-5">
                {serverError}
              </Alert>
            )}

            <div className="space-y-2">
              {orgChoice.organisations.map((organisation) => (
                <Button
                  key={organisation.id}
                  type="button"
                  variant="secondary"
                  loading={selectingOrgId === organisation.id}
                  disabled={selectingOrgId !== null}
                  onClick={() => handleSelectOrganisation(organisation.id)}
                  className="w-full justify-start"
                >
                  {organisation.name}
                </Button>
              ))}
            </div>

            <button
              type="button"
              onClick={() => setOrgChoice(null)}
              disabled={selectingOrgId !== null}
              className="mt-5 text-sm text-gray-500 hover:text-gray-700 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Use a different account
            </button>
          </div>
        ) : (
          <div className="login-card p-8">
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

              <Button type="submit" loading={isSubmitting} className="w-full mt-2">
                Sign in
              </Button>
            </form>
          </div>
        )}
      </div>
    </div>
  )
}
