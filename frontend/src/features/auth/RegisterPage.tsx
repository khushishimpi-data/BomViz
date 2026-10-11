import { useState, type SubmitEvent } from 'react';
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';

import { describeAuthError } from './errors';
import { FormField } from './FormField';
import { DEFAULT_AFTER_LOGIN, safeRedirectPath } from './redirect';
import { useAuth } from './useAuth';
import {
  firstInvalidField,
  PASSWORD_MAX_LENGTH,
  PASSWORD_MIN_LENGTH,
  validateRegister,
  type FieldErrors,
  type RegisterField,
} from './validation';

const FIELD_ORDER: readonly RegisterField[] = ['full_name', 'email', 'password'];
const FIELD_IDS: Record<RegisterField, string> = {
  full_name: 'register-full-name',
  email: 'register-email',
  password: 'register-password',
};

export function RegisterPage() {
  const { status, register } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const nextParam = searchParams.get('next');
  const destination = safeRedirectPath(nextParam) ?? DEFAULT_AFTER_LOGIN;

  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [fieldErrors, setFieldErrors] = useState<FieldErrors<RegisterField>>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  if (status === 'authenticated' && !pending) return <Navigate to={destination} replace />;

  async function handleSubmit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const values = { full_name: fullName, email, password };
    const errors = validateRegister(values);
    setFieldErrors(errors);
    setServerError(null);
    const invalid = firstInvalidField(FIELD_ORDER, errors);
    if (invalid) {
      document.getElementById(FIELD_IDS[invalid])?.focus();
      return;
    }

    setPending(true);
    try {
      await register({ full_name: fullName.trim(), email: email.trim(), password });
      void navigate(destination, { replace: true });
    } catch (error) {
      setServerError(describeAuthError(error));
      setPending(false);
    }
  }

  const loginHref = nextParam
    ? `/login?${new URLSearchParams({ next: nextParam }).toString()}`
    : '/login';

  return (
    <div className="mx-auto w-full max-w-sm">
      <Card>
        <CardHeader>
          <CardTitle as="h1">Create your account</CardTitle>
          <CardDescription>You will be signed in as soon as the account exists.</CardDescription>
        </CardHeader>
        <CardContent>
          <form
            noValidate
            aria-label="Create account"
            className="space-y-4"
            onSubmit={(event) => void handleSubmit(event)}
          >
            {serverError && (
              <Alert variant="destructive">
                <AlertDescription>{serverError}</AlertDescription>
              </Alert>
            )}
            <FormField
              id={FIELD_IDS.full_name}
              label="Full name"
              name="full_name"
              autoComplete="name"
              required
              value={fullName}
              error={fieldErrors.full_name}
              onChange={(event) => {
                setFullName(event.target.value);
              }}
            />
            <FormField
              id={FIELD_IDS.email}
              label="Email"
              type="email"
              name="email"
              autoComplete="email"
              required
              value={email}
              error={fieldErrors.email}
              onChange={(event) => {
                setEmail(event.target.value);
              }}
            />
            <FormField
              id={FIELD_IDS.password}
              label="Password"
              type="password"
              name="password"
              autoComplete="new-password"
              required
              minLength={PASSWORD_MIN_LENGTH}
              hint={`${PASSWORD_MIN_LENGTH}–${PASSWORD_MAX_LENGTH} characters, different from your email.`}
              value={password}
              error={fieldErrors.password}
              onChange={(event) => {
                setPassword(event.target.value);
              }}
            />
            <Button type="submit" className="w-full" disabled={pending}>
              {pending ? 'Creating account…' : 'Create account'}
            </Button>
          </form>
        </CardContent>
        <CardFooter className="text-sm text-muted-foreground">
          <p>
            Already registered?{' '}
            <Link
              to={loginHref}
              className="font-medium text-primary underline-offset-4 hover:underline"
            >
              Sign in
            </Link>
          </p>
        </CardFooter>
      </Card>
    </div>
  );
}
