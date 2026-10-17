import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import { describe, expect, it, vi } from 'vitest';
import type { Plan } from '../../api/types';
import i18n from '../../i18n';
import { samplePlan } from '../../test/fixtures';
import { AlternativesPanel } from './AlternativesPanel';

const alt = (
  paper_id: string,
  orientation: 'portrait' | 'landscape',
  sheets: number,
  current = false,
) => ({ paper_id, orientation, cols: sheets, rows: 1, total: sheets, sheets, current });

function renderPanel(plan: Plan = samplePlan, open = true) {
  const onPatch = vi.fn();
  render(
    <MantineProvider>
      <I18nextProvider i18n={i18n}>
        <AlternativesPanel plan={plan} open={open} onToggle={() => {}} onPatch={onPatch} />
      </I18nextProvider>
    </MantineProvider>,
  );
  return onPatch;
}

describe('AlternativesPanel', () => {
  it('renders paper x orientation and applies a click', async () => {
    await i18n.changeLanguage('es');
    const plan: Plan = {
      ...samplePlan,
      alternatives: [
        alt('A4', 'landscape', 2, true),
        alt('A4', 'portrait', 3),
        alt('A3', 'landscape', 1),
      ],
    };
    const onPatch = renderPanel(plan);
    expect(screen.getByTestId('alternatives-table')).toBeInTheDocument();
    expect(screen.getByTestId('alt-A4-landscape')).toHaveAttribute('data-current', 'true');
    expect(screen.queryByTestId('alt-A3-portrait')).toBeNull();
    fireEvent.click(screen.getByTestId('alt-A3-landscape'));
    expect(onPatch).toHaveBeenCalledWith({ paper: 'A3', orientation: 'landscape' });
  });

  it('shows savings and applies the threshold percentage', async () => {
    await i18n.changeLanguage('es');
    const plan: Plan = {
      ...samplePlan,
      alternatives: [],
      savings: [
        {
          scale: 0.9156,
          scale_label: '91.56 %',
          orientation: 'portrait',
          cols: 4,
          rows: 4,
          sheets: 16,
          saved: 4,
        },
      ],
    };
    const onPatch = renderPanel(plan);
    fireEvent.click(screen.getByText(/91\.56 %: 16/));
    expect(onPatch).toHaveBeenCalledWith({
      scaleMode: 'percent',
      percent: 91.56,
      orientation: 'portrait',
    });
  });

  it('renders no savings block when the API sends none (exact scale)', async () => {
    await i18n.changeLanguage('es');
    renderPanel({ ...samplePlan, alternatives: [alt('A4', 'portrait', 1, true)], savings: null });
    expect(screen.queryByTestId('savings')).toBeNull();
  });

  it('shows only the toggle while closed', async () => {
    await i18n.changeLanguage('es');
    renderPanel(samplePlan, false);
    expect(screen.queryByTestId('alternatives-table')).toBeNull();
    expect(screen.getByTestId('alternatives-toggle')).toBeInTheDocument();
  });
});
