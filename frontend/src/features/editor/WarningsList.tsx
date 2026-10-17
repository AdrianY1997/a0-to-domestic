import { Alert, Button, Group, Stack, Text } from '@mantine/core';
import { useTranslation } from 'react-i18next';
import type { Plan, PlanWarning } from '../../api/types';
import { actionPatch, warningAction, type WarningActionId } from './warnings';
import type { EditorSettings } from '../../lib/settings';
import { useFormat } from '../../lib/useFormat';
import { useDocEditor, useEditorStore } from '../../store/editor';

interface Props {
  documentId: string;
  warnings: PlanWarning[];
  plan: Plan | undefined;
  onPatch: (patch: Partial<EditorSettings>) => void;
  onSetPhysicalSize: () => void;
}

const COLORS = { info: 'blue', warning: 'yellow', error: 'red' } as const;

/** Warning list with a suggested action and "Ignore" (SPEC 8.2). */
export function WarningsList({ documentId, warnings, plan, onPatch, onSetPhysicalSize }: Props) {
  const { t } = useTranslation();
  const fmt = useFormat();
  const { ignoredWarnings } = useDocEditor(documentId);
  const visible = warnings.filter((w) => !ignoredWarnings.includes(w.code));

  const run = (action: WarningActionId) => {
    if (action === 'setPhysicalSize') return onSetPhysicalSize();
    const patch = actionPatch(action, plan);
    if (patch) onPatch(patch);
  };

  if (visible.length === 0) {
    return (
      <Text size="xs" c="dimmed">
        {t('editor.warnings.none')}
      </Text>
    );
  }

  return (
    <Stack gap="xs" role="list" aria-label={t('editor.warnings.title', { count: visible.length })}>
      {visible.map((w) => {
        const action = warningAction(w.code);
        const canRun = action !== null && (action !== 'reduceScale' || actionPatch(action, plan));
        const params = {
          dpi: plan?.effective_dpi ? fmt.number(plan.effective_dpi, 0) : '',
          ...(w.params ?? {}),
        };
        return (
          <Alert
            key={w.code}
            role="listitem"
            data-testid={`warning-${w.code}`}
            color={COLORS[w.severity]}
            variant="light"
            p="xs"
            title={t(`warnings.${w.code}.title`)}
          >
            <Text size="xs">{t(`warnings.${w.code}.message`, params)}</Text>
            <Group gap="xs" mt={6}>
              {action && canRun && (
                <Button size="compact-xs" onClick={() => run(action)}>
                  {t(`warnings.action.${action}`)}
                </Button>
              )}
              <Button
                size="compact-xs"
                variant="default"
                onClick={() => useEditorStore.getState().ignoreWarning(documentId, w.code)}
              >
                {t('warnings.ignore')}
              </Button>
            </Group>
          </Alert>
        );
      })}
    </Stack>
  );
}
