import { NumberInput } from '@mantine/core';
import { fromMm, toMm } from '../lib/units';
import { useAppStore } from '../store/app';

interface Props {
  label: string;
  /** Value in mm (the unit shown depends on the user's setting). */
  valueMm: number;
  onChangeMm: (mm: number) => void;
  minMm?: number;
  maxMm?: number;
  disabled?: boolean;
  description?: string;
}

const round = (n: number) => Number(n.toFixed(3));

/** Number input for a length: stored in mm, shown in the configured unit. */
export function LengthInput({
  label,
  valueMm,
  onChangeMm,
  minMm,
  maxMm,
  disabled,
  description,
}: Props) {
  const unit = useAppStore((s) => s.unit);
  return (
    <NumberInput
      label={label}
      description={description}
      size="xs"
      disabled={disabled}
      value={round(fromMm(valueMm, unit))}
      min={minMm === undefined ? undefined : round(fromMm(minMm, unit))}
      max={maxMm === undefined ? undefined : round(fromMm(maxMm, unit))}
      decimalScale={unit === 'mm' ? 1 : 3}
      rightSection={unit}
      rightSectionWidth={32}
      onChange={(v) => {
        if (typeof v === 'number') onChangeMm(toMm(v, unit));
      }}
    />
  );
}
