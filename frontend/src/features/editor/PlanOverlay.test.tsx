import { fireEvent, render, screen } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import { describe, expect, it, vi } from 'vitest';
import i18n from '../../i18n';
import { samplePlan } from '../../test/fixtures';
import { PlanOverlay } from './PlanOverlay';

const toggles = { margins: true, overlaps: true, ids: true };

function renderOverlay(props: Partial<React.ComponentProps<typeof PlanOverlay>> = {}) {
  return render(
    <I18nextProvider i18n={i18n}>
      <PlanOverlay plan={samplePlan} toggles={toggles} {...props} />
    </I18nextProvider>,
  );
}

describe('PlanOverlay', () => {
  it('draws an SVG whose viewBox is in view-space mm and one group per sheet', async () => {
    await i18n.changeLanguage('es');
    renderOverlay();
    const svg = screen.getByTestId('plan-overlay');
    const [x, y, w, h] = svg.getAttribute('viewBox')!.split(' ').map(Number);
    expect(x).toBeLessThan(0);
    expect(x + w).toBeGreaterThan(410);
    expect(y).toBeLessThan(0);
    expect(y + h).toBeGreaterThan(200);
    expect(screen.getByTestId('sheet-A1')).toHaveAttribute('data-state', 'printed');
    expect(screen.getByTestId('sheet-A2')).toHaveAttribute('data-state', 'blank');
    const rect = screen.getByTestId('plan-page');
    expect(rect).toHaveAttribute('width', '400');
    expect(rect).toHaveAttribute('height', '200');
  });

  it('shows IDs and glue strips only when toggled on', () => {
    const { rerender } = renderOverlay();
    expect(screen.getAllByText(/^A[12]$/)).toHaveLength(2);
    expect(screen.getAllByTestId('overlap-strip')).toHaveLength(1);
    rerender(
      <I18nextProvider i18n={i18n}>
        <PlanOverlay plan={samplePlan} toggles={{ margins: false, overlaps: false, ids: false }} />
      </I18nextProvider>,
    );
    expect(screen.queryAllByText(/^A[12]$/)).toHaveLength(0);
    expect(screen.queryAllByTestId('overlap-strip')).toHaveLength(0);
  });

  it('places the preview image with the view transform', () => {
    renderOverlay({
      image: {
        url: '/p.webp',
        widthMm: 400,
        heightMm: 200,
        box: [0, 0, 400, 200],
        rotate: 90,
      },
    });
    const img = screen.getByTestId('plan-image');
    expect(img).toHaveAttribute('width', '400');
    expect(img.getAttribute('transform')).toBe('matrix(0 1 -1 0 200 0)');
  });

  it('opens the exact sheet preview on double click and Enter', () => {
    const onOpen = vi.fn();
    renderOverlay({ onOpenSheet: onOpen });
    fireEvent.doubleClick(screen.getByTestId('sheet-A1'));
    fireEvent.keyDown(screen.getByTestId('sheet-A2'), { key: 'Enter' });
    expect(onOpen.mock.calls).toEqual([['A1'], ['A2']]);
  });

  it('gives sheets an accessible name and keyboard focus', async () => {
    await i18n.changeLanguage('es');
    renderOverlay();
    const a2 = screen.getByRole('button', { name: /Hoja A2, vacía, omitida/ });
    expect(a2).toHaveAttribute('tabindex', '0');
  });

  it('clips the page image to the view rect', () => {
    renderOverlay({
      image: { url: '/x.webp', widthMm: 400, heightMm: 200, box: [0, 0, 400, 200], rotate: 0 },
    });
    const g = screen.getByTestId('plan-image-clip');
    expect(g.getAttribute('clip-path')).toBe('url(#plan-view-clip)');
    const clip = document.getElementById('plan-view-clip')!.querySelector('rect')!;
    expect(clip).toHaveAttribute('width', '400');
    expect(clip).toHaveAttribute('height', '200');
  });
});

describe('PlanOverlay interactions', () => {
  // Gives the SVG a client box equal to its viewBox, so 1 px = 1 view mm.
  function mockRect() {
    const svg = screen.getByTestId('plan-overlay');
    const [, , w, h] = svg.getAttribute('viewBox')!.split(' ').map(Number);
    svg.getBoundingClientRect = () =>
      ({ left: 0, top: 0, width: w, height: h, right: w, bottom: h, x: 0, y: 0 }) as DOMRect;
  }

  it('clicking a sheet toggles it', () => {
    const onToggleSheet = vi.fn();
    renderOverlay({ onToggleSheet });
    fireEvent.click(screen.getByTestId('sheet-A1'));
    expect(onToggleSheet).toHaveBeenCalledWith('A1');
  });

  it('the second click of a double-click opens the sheet without toggling again', () => {
    const onToggleSheet = vi.fn();
    const onOpenSheet = vi.fn();
    renderOverlay({ onToggleSheet, onOpenSheet });
    const el = screen.getByTestId('sheet-A1');
    fireEvent.click(el, { detail: 1 });
    fireEvent.click(el, { detail: 2 });
    fireEvent.doubleClick(el, { detail: 2 });
    expect(onToggleSheet).toHaveBeenCalledTimes(1);
    expect(onOpenSheet).toHaveBeenCalledWith('A1');
  });

  it('a press that travelled is not a click', () => {
    const onToggleSheet = vi.fn();
    renderOverlay({ onToggleSheet });
    const svg = screen.getByTestId('plan-overlay');
    fireEvent.pointerDown(svg, { clientX: 0, clientY: 0, button: 0 });
    fireEvent.pointerMove(svg, { clientX: 30, clientY: 0, buttons: 1 });
    fireEvent.click(screen.getByTestId('sheet-A1'));
    expect(onToggleSheet).not.toHaveBeenCalled();
  });

  it('dragging in the grid tool reports an offset clamped to offset_range_mm', () => {
    const onOffsetChange = vi.fn();
    renderOverlay({ tool: 'grid', onOffsetChange });
    mockRect();
    const svg = screen.getByTestId('plan-overlay');
    fireEvent.pointerDown(svg, { clientX: 200, clientY: 100, button: 0 });
    fireEvent.pointerMove(window, { clientX: 150, clientY: 130 }); // dx -50, dy +30
    fireEvent.pointerUp(window);
    // sample range: x [-10, 0], y [0, 0]
    expect(onOffsetChange).toHaveBeenCalledWith({ x: -10, y: 0 });
  });

  it('a small grid drag inside the range keeps the exact amount', () => {
    const onOffsetChange = vi.fn();
    renderOverlay({ tool: 'grid', onOffsetChange, offset: { x: 0, y: 0 } });
    mockRect();
    const svg = screen.getByTestId('plan-overlay');
    fireEvent.pointerDown(svg, { clientX: 200, clientY: 100, button: 0 });
    fireEvent.pointerMove(window, { clientX: 194, clientY: 100 });
    fireEvent.pointerUp(window);
    expect(onOffsetChange).toHaveBeenCalledWith({ x: -6, y: 0 });
  });

  it('dragging in the region tool reports a region in view mm', () => {
    const onRegionDrawn = vi.fn();
    renderOverlay({ tool: 'region', onRegionDrawn });
    mockRect();
    const svg = screen.getByTestId('plan-overlay');
    const [vx, vy] = svg.getAttribute('viewBox')!.split(' ').map(Number);
    // view point (100, 50) -> (200, 100)
    fireEvent.pointerDown(svg, { clientX: 100 - vx!, clientY: 50 - vy!, button: 0 });
    fireEvent.pointerMove(window, { clientX: 200 - vx!, clientY: 100 - vy! });
    fireEvent.pointerUp(window);
    expect(onRegionDrawn).toHaveBeenCalledTimes(1);
    const r = onRegionDrawn.mock.calls[0]![0];
    expect(r.x).toBeCloseTo(100, 1);
    expect(r.y).toBeCloseTo(50, 1);
    expect(r.width).toBeCloseTo(100, 1);
    expect(r.height).toBeCloseTo(50, 1);
  });
});
