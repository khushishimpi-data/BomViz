/** Client-side checks mirroring the server's auth schemas (the server still enforces them). */

export const PASSWORD_MIN_LENGTH = 12;
export const PASSWORD_MAX_LENGTH = 128;
export const FULL_NAME_MAX_LENGTH = 200;

export type FieldErrors<TField extends string> = Partial<Record<TField, string>>;

export type LoginField = 'email' | 'password';
export type RegisterField = 'full_name' | 'email' | 'password';

// Deliberately loose: the server's EmailStr is the authority; this only catches typos early.
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function emailError(email: string): string | undefined {
  if (email.trim() === '') return 'Enter your email address.';
  if (!EMAIL_PATTERN.test(email.trim())) {
    return 'Enter an email address like name@example.com.';
  }
  return undefined;
}

export function validateLogin(values: Record<LoginField, string>): FieldErrors<LoginField> {
  const errors: FieldErrors<LoginField> = {};
  const email = emailError(values.email);
  if (email) errors.email = email;
  if (values.password === '') errors.password = 'Enter your password.';
  else if (values.password.length > PASSWORD_MAX_LENGTH) {
    errors.password = `Passwords are at most ${PASSWORD_MAX_LENGTH} characters.`;
  }
  return errors;
}

export function validateRegister(
  values: Record<RegisterField, string>,
): FieldErrors<RegisterField> {
  const errors: FieldErrors<RegisterField> = {};
  const fullName = values.full_name.trim();
  if (fullName === '') errors.full_name = 'Enter your full name.';
  else if (fullName.length > FULL_NAME_MAX_LENGTH) {
    errors.full_name = `Use at most ${FULL_NAME_MAX_LENGTH} characters for your name.`;
  }

  const email = emailError(values.email);
  if (email) errors.email = email;

  const { password } = values;
  if (password.length < PASSWORD_MIN_LENGTH) {
    errors.password = `Use at least ${PASSWORD_MIN_LENGTH} characters for your password.`;
  } else if (password.length > PASSWORD_MAX_LENGTH) {
    errors.password = `Use at most ${PASSWORD_MAX_LENGTH} characters for your password.`;
  } else if (password.toLowerCase() === values.email.trim().toLowerCase()) {
    errors.password = 'Choose a password that is different from your email address.';
  }
  return errors;
}

/** First field (in form order) that has an error, for moving focus to it. */
export function firstInvalidField<TField extends string>(
  order: readonly TField[],
  errors: FieldErrors<TField>,
): TField | undefined {
  return order.find((field) => errors[field] !== undefined);
}
