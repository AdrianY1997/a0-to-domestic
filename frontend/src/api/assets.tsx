import { Image, type ImageProps } from '@mantine/core';
import { useEffect, useState, type ComponentPropsWithoutRef, type MouseEvent } from 'react';
import { getTransport } from './transport';

/**
 * The browser-loadable URL of an API asset. Server mode returns it untouched; local mode asks the
 * worker for the bytes and returns a blob URL (`undefined` until it is ready).
 */
export function useAssetUrl(
  url: string | null | undefined,
  keepPrevious = false,
): string | undefined {
  const transport = getTransport();
  const [resolved, setResolved] = useState<{ src: string; url: string } | null>(null);
  const local = transport.kind === 'local';

  useEffect(() => {
    if (!local || !url) return;
    let live = true;
    getTransport()
      .resolveAsset(url)
      .then((asset) => {
        if (live) setResolved({ src: url, url: asset.url });
      })
      .catch(() => {
        if (live) setResolved(null);
      });
    return () => {
      live = false;
    };
  }, [local, url]);

  if (!local) return url ?? undefined;
  if (!resolved) return undefined;
  return keepPrevious || resolved.src === url ? resolved.url : undefined;
}

type AssetImageProps = Omit<ImageProps, 'src'> & {
  src: string;
  alt?: string;
  loading?: 'lazy' | 'eager';
};

/** `<Image>` for an API URL (thumbnails, previews). */
export function AssetImage({ src, ...rest }: AssetImageProps) {
  const resolved = useAssetUrl(src);
  if (!resolved) return <div style={{ height: rest.h as number | undefined }} aria-hidden />;
  return <Image src={resolved} {...rest} />;
}

function triggerDownload(url: string, filename: string) {
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/** Saves an API asset to disk (local mode) with the given or server-provided name. */
export async function saveAsset(url: string, filename?: string): Promise<void> {
  const asset = await getTransport().resolveAsset(url);
  triggerDownload(asset.url, filename || asset.filename || 'download');
}

/** Opens an API asset in a new tab (print preview, calibration sheet). */
export function openAsset(url: string): void {
  if (getTransport().kind === 'server') {
    window.open(url, '_blank', 'noopener');
    return;
  }
  // The tab is opened synchronously so the popup blocker accepts it, then pointed at the blob.
  const tab = window.open('', '_blank');
  void getTransport()
    .resolveAsset(url)
    .then((asset) => {
      if (tab) tab.location.href = asset.url;
      else window.open(asset.url, '_blank');
    })
    .catch(() => tab?.close());
}

/**
 * An `<a>` for API URLs. In local mode there is no server to follow the `href`, so a click
 * resolves the bytes in the worker and downloads (or opens) them from memory.
 */
export function AssetLink({
  href,
  download,
  target,
  onClick,
  ...rest
}: ComponentPropsWithoutRef<'a'>) {
  const handle = (e: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(e);
    if (e.defaultPrevented || !href || getTransport().kind !== 'local') return;
    e.preventDefault();
    if (download !== undefined && (download as unknown) !== false) {
      void saveAsset(href, typeof download === 'string' ? download : undefined);
    } else {
      openAsset(href);
    }
  };
  return <a href={href} download={download} target={target} onClick={handle} {...rest} />;
}
