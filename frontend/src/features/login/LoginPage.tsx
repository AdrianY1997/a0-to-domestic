import { Button, Card, PasswordInput, Stack, Text, Title } from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Navigate, useNavigate } from 'react-router';
import { SESSION_KEY, login, useSession } from '../../api/auth';
import { ApiError } from '../../api/client';
import { ErrorAlert } from '../../components/ErrorAlert';

export default function LoginPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const session = useSession();
  const [password, setPassword] = useState('');
  const mutation = useMutation({
    mutationFn: login,
    onSuccess: (s) => {
      qc.setQueryData(SESSION_KEY, s);
      void qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== 'session' });
      void navigate('/', { replace: true });
    },
  });

  if (session.data && (session.data.auth_mode !== 'password' || session.data.authenticated)) {
    return <Navigate to="/" replace />;
  }

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (password) mutation.mutate(password);
  };
  const error = mutation.error;
  const wrong = error instanceof ApiError && error.code === 'INVALID_CREDENTIALS';
  const limited = error instanceof ApiError && error.code === 'RATE_LIMITED';

  return (
    <Card withBorder maw={400} mx="auto" mt="xl" component="form" onSubmit={submit}>
      <Stack>
        <Title order={1} size="h3">
          {t('login.title')}
        </Title>
        <Text size="sm" c="dimmed">
          {t('login.intro')}
        </Text>
        <PasswordInput
          label={t('login.password')}
          value={password}
          onChange={(e) => setPassword(e.currentTarget.value)}
          autoComplete="current-password"
          data-autofocus
          required
        />
        {wrong && (
          <Text c="red" size="sm" role="alert">
            {t('login.wrong')}
          </Text>
        )}
        {limited && (
          <Text c="red" size="sm" role="alert">
            {t('login.rateLimited')}
          </Text>
        )}
        {error && !wrong && !limited && <ErrorAlert error={error} />}
        <Button type="submit" loading={mutation.isPending}>
          {t('login.submit')}
        </Button>
      </Stack>
    </Card>
  );
}
