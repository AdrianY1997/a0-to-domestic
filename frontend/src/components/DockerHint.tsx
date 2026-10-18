import { Anchor } from '@mantine/core';
import { useTranslation } from 'react-i18next';
import { DOCKER_ONLY_CODES, README_URL, isLocalMode } from '../lib/capabilities';

/** True when `code` means, in the web version, "this format needs the Docker version". */
export const isDockerOnlyError = (code: string | null | undefined): boolean =>
  !!code && isLocalMode() && DOCKER_ONLY_CODES.has(code);

/** Link to the README section on the Docker version (shown next to unsupported formats). */
export function DockerLink() {
  const { t } = useTranslation();
  return (
    <Anchor href={README_URL} target="_blank" rel="noopener noreferrer" size="sm">
      {t('local.dockerLink')}
    </Anchor>
  );
}
