import { Group, NumberInput } from '@mantine/core';
import { useTranslation } from 'react-i18next';
import type { Margins } from '../../api/types';

const SIDES = ['top', 'right', 'bottom', 'left'] as const;

interface Props {
  value: Margins;
  onChange: (m: Margins) => void;
  /** Prefix for accessible names, e.g. the paper id. */
  label?: string;
}

/** Four margin inputs in mm (the physical margins of a printer, paper held in portrait). */
export function MarginsFields({ value, onChange, label = '' }: Props) {
  const { t } = useTranslation();
  return (
    <Group gap="xs" grow wrap="nowrap">
      {SIDES.map((side) => (
        <NumberInput
          key={side}
          size="xs"
          label={`${label} ${t(`printers.form.${side}`)}`.trim()}
          min={0}
          max={100}
          step={0.5}
          decimalScale={2}
          value={value[side] ?? 0}
          onChange={(v) => onChange({ ...value, [side]: typeof v === 'number' ? v : 0 })}
        />
      ))}
    </Group>
  );
}
