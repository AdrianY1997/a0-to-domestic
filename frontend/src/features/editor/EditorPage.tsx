import { Alert, Button, Group, Loader, Progress, Stack, Text, Title } from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import { createJob, documentEventsUrl, previewUrl } from '../../api/endpoints';
import { useCadInfo, useDocument, useInfo, usePlan, usePresets } from '../../api/queries';
import { useAssetUrl } from '../../api/assets';
import { useEventStream } from '../../api/sse';
import type { DocumentOut } from '../../api/types';
import { DockerLink, isDockerOnlyError } from '../../components/DockerHint';
import { ErrorAlert } from '../../components/ErrorAlert';
import { errorMessage } from '../../lib/errors';
import { progressMessage } from '../../lib/progress';
import { toJobSettings } from '../../lib/settings';
import { useDocEditor, useEditorStore } from '../../store/editor';
import { CAD_FORMATS, CadOptionsDialog } from './CadOptionsDialog';
import { DocumentPanel } from './DocumentPanel';
import { PasswordDialog, PhysicalSizeDialog, SheetPreviewModal } from './dialogs';
import type { PageImage } from './PlanOverlay';
import { PreviewPane } from './PreviewPane';
import { clamp, normalizeOnlySheets } from '../../lib/tiling';
import { AlternativesPanel } from './AlternativesPanel';
import type { PreviewTool } from './PlanOverlay';
import { SettingsPanel } from './SettingsPanel';
import { TilingTools } from './TilingTools';
import { SummaryBar } from './SummaryBar';
import { collectWarnings } from './warnings';
import './editor.css';

export default function EditorPage() {
  const { id = '' } = useParams();
  const { t } = useTranslation();
  const qc = useQueryClient();
  const doc = useDocument(id);
  const processing = doc.data?.status === 'processing';
  const sse = useEventStream(documentEventsUrl(id), processing);

  // The terminal SSE event means the document changed: refresh it right away.
  useEffect(() => {
    if (sse.done || sse.error) void qc.invalidateQueries({ queryKey: ['document', id] });
  }, [sse.done, sse.error, qc, id]);

  if (doc.isLoading) {
    return (
      <Group justify="center" p="xl">
        <Loader />
      </Group>
    );
  }
  if (doc.isError || !doc.data) {
    return (
      <Stack maw={600} mx="auto">
        <ErrorAlert error={doc.error} />
        <Button component={Link} to="/" variant="default" w="fit-content">
          {t('common.backHome')}
        </Button>
      </Stack>
    );
  }
  const data = doc.data;
  switch (data.status) {
    case 'processing':
      return <ProcessingView doc={data} progress={sse.progress} />;
    case 'password_required':
      return (
        <>
          <ProcessingView doc={data} progress={null} waiting />
          <PasswordDialog documentId={id} invalid={data.error_code === 'PASSWORD_INVALID'} />
        </>
      );
    case 'failed':
      return <FailedView doc={data} />;
    default:
      return <ReadyEditor doc={data} />;
  }
}

function ProcessingView({
  doc,
  progress,
  waiting = false,
}: {
  doc: DocumentOut;
  progress: import('../../api/types').ProgressEvent | null;
  waiting?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <Stack maw={560} mx="auto" gap="sm" aria-live="polite">
      <Title order={1} size="h3">
        {doc.name}
      </Title>
      <Text>{waiting ? t('password.message') : progressMessage(t, progress)}</Text>
      {!waiting && (
        <Progress
          value={progress?.pct ?? 0}
          animated={!progress}
          aria-label={t('document.status.processing')}
        />
      )}
    </Stack>
  );
}

function FailedView({ doc }: { doc: DocumentOut }) {
  const { t } = useTranslation();
  const info = useInfo();
  const formats = [...new Set((info.data?.converters ?? []).flatMap((c) => c.formats))]
    .sort()
    .join(', ');
  return (
    <Stack maw={600} mx="auto" gap="sm">
      <Title order={1} size="h3">
        {doc.name}
      </Title>
      <Alert color="red" title={t('document.failed')} role="alert">
        {errorMessage(t, doc.error_code, doc.error_params)}
        {isDockerOnlyError(doc.error_code) && (
          <>
            {' '}
            <DockerLink />
          </>
        )}
        {doc.error_code === 'UNSUPPORTED_FORMAT' && formats && (
          <Text size="sm" mt="xs">
            {t('document.supportedFormats', { formats })}
          </Text>
        )}
      </Alert>
      <Button component={Link} to="/" w="fit-content">
        {t('common.backHome')}
      </Button>
    </Stack>
  );
}

function ReadyEditor({ doc }: { doc: DocumentOut }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const info = useInfo();
  const presets = usePresets();
  const editor = useDocEditor(doc.id);
  const { patchSettings, setPageIndex, applyPreset } = useEditorStore.getState();

  const pageIndex = Math.min(editor.pageIndex, Math.max(doc.pages.length - 1, 0));
  const page = doc.pages[pageIndex];
  const settings = useMemo(() => toJobSettings(doc.id, editor.settings), [doc.id, editor.settings]);

  // `?preset=` comes from the home page quick start; it only applies to a fresh document.
  const presetParam = searchParams.get('preset');
  useEffect(() => {
    if (!presetParam) return;
    const known = useEditorStore.getState().docs[doc.id];
    const preset = presets.data?.find((p) => p.id === presetParam);
    if (presets.isSuccess || known) {
      if (preset && !known) applyPreset(doc.id, preset.id, preset.settings ?? {});
      setSearchParams({}, { replace: true });
    }
  }, [presetParam, presets.data, presets.isSuccess, doc.id, applyPreset, setSearchParams]);

  const [altOpen, setAltOpen] = useState(false);
  const [tool, setTool] = useState<PreviewTool>('pan');
  const planQ = usePlan({
    settings,
    pageIndex,
    presetId: editor.presetId,
    enabled: !!page,
    includeAlternatives: altOpen,
  });
  const plan = planQ.data;
  const warnings = collectWarnings(doc, page, plan);

  // ANA-03: ask what size an image without reliable physical size represents (once per page).
  const [sizeOpen, setSizeOpen] = useState(false);
  const asked = useRef(new Set<string>());
  const assumed = !!page?.warnings?.some((w) => w.code === 'PHYSICAL_SIZE_ASSUMED');
  useEffect(() => {
    const key = `${doc.id}:${pageIndex}`;
    if (assumed && !asked.current.has(key)) {
      asked.current.add(key);
      setSizeOpen(true);
    }
  }, [assumed, doc.id, pageIndex]);

  const [sheetId, setSheetId] = useState<string | null>(null);

  // DXF / DWG: layout, units, scale and colour policy (SPEC 7.1).
  const isCad = CAD_FORMATS.includes(doc.format ?? '');
  const cad = useCadInfo(doc.id, isCad);
  const [cadOpen, setCadOpen] = useState(false);

  const job = useMutation({
    mutationFn: (testSheetOnly: boolean) =>
      createJob({
        settings,
        preset_id: editor.presetId,
        test_sheet_only: testSheetOnly,
      }),
    onSuccess: (created) => void navigate(`/jobs/${created.id}`),
  });

  // While a placeholder (previous) plan is drawn, keep the box/rotation that plan was computed
  // with, so the image never mismatches the overlay.
  const appliedRef = useRef({
    box: editor.settings.box,
    rotate: editor.settings.rotate,
    mirror: editor.settings.mirror,
  });
  if (!planQ.isPlaceholderData) {
    appliedRef.current = {
      box: editor.settings.box,
      rotate: editor.settings.rotate,
      mirror: editor.settings.mirror,
    };
  }
  const { box: drawnBox, rotate: drawnRotate, mirror: drawnMirror } = appliedRef.current;
  const hiddenKey = editor.settings.hiddenLayers.join(',');
  const visibleKey = editor.settings.visibleLayers.join(',');

  const pageImage: PageImage | undefined = useMemo(() => {
    if (!page) return undefined;
    const maxPx = Math.min(2400, info.data?.limits.preview_max_px ?? 2400);
    const box = page.boxes_mm[drawnBox] ?? page.boxes_mm.crop;
    return {
      url: previewUrl(doc.id, page.index, maxPx, `${page.width_mm}x${page.height_mm}`, {
        hiddenLayers: hiddenKey ? hiddenKey.split(',') : [],
        visibleLayers: visibleKey ? visibleKey.split(',') : [],
      }),
      widthMm: page.width_mm,
      heightMm: page.height_mm,
      box: (box as [number, number, number, number] | undefined) ?? [
        0,
        0,
        page.width_mm,
        page.height_mm,
      ],
      rotate: drawnRotate,
      mirror: drawnMirror,
    };
  }, [page, doc.id, info.data, drawnBox, drawnRotate, drawnMirror, hiddenKey, visibleKey]);
  // Local mode renders the page in the worker: the URL becomes a blob URL once it is ready.
  const imageUrl = useAssetUrl(pageImage?.url, true);
  const image = useMemo(
    () => (pageImage && imageUrl ? { ...pageImage, url: imageUrl } : undefined),
    [pageImage, imageUrl],
  );

  if (!page) {
    return <Alert color="red">{t('errors.UNKNOWN')}</Alert>;
  }

  const patch = (p: Parameters<typeof patchSettings>[1]) => patchSettings(doc.id, p);
  const offsetRange = plan?.offset_range_mm;
  const offset = {
    x: offsetRange ? clamp(editor.settings.offsetXMm, ...offsetRange.x) : 0,
    y: offsetRange ? clamp(editor.settings.offsetYMm, ...offsetRange.y) : 0,
  };
  const jobError = job.error;

  return (
    <div className="editor-root">
      <aside className="editor-doc">
        <DocumentPanel
          doc={doc}
          page={page}
          pageIndex={pageIndex}
          onPageChange={(i) => setPageIndex(doc.id, i)}
          plan={plan}
          warnings={warnings}
          onPatch={patch}
          onSetPhysicalSize={() => setSizeOpen(true)}
          onCadOptions={isCad ? () => setCadOpen(true) : undefined}
          onOpenSheet={setSheetId}
        />
      </aside>
      <section className="editor-preview" aria-label={t('editor.preview')}>
        <PreviewPane
          plan={plan}
          image={image}
          loading={planQ.isFetching}
          onOpenSheet={setSheetId}
          tool={tool}
          onToolChange={setTool}
          onToggleSheet={(sid) => {
            if (!plan) return;
            // Read the live store value: the `plan` prop lags behind rapid clicks.
            const cur = useEditorStore.getState().docs[doc.id]?.settings.onlySheets;
            const ids = new Set(cur ?? plan.sheets.map((s) => s.id));
            if (ids.has(sid)) ids.delete(sid);
            else ids.add(sid);
            patch({ onlySheets: normalizeOnlySheets([...ids], plan.sheets) });
          }}
          offset={offset}
          onOffsetChange={(o) => patch({ offsetXMm: o.x, offsetYMm: o.y })}
          onRegionDrawn={(r) => {
            patch({ regionMm: r });
            setTool('pan');
          }}
        />
      </section>
      <aside className="editor-settings">
        <SettingsPanel
          settings={editor.settings}
          onPatch={patch}
          presetId={editor.presetId}
          onPreset={(presetId, patchJson) => {
            if (presetId) applyPreset(doc.id, presetId, patchJson);
            else useEditorStore.getState().resetSettings(doc.id);
          }}
          pageCount={doc.page_count}
          page={page}
          scaleLabel={plan?.scale_label}
          scaleFactor={plan?.scale}
        />
        <Stack gap="md" mt="md">
          <TilingTools
            settings={editor.settings}
            plan={plan}
            onPatch={patch}
            onToolChange={setTool}
          />
          <AlternativesPanel
            plan={plan}
            open={altOpen}
            onToggle={() => setAltOpen((o) => !o)}
            onPatch={patch}
          />
        </Stack>
      </aside>
      <div className="editor-summary">
        <SummaryBar
          plan={plan}
          planError={planQ.error}
          fetching={planQ.isFetching}
          busy={job.isPending}
          jobError={jobError}
          onTestSheet={() => job.mutate(true)}
          onGenerate={() => job.mutate(false)}
        />
      </div>
      <PhysicalSizeDialog
        documentId={doc.id}
        pageIndex={pageIndex}
        opened={sizeOpen}
        onClose={() => setSizeOpen(false)}
      />
      {isCad && (
        <CadOptionsDialog
          documentId={doc.id}
          info={cad.data}
          opened={cadOpen}
          onClose={() => setCadOpen(false)}
        />
      )}
      <SheetPreviewModal
        sheetId={sheetId}
        settings={settings}
        pageIndex={pageIndex}
        onClose={() => setSheetId(null)}
      />
    </div>
  );
}
