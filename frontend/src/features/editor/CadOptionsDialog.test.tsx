import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadInfo } from '../../api/types';
import i18n from '../../i18n';
import { buildCadOptions, CadOptionsDialog } from './CadOptionsDialog';

const setCadOptions = vi.fn();
vi.mock('../../api/endpoints', () => ({
  setCadOptions: (...args: unknown[]) => setCadOptions(...args),
}));

const info: CadInfo = {
  layouts: [
    { name: 'Model', kind: 'model', width_mm: null, height_mm: null },
    { name: 'A0 sheet', kind: 'paper', width_mm: 1189, height_mm: 841 },
  ],
  layout: 'Model',
  units: 'mm',
  units_source: 'insunits',
  scale: 20,
  scale_auto: true,
  color: 'color',
};

function renderDialog(data: CadInfo | null = info, onClose = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MantineProvider>
        <I18nextProvider i18n={i18n}>
          <CadOptionsDialog documentId="doc1" info={data ?? undefined} opened onClose={onClose} />
        </I18nextProvider>
      </MantineProvider>
    </QueryClientProvider>,
  );
  return { qc, onClose };
}

describe('buildCadOptions', () => {
  const base = { layout: 'Model', units: 'auto', scale: '' as number | '', color: 'gray' as const };

  it('sends automatic units and scale as null', () => {
    expect(buildCadOptions({ ...base, isModel: true })).toEqual({
      layout: 'Model',
      units: null,
      scale: null,
      color: 'gray',
    });
  });

  it('keeps the chosen units and scale in model space', () => {
    expect(buildCadOptions({ ...base, units: 'in', scale: 50, isModel: true })).toEqual({
      layout: 'Model',
      units: 'in',
      scale: 50,
      color: 'gray',
    });
  });

  it('drops units and scale in a paper space layout', () => {
    expect(
      buildCadOptions({ ...base, layout: 'A0 sheet', units: 'in', scale: 50, isModel: false }),
    ).toEqual({ layout: 'A0 sheet', units: null, scale: null, color: 'gray' });
  });
});

describe('CadOptionsDialog', () => {
  beforeEach(async () => {
    setCadOptions.mockReset();
    await i18n.changeLanguage('en');
  });

  it('shows the layouts, the detected units and the automatic scale', () => {
    renderDialog();
    expect(screen.getByRole('dialog', { name: 'CAD drawing options' })).toBeInTheDocument();
    expect(screen.getByText('The file says: mm.')).toBeInTheDocument();
    expect(screen.getByText(/Automatic: 1:20/)).toBeInTheDocument();
  });

  it('applies the options and refreshes the document', async () => {
    setCadOptions.mockResolvedValue({ id: 'doc1', status: 'processing' });
    const { qc, onClose } = renderDialog();
    fireEvent.click(screen.getByText('Black'));
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(setCadOptions).toHaveBeenCalledTimes(1));
    expect(setCadOptions).toHaveBeenCalledWith('doc1', {
      layout: 'Model',
      units: null,
      scale: null,
      color: 'black',
    });
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(qc.getQueryData(['document', 'doc1'])).toEqual({ id: 'doc1', status: 'processing' });
  });

  it('hides units and scale for a paper space layout', () => {
    renderDialog({ ...info, layout: 'A0 sheet', scale: null, scale_auto: false });
    expect(screen.queryByText('Drawing units (model space)')).toBeNull();
    expect(screen.getByText(/keeps the page size and scale/)).toBeInTheDocument();
  });

  it('cannot be applied before the drawing information has loaded', () => {
    renderDialog(null);
    expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled();
  });
});
