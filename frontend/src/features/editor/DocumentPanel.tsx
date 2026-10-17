import {
  Badge,
  Button,
  Collapse,
  Group,
  Select,
  Stack,
  Text,
  Title,
  UnstyledButton,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { useTranslation } from 'react-i18next';
import type { DocumentOut, Page, Plan, PlanWarning } from '../../api/types';
import { useFormat } from '../../lib/useFormat';
import type { EditorSettings } from '../../lib/settings';
import { WarningsList } from './WarningsList';

interface Props {
  doc: DocumentOut;
  page: Page;
  pageIndex: number;
  onPageChange: (index: number) => void;
  plan: Plan | undefined;
  warnings: PlanWarning[];
  onPatch: (patch: Partial<EditorSettings>) => void;
  onSetPhysicalSize: () => void;
  /** DXF / DWG only: opens the layout, units, scale and colour dialog. */
  onCadOptions?: () => void;
  onOpenSheet: (id: string) => void;
}

export function DocumentPanel({
  doc,
  page,
  pageIndex,
  onPageChange,
  plan,
  warnings,
  onPatch,
  onSetPhysicalSize,
  onCadOptions,
  onOpenSheet,
}: Props) {
  const { t } = useTranslation();
  const fmt = useFormat();
  const [sheetsOpen, { toggle }] = useDisclosure(false);
  const std = page.standard_match;
  const detected = std
    ? `${std.id} ${t(`orientation.${std.orientation}`)}`
    : t('editor.document.nonStandard');

  return (
    <Stack gap="sm" component="section" aria-label={t('editor.document.title')}>
      <Title order={2} size="h5">
        {t('editor.document.title')}
      </Title>
      <Text fw={600} style={{ wordBreak: 'break-word' }}>
        {doc.name}
      </Text>
      <Stack gap={2}>
        <Text size="sm">{detected}</Text>
        <Text size="sm" c="dimmed">
          {fmt.size(page.width_mm, page.height_mm)}
        </Text>
        <Group gap={6}>
          <Badge variant="light">{t(`document.kind.${page.kind}`)}</Badge>
          {page.dpi ? (
            <Badge variant="light" color="gray">
              {t('editor.document.dpi', { dpi: fmt.number(page.dpi, 0) })}
            </Badge>
          ) : null}
        </Group>
      </Stack>

      {doc.page_count > 1 && (
        <Select
          label={t('editor.document.page')}
          size="xs"
          allowDeselect={false}
          value={String(pageIndex)}
          onChange={(v) => v !== null && onPageChange(Number(v))}
          data={doc.pages.map((p) => ({
            value: String(p.index),
            label: t('editor.document.pageOf', {
              n: p.index + 1,
              total: doc.page_count,
              size: fmt.size(p.width_mm, p.height_mm, 0),
            }),
          }))}
        />
      )}
      {onCadOptions && (
        <Button
          size="compact-xs"
          variant="default"
          onClick={onCadOptions}
          data-testid="cad-options"
        >
          {t('cad.open')}
        </Button>
      )}
      {page.kind === 'raster' && (
        <Button size="compact-xs" variant="default" onClick={onSetPhysicalSize}>
          {t('editor.document.setSize')}
        </Button>
      )}

      <Title order={3} size="h6">
        {t('editor.warnings.title', { count: warnings.length })}
      </Title>
      <WarningsList
        documentId={doc.id}
        warnings={warnings}
        plan={plan}
        onPatch={onPatch}
        onSetPhysicalSize={onSetPhysicalSize}
      />

      {plan && (
        <>
          <UnstyledButton
            onClick={toggle}
            aria-expanded={sheetsOpen}
            aria-controls="sheet-list"
            fz="sm"
            fw={500}
          >
            {sheetsOpen ? '▾' : '▸'} {t('editor.sheetList.title', { count: plan.sheets.length })}
          </UnstyledButton>
          <Collapse in={sheetsOpen} id="sheet-list">
            <ul
              style={{
                margin: 0,
                paddingLeft: 0,
                listStyle: 'none',
                maxHeight: 220,
                overflow: 'auto',
              }}
            >
              {plan.sheets.map((s) => (
                <li key={s.id}>
                  <Button
                    size="compact-xs"
                    variant="subtle"
                    onClick={() => onOpenSheet(s.id)}
                    aria-label={t('editor.sheetList.open', { id: s.id })}
                  >
                    {s.id}
                    {' · '}
                    {s.print_index !== null
                      ? t('editor.sheetList.printIndex', { k: s.print_index })
                      : t(`editor.sheetState.${s.selected ? 'blank' : 'deselected'}`)}
                  </Button>
                </li>
              ))}
            </ul>
          </Collapse>
        </>
      )}
    </Stack>
  );
}
