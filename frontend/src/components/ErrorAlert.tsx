import { Alert, List } from '@mantine/core';
import { useTranslation } from 'react-i18next';
import { ApiError } from '../api/client';
import { apiErrorMessage, fieldErrors } from '../lib/errors';
import { DockerLink, isDockerOnlyError } from './DockerHint';

interface Props {
  error: unknown;
  title?: string;
}

/** Shows a server error with its translated message and, for validation errors, the fields. */
export function ErrorAlert({ error, title }: Props) {
  const { t } = useTranslation();
  const fields = fieldErrors(error);
  return (
    <Alert color="red" title={title ?? t('common.error')} role="alert">
      {apiErrorMessage(t, error)}
      {error instanceof ApiError && isDockerOnlyError(error.code) && (
        <>
          {' '}
          <DockerLink />
        </>
      )}
      {fields.length > 0 && (
        <List size="xs" mt="xs">
          {fields.map((f) => (
            <List.Item key={f}>{f}</List.Item>
          ))}
        </List>
      )}
    </Alert>
  );
}
