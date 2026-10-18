/** Horizontally mirrors a PNG (the preview of a mirrored view); returns it unchanged on failure. */
export async function flipHorizontal(png: Blob): Promise<Blob> {
  try {
    const bitmap = await createImageBitmap(png);
    const canvas = document.createElement('canvas');
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    const ctx = canvas.getContext('2d');
    if (!ctx) return png;
    ctx.translate(bitmap.width, 0);
    ctx.scale(-1, 1);
    ctx.drawImage(bitmap, 0, 0);
    bitmap.close();
    return await new Promise<Blob>((resolve) =>
      canvas.toBlob((b) => resolve(b ?? png), 'image/png'),
    );
  } catch {
    return png;
  }
}
