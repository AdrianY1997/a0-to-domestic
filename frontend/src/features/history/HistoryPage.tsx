import {
  Badge,
  Button,
  Group,
  Pagination,
  Stack,
  Table,
  Tabs,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate } from 'react-router';
import { apiList } from '../../api/client';
import { createJob, deleteDocument, outputUrl } from '../../api/endpoints';
import type { DocumentOut, JobOut } from '../../api/types';
import { ErrorAlert } from '../../components/ErrorAlert';
import { useFormat } from '../../lib/useFormat';
import { useEditorStore } from '../../store/editor';

const PAGE_SIZE = 20;

const isExpired = (iso: string) => new Date(iso).getTime() <= Date.now();

export default function HistoryPage() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<string | null>('documents');
  const [search, setSearch] = useState('');
  const [q] = useDebouncedValue(search.trim(), 250);
  return (
    <Stack maw={1100} mx="auto" gap="md">
      <Title order={1}>{t('history.title')}</Title>
      <TextInput
        aria-label={t('history.search')}
        placeholder={t('history.search')}
        value={search}
        onChange={(e) => setSearch(e.currentTarget.value)}
        w={320}
      />
      <Tabs value={tab} onChange={setTab} keepMounted={false}>
        <Tabs.List>
          <Tabs.Tab value="documents">{t('history.documents')}</Tabs.Tab>
          <Tabs.Tab value="jobs">{t('history.jobs')}</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="documents" pt="md">
          <DocumentsTab q={q} />
        </Tabs.Panel>
        <Tabs.Panel value="jobs" pt="md">
          <JobsTab q={q} />
        </Tabs.Panel>
      </Tabs>
    </Stack>
  );
}

function usePaged<T>(kind: 'documents' | 'jobs', q: string) {
  const [page, setPage] = useState(1);
  const [lastQ, setLastQ] = useState(q);
  if (lastQ !== q) {
    setLastQ(q);
    setPage(1);
  }
  const query = useQuery({
    queryKey: ['history', kind, q, page],
    queryFn: () =>
      apiList<T>(`/${kind}`, {
        q: q || undefined,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
        ...(kind === 'jobs' ? { kind: 'render' } : {}),
      }),
    placeholderData: keepPreviousData,
  });
  return {
    query,
    page,
    setPage,
    pages: Math.max(1, Math.ceil((query.data?.total ?? 0) / PAGE_SIZE)),
  };
}

function DocumentsTab({ q }: { q: string }) {
  const { t } = useTranslation();
  const fmt = useFormat();
  const qc = useQueryClient();
  const { query, page, setPage, pages } = usePaged<DocumentOut>('documents', q);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const remove = useMutation({
    mutationFn: deleteDocument,
    onSuccess: () => {
      setConfirmId(null);
      return qc.invalidateQueries({ queryKey: ['history'] });
    },
  });
  const items = query.data?.items ?? [];
  return (
    <Stack>
      {query.isError && <ErrorAlert error={query.error} />}
      {remove.isError && <ErrorAlert error={remove.error} />}
      {query.isSuccess && items.length === 0 && <Text c="dimmed">{t('history.empty')}</Text>}
      {items.length > 0 && (
        <Table withTableBorder aria-label={t('history.documents')}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t('history.colName')}</Table.Th>
              <Table.Th>{t('history.colStatus')}</Table.Th>
              <Table.Th>{t('history.colCreated')}</Table.Th>
              <Table.Th>{t('history.colExpires')}</Table.Th>
              <Table.Th>{t('history.colActions')}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {items.map((d) => {
              const expired = isExpired(d.expires_at);
              return (
                <Table.Tr key={d.id}>
                  <Table.Td style={{ wordBreak: 'break-all' }}>{d.name}</Table.Td>
                  <Table.Td>
                    <Badge variant="light">{t(`document.status.${d.status}`, d.status)}</Badge>
                  </Table.Td>
                  <Table.Td>{fmt.dateTime(d.created_at)}</Table.Td>
                  <Table.Td>{fmt.dateTime(d.expires_at)}</Table.Td>
                  <Table.Td>
                    <Group gap="xs">
                      <Button
                        component={Link}
                        to={`/documents/${d.id}`}
                        size="xs"
                        variant="default"
                        disabled={expired}
                      >
                        {t('history.reopen')}
                      </Button>
                      {confirmId === d.id ? (
                        <>
                          <Button
                            size="xs"
                            color="red"
                            loading={remove.isPending}
                            onClick={() => remove.mutate(d.id)}
                          >
                            {t('history.confirmDelete')}
                          </Button>
                          <Button size="xs" variant="default" onClick={() => setConfirmId(null)}>
                            {t('common.cancel')}
                          </Button>
                        </>
                      ) : (
                        <Button
                          size="xs"
                          color="red"
                          variant="light"
                          onClick={() => setConfirmId(d.id)}
                        >
                          {t('history.delete')}
                        </Button>
                      )}
                    </Group>
                  </Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      )}
      {pages > 1 && <Pagination value={page} onChange={setPage} total={pages} />}
    </Stack>
  );
}

function JobsTab({ q }: { q: string }) {
  const { t } = useTranslation();
  const fmt = useFormat();
  const navigate = useNavigate();
  const { query, page, setPage, pages } = usePaged<JobOut>('jobs', q);
  // Document names for the rows (the job list only carries ids).
  const names = useQuery({
    queryKey: ['history', 'names'],
    queryFn: () => apiList<DocumentOut>('/documents', { limit: 200 }),
  });
  const nameOf = (id: string) => names.data?.items.find((d) => d.id === id)?.name ?? '…';

  const regenerate = useMutation({
    mutationFn: (job: JobOut) => {
      if (!job.settings) throw new Error('no settings');
      return createJob({ settings: job.settings, test_sheet_only: job.test_sheet_only });
    },
    onSuccess: (created) => void navigate(`/jobs/${created.id}`),
  });
  const reopen = (job: JobOut) => {
    // Load the job settings into the editor of its document, then open it.
    useEditorStore.getState().applyPreset(job.document_id, null, job.settings ?? {});
    void navigate(`/documents/${job.document_id}`);
  };

  const items = query.data?.items ?? [];
  return (
    <Stack>
      {query.isError && <ErrorAlert error={query.error} />}
      {regenerate.isError && <ErrorAlert error={regenerate.error} />}
      {query.isSuccess && items.length === 0 && <Text c="dimmed">{t('history.empty')}</Text>}
      {items.length > 0 && (
        <Table withTableBorder aria-label={t('history.jobs')}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t('history.colName')}</Table.Th>
              <Table.Th>{t('history.colStatus')}</Table.Th>
              <Table.Th>{t('history.colCreated')}</Table.Th>
              <Table.Th>{t('history.colOutputs')}</Table.Th>
              <Table.Th>{t('history.colActions')}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {items.map((j) => {
              const expired = isExpired(j.expires_at);
              return (
                <Table.Tr key={j.id}>
                  <Table.Td style={{ wordBreak: 'break-all' }}>
                    <Link to={`/jobs/${j.id}`}>{nameOf(j.document_id)}</Link>
                    {j.test_sheet_only && (
                      <Badge ml="xs" variant="light" color="gray">
                        {t('history.testSheet')}
                      </Badge>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Badge variant="light">{t(`history.jobStatus.${j.status}`)}</Badge>
                  </Table.Td>
                  <Table.Td>{fmt.dateTime(j.created_at)}</Table.Td>
                  <Table.Td>
                    <Stack gap={2}>
                      {j.outputs.map((o) =>
                        expired ? (
                          <Text size="xs" key={o.name} c="dimmed">
                            {o.name}
                          </Text>
                        ) : (
                          <a
                            key={o.name}
                            href={outputUrl(j.id, o.name)}
                            download={o.name}
                            style={{ fontSize: 12, wordBreak: 'break-all' }}
                          >
                            {t('history.download', { name: o.name })}
                          </a>
                        ),
                      )}
                    </Stack>
                  </Table.Td>
                  <Table.Td>
                    <Group gap="xs">
                      <Button
                        size="xs"
                        variant="default"
                        disabled={expired || !j.settings}
                        onClick={() => reopen(j)}
                      >
                        {t('history.reopen')}
                      </Button>
                      <Button
                        size="xs"
                        variant="light"
                        disabled={expired || !j.settings}
                        loading={regenerate.isPending && regenerate.variables?.id === j.id}
                        onClick={() => regenerate.mutate(j)}
                      >
                        {t('history.regenerate')}
                      </Button>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      )}
      {pages > 1 && <Pagination value={page} onChange={setPage} total={pages} />}
    </Stack>
  );
}
