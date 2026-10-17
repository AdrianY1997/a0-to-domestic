import {
  ActionIcon,
  Anchor,
  Badge,
  Button,
  Card,
  Group,
  Image,
  Loader,
  Progress,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { Dropzone, type FileRejection } from '@mantine/dropzone';
import { notifications } from '@mantine/notifications';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate } from 'react-router';
import { deleteDocument, thumbUrl } from '../../api/endpoints';
import { useInfo, usePresets, useRecentDocuments } from '../../api/queries';
import { errorMessage } from '../../lib/errors';
import { useFormat } from '../../lib/useFormat';
import { useAppStore } from '../../store/app';
import { useUploads, type UploadItem } from './useUploads';

const MB = 1024 * 1024;

export function HomePage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const fmt = useFormat();
  const info = useInfo();
  const presets = usePresets();
  const recent = useRecentDocuments();
  const quickPresetId = useAppStore((s) => s.quickPresetId);
  const setQuickPresetId = useAppStore((s) => s.setQuickPresetId);

  const onUploaded = useCallback(
    (id: string, batchSize: number) => {
      // A single file goes straight to the editor; several stay listed (ING-01).
      if (batchSize === 1) {
        const qs = quickPresetId ? `?preset=${encodeURIComponent(quickPresetId)}` : '';
        void navigate(`/documents/${id}${qs}`);
      }
    },
    [navigate, quickPresetId],
  );
  const { items, upload, clearFinished } = useUploads({ onUploaded });

  const maxMb = info.data?.limits.max_upload_mb;
  const formats = useMemo(() => {
    const all = (info.data?.converters ?? [])
      .filter((c) => c.available)
      .flatMap((c) => c.formats.map((f) => f.toUpperCase()));
    return [...new Set(all)].sort();
  }, [info.data]);

  const onReject = (rejections: FileRejection[]) => {
    for (const r of rejections) {
      const tooLarge = r.errors.some((e) => e.code === 'file-too-large');
      notifications.show({
        color: 'red',
        title: r.file.name,
        message: tooLarge
          ? errorMessage(t, 'FILE_TOO_LARGE', { max_mb: maxMb })
          : errorMessage(t, 'UNSUPPORTED_FORMAT'),
      });
    }
  };

  return (
    <Stack gap="xl" maw={1000} mx="auto">
      <Stack gap={4}>
        <Title order={1}>{t('home.title')}</Title>
        <Text c="dimmed">{t('home.subtitle')}</Text>
      </Stack>

      <Dropzone
        onDrop={upload}
        onReject={onReject}
        maxSize={maxMb ? maxMb * MB : undefined}
        multiple
        aria-label={t('home.dropzone.label')}
      >
        <Stack align="center" gap={4} mih={140} justify="center">
          <Text size="lg" fw={500}>
            {t('home.dropzone.title')}
          </Text>
          <Text size="sm" c="dimmed">
            {t('home.dropzone.hint')}
            {maxMb ? ` ${t('home.dropzone.limit', { mb: fmt.number(maxMb, 0) })}` : ''}
          </Text>
          <Text size="xs" c="dimmed">
            {t('home.dropzone.keyboard')}
          </Text>
        </Stack>
      </Dropzone>

      {items.length > 0 && (
        <Stack gap="xs" aria-live="polite">
          <Group justify="space-between">
            <Title order={2} size="h4">
              {t('home.uploads.title')}
            </Title>
            <Button variant="subtle" size="xs" onClick={clearFinished}>
              {t('home.uploads.clear')}
            </Button>
          </Group>
          {items.map((item) => (
            <UploadRow key={item.key} item={item} />
          ))}
        </Stack>
      )}

      <Stack gap="xs">
        <Title order={2} size="h4">
          {t('home.presets.title')}
        </Title>
        <Text size="sm" c="dimmed">
          {t('home.presets.hint')}
        </Text>
        <Group gap="xs" role="group" aria-label={t('home.presets.title')}>
          <Button
            size="xs"
            variant={quickPresetId === null ? 'filled' : 'default'}
            aria-pressed={quickPresetId === null}
            onClick={() => setQuickPresetId(null)}
          >
            {t('home.presets.none')}
          </Button>
          {(presets.data ?? []).map((p) => (
            <Button
              key={p.id}
              size="xs"
              variant={quickPresetId === p.id ? 'filled' : 'default'}
              aria-pressed={quickPresetId === p.id}
              onClick={() => setQuickPresetId(p.id)}
            >
              {p.name}
            </Button>
          ))}
        </Group>
      </Stack>

      <Stack gap="xs">
        <Title order={2} size="h4">
          {t('home.formats.title')}
        </Title>
        {info.isLoading ? (
          <Loader size="xs" />
        ) : (
          <Group gap={6} aria-label={t('home.formats.title')}>
            {formats.map((f) => (
              <Badge key={f} variant="light" color="gray">
                {f}
              </Badge>
            ))}
          </Group>
        )}
        <Text size="xs" c="dimmed">
          {t('home.formats.more')}
        </Text>
      </Stack>

      <RecentDocuments docs={recent.data} loading={recent.isLoading} />
    </Stack>
  );
}

function UploadRow({ item }: { item: UploadItem }) {
  const { t } = useTranslation();
  const fmt = useFormat();
  return (
    <Card withBorder padding="xs">
      <Group justify="space-between" wrap="nowrap">
        <Stack gap={2} style={{ minWidth: 0, flex: 1 }}>
          <Text size="sm" truncate fw={500}>
            {item.name}
          </Text>
          {item.status === 'uploading' && (
            <Progress
              value={item.progress * 100}
              size="sm"
              aria-label={t('home.uploads.progress', { name: item.name })}
            />
          )}
          {item.status === 'error' && (
            <Text size="xs" c="red" role="alert">
              {errorMessage(t, item.errorCode, item.errorParams)}
            </Text>
          )}
          {item.status === 'done' && (
            <Text size="xs" c="dimmed">
              {t('home.uploads.done', { size: fmt.number(item.size / MB, 1) })}
            </Text>
          )}
        </Stack>
        {item.status === 'done' && item.documentId && (
          <Button component={Link} to={`/documents/${item.documentId}`} size="xs" variant="light">
            {t('home.uploads.open')}
          </Button>
        )}
      </Group>
    </Card>
  );
}

function RecentDocuments({
  docs,
  loading,
}: {
  docs: import('../../api/types').DocumentOut[] | undefined;
  loading: boolean;
}) {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const remove = useMutation({
    mutationFn: (id: string) => deleteDocument(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['documents'] }),
  });
  return (
    <Stack gap="xs">
      <Title order={2} size="h4">
        {t('home.recent.title')}
      </Title>
      {loading && <Loader size="xs" />}
      {docs && docs.length === 0 && (
        <Text size="sm" c="dimmed">
          {t('home.recent.empty')}
        </Text>
      )}
      <SimpleGrid cols={{ base: 1, sm: 2, md: 4 }}>
        {(docs ?? []).map((d) => (
          <Card key={d.id} withBorder padding="xs">
            <Stack gap={4}>
              {d.status === 'ready' ? (
                <Image src={thumbUrl(d.id, 0)} alt="" h={110} fit="contain" />
              ) : (
                <Text size="xs" c="dimmed" h={110} ta="center" pt={45}>
                  {t(`document.status.${d.status}`)}
                </Text>
              )}
              <Anchor component={Link} to={`/documents/${d.id}`} size="sm" fw={500} truncate>
                {d.name}
              </Anchor>
              <Group justify="space-between" wrap="nowrap">
                <Text size="xs" c="dimmed">
                  {new Date(d.created_at).toLocaleString(i18n.resolvedLanguage)}
                </Text>
                <ActionIcon
                  variant="subtle"
                  color="red"
                  size="sm"
                  aria-label={t('home.recent.delete', { name: d.name })}
                  onClick={() => remove.mutate(d.id)}
                >
                  ×
                </ActionIcon>
              </Group>
            </Stack>
          </Card>
        ))}
      </SimpleGrid>
    </Stack>
  );
}
