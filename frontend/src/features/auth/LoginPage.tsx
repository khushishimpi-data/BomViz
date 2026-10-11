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
import { firstInvalidField, validateLogin, type FieldErrors, type LoginField } from './validation';

const FIELD_ORDER: readonly LoginField[] = ['email', 'password'];
const FIELD_IDS: Record<LoginField, string> = { email: 'login-email', password: 'login-password' };

export function LoginPage() {
  const { status, login } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const nextParam = searchParams.get('next');
  const destination = safeRedirectPath(nextParam) ?? DEFAULT_AFTER_LOGIN;

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [fieldErrors, setFieldErrors] = useState<FieldErrors<LoginField>>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  // Already signed in (e.g. opened /login in a second tab): go straight on. Skipped while a
  // submit is in flight so the post-login navigation below stays in charge.
  if (status === 'authenticated' && !pending) return <Navigate to={destination} replace />;

  async function handleSubmit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const errors = validateLogin({ email, password });
    setFieldErrors(errors);
    setServerError(null);
    const invalid = firstInvalidField(FIELD_ORDER, errors);
    if (invalid) {
      document.getElementById(FIELD_IDS[invalid])?.focus();
      return;
    }

    setPending(true);
    try {
      await login({ email: email.trim(), password });
      void navigate(destination, { replace: true });
    } catch (error) {
      setServerError(describeAuthError(error));
      setPending(false);
    }
  }

  const registerHref = nextParam
    ? `/register?${new URLSearchParams({ next: nextParam }).toString()}`
    : '/register';

  return (
    <div className="mx-auto w-full max-w-sm">
      <Card>
        <CardHeader>
          <CardTitle as="h1">Sign in</CardTitle>
          <CardDescription>Use the email and password you registered with.</CardDescription>
        </CardHeader>
        <CardContent>
          <form
            noValidate
            aria-label="Sign in"
            className="space-y-4"
            onSubmit={(event) => void handleSubmit(event)}
          >
            {serverError && (
              <Alert variant="destructive">
                <AlertDescription>{serverError}</AlertDescription>
              </Alert>
            )}
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
              autoComplete="current-password"
              required
              value={password}
              error={fieldErrors.password}
              onChange={(event) => {
                setPassword(event.target.value);
              }}
            />
            <Button type="submit" className="w-full" disabled={pending}>
              {pending ? 'Signing in…' : 'Sign in'}
            </Button>
          </form>
        </CardContent>
        <CardFooter className="text-sm text-muted-foreground">
          <p>
            No account yet?{' '}
            <Link
              to={registerHref}
              className="font-medium text-primary underline-offset-4 hover:underline"
            >
              Create one
            </Link>
          </p>
        </CardFooter>
      </Card>
    </div>
  );
}
